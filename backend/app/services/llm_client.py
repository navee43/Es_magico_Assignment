"""LLM access for every supported provider (OpenAI, Grok/xAI, any OpenAI-compatible endpoint).

* ``OpenAICompatibleClient`` talks to ONE provider: timeouts, limited retries with
  backoff, structured-output negotiation, and *actual* token usage for every HTTP call.
* ``MultiProviderClient`` picks the provider per request (text route vs. page-image
  route) and, if that provider fails, retries the request once on a fallback provider.

Every ``HTTPCall`` records which provider and model served it, so cost is priced per call.
"""

from __future__ import annotations

import base64
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from backend.app.config import PROVIDERS, Settings, normalize_provider

logger = logging.getLogger(__name__)


class LLMError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, calls: list["HTTPCall"] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.calls = calls or []


@dataclass
class HTTPCall:
    ok: bool
    latency_ms: int
    status_code: int | None = None
    error: str | None = None
    input_tokens: int = 0  # prompt tokens, including cached and image tokens
    cached_input_tokens: int = 0
    output_tokens: int = 0  # billed output: completion + reasoning tokens
    reasoning_tokens: int = 0  # informational; already included in output_tokens
    provider: str | None = None
    model: str | None = None  # the model id that was requested (used for pricing)


@dataclass
class LLMResponse:
    content: str
    model: str
    finish_reason: str | None
    calls: list[HTTPCall] = field(default_factory=list)
    provider: str | None = None
    fallback_used: bool = False

    @property
    def input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def cached_input_tokens(self) -> int:
        return sum(c.cached_input_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def latency_ms(self) -> int:
        return sum(c.latency_ms for c in self.calls)


class LLMClient(Protocol):
    provider: str

    def complete(
        self,
        system: str,
        user_text: str,
        images: list[bytes],
        json_schema: dict[str, Any],
        model: str,
    ) -> LLMResponse: ...


def image_data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def billed_output_tokens(usage: dict[str, Any]) -> tuple[int, int]:
    """Return (billed_output_tokens, reasoning_tokens).

    OpenAI counts reasoning tokens inside ``completion_tokens``; some providers (xAI)
    report them separately but bill them at the output rate. ``total_tokens`` tells the
    two cases apart, so reasoning tokens are never dropped or double-counted.
    """
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    details = usage.get("completion_tokens_details") or {}
    reasoning = int(details.get("reasoning_tokens") or usage.get("reasoning_tokens") or 0)
    total = int(usage.get("total_tokens") or 0)
    if reasoning and total >= prompt + completion + reasoning:
        return completion + reasoning, reasoning
    return completion, reasoning


# Recognisable key prefixes. Used only to explain an obvious mix-up before any request is sent.
KEY_PREFIXES = {"gsk_": "groq", "xai-": "xai"}


def key_provider_mismatch(provider: str, api_key: str) -> str | None:
    """Return a helpful message if the key clearly belongs to another provider (e.g. a Groq key sent to Grok)."""
    for prefix, owner in KEY_PREFIXES.items():
        if api_key.startswith(prefix) and provider not in (owner, "custom"):
            return (
                f"The API key for provider '{provider}' starts with '{prefix}', which is a {owner} key. "
                f"Set LLM_PROVIDER={owner} (note: 'groq' = GroqCloud, 'grok' = xAI) and put the key in "
                f"{owner.upper()}_API_KEY, then restart the app."
            )
    return None


class OpenAICompatibleClient:
    """One provider endpoint. ``provider`` defaults to the primary text provider."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
        provider: str | None = None,
    ):
        self.provider = normalize_provider(provider or settings.llm_provider)
        base_url, api_key = settings.endpoint(self.provider)
        if not api_key:
            key_env = PROVIDERS.get(self.provider, {}).get("key_env", "LLM_API_KEY")
            raise LLMError("LLM_NOT_CONFIGURED", f"No API key for provider '{self.provider}' (set {key_env}).")
        if not base_url:
            raise LLMError("LLM_NOT_CONFIGURED", f"No base URL for provider '{self.provider}'.")
        mismatch = key_provider_mismatch(self.provider, api_key)
        if mismatch:
            raise LLMError("LLM_AUTH_ERROR", mismatch)
        self.settings = settings
        self._sleep = sleep
        # Request options that are adapted if a provider rejects them. The response format is tracked
        # per model: e.g. a vision model may fail strict schemas while the text model handles them fine.
        self._format_by_model: dict[str, str] = {}
        self._max_tokens_param = settings.llm_max_tokens_param
        self._send_temperature = settings.llm_temperature is not None
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(settings.llm_timeout_s, connect=15.0),
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def close(self) -> None:
        self._client.close()

    def response_format(self, model: str) -> str:
        return self._format_by_model.get(model, self.settings.llm_response_format)

    # ------------------------------------------------------------------
    def _payload(self, system: str, user_text: str, images: list[bytes], schema: dict, model: str) -> dict:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for img in images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_data_url(img), "detail": self.settings.llm_image_detail},
                }
            )
        fmt = self.response_format(model)
        if fmt != "json_schema":
            # Without server-side schema enforcement the model must see the exact schema.
            system = (system + "\n\nReturn a single JSON object that matches this JSON Schema exactly "
                      "(all keys present, null when unknown):\n" + json.dumps(schema, separators=(",", ":")))
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content if images else user_text},
            ],
            self._max_tokens_param: self.settings.llm_max_output_tokens,
        }
        if self._send_temperature:
            payload["temperature"] = self.settings.llm_temperature
        if fmt == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "invoice_extraction", "strict": True, "schema": schema},
            }
        elif fmt == "json_object":
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _adapt_to_rejection(self, body: str, model: str) -> bool:
        """A 400 that names an unsupported option or a failed JSON generation: adjust once and resend."""
        text = body.lower()
        fmt = self.response_format(model)
        generation_failed = "json_validate_failed" in text or "failed to generate json" in text
        if fmt == "json_schema" and (generation_failed or "response_format" in text or "json_schema" in text):
            # e.g. Groq rejects a reply that doesn't satisfy the strict schema; plain JSON mode + our own
            # Pydantic validation and repair call handle it instead.
            logger.info("%s/%s: strict schema failed; retrying in JSON mode", self.provider, model)
            self._format_by_model[model] = "json_object"
            return True
        if fmt == "json_object" and generation_failed:
            logger.info("%s/%s: JSON mode failed; retrying without response_format", self.provider, model)
            self._format_by_model[model] = "none"
            return True
        if self._send_temperature and "temperature" in text:
            logger.info("%s rejected temperature; omitting it", self.provider)
            self._send_temperature = False
            return True
        if self._max_tokens_param in text and "max" in text:
            other = "max_completion_tokens" if self._max_tokens_param == "max_tokens" else "max_tokens"
            logger.info("%s rejected %s; using %s", self.provider, self._max_tokens_param, other)
            self._max_tokens_param = other
            return True
        return False

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        delay = min(20.0, 1.5 * (2 ** (attempt - 1))) + random.uniform(0, 0.5)
        if retry_after:
            try:
                delay = min(20.0, max(delay, float(retry_after)))
            except ValueError:
                pass
        self._sleep(delay)

    def _failed(self, latency: int, model: str, status: int | None = None, error: str | None = None) -> HTTPCall:
        return HTTPCall(False, latency, status, error=error, provider=self.provider, model=model)

    def complete(self, system, user_text, images, json_schema, model) -> LLMResponse:
        calls: list[HTTPCall] = []
        max_attempts = self.settings.llm_max_api_retries + 1
        attempt = 0
        adaptations = 0
        while True:
            attempt += 1
            payload = self._payload(system, user_text, images, json_schema, model)
            started = time.perf_counter()
            try:
                response = self._client.post("/chat/completions", json=payload)
            except httpx.TimeoutException:
                calls.append(self._failed(_ms(started), model, error="timeout"))
                error = LLMError(
                    "LLM_TIMEOUT",
                    f"{self.provider} request timed out after {self.settings.llm_timeout_s:.0f}s.",
                    True,
                )
            except httpx.HTTPError as exc:
                calls.append(self._failed(_ms(started), model, error=type(exc).__name__))
                error = LLMError("LLM_CONNECTION_ERROR", f"Could not reach {self.provider} ({type(exc).__name__}).", True)
            else:
                latency = _ms(started)
                if response.status_code == 200:
                    try:
                        return self._parse_success(response, calls, latency, model)
                    except LLMError as exc:
                        exc.calls = calls
                        raise
                body = _safe_text(response)
                calls.append(self._failed(latency, model, response.status_code, body[:200]))
                status = response.status_code
                if status in (400, 422) and adaptations < 4 and self._adapt_to_rejection(body, model):
                    adaptations += 1
                    attempt -= 1
                    continue
                if status in (401, 403) or (status == 400 and "api key" in body.lower()):
                    raise LLMError("LLM_AUTH_ERROR", f"{self.provider} rejected the API key.", False, calls)
                if status == 429:
                    error = LLMError("LLM_RATE_LIMITED", f"{self.provider} rate limit or quota was hit.", True)
                elif status >= 500:
                    error = LLMError("LLM_SERVER_ERROR", f"{self.provider} returned HTTP {status}.", True)
                else:
                    raise LLMError(
                        "LLM_BAD_REQUEST", f"{self.provider} returned HTTP {status}: {body[:200]}", False, calls
                    )
                if attempt < max_attempts:
                    self._backoff(attempt, response.headers.get("retry-after"))
                    continue
                error.calls = calls
                raise error
            if attempt < max_attempts:
                self._backoff(attempt, None)
                continue
            error.calls = calls
            raise error

    def _parse_success(self, response: httpx.Response, calls: list[HTTPCall], latency: int, model: str) -> LLMResponse:
        try:
            body = response.json()
            choice = body["choices"][0]
            message = choice.get("message") or {}
        except (ValueError, KeyError, IndexError, TypeError):
            calls.append(self._failed(latency, model, 200, "malformed response"))
            raise LLMError("LLM_MALFORMED_RESPONSE", f"{self.provider} returned an unexpected response body.")
        usage = body.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        output, reasoning = billed_output_tokens(usage)
        calls.append(
            HTTPCall(
                True,
                latency,
                200,
                input_tokens=int(usage.get("prompt_tokens") or 0),
                cached_input_tokens=int(details.get("cached_tokens") or 0),
                output_tokens=output,
                reasoning_tokens=reasoning,
                provider=self.provider,
                model=model,
            )
        )
        content = message.get("content")
        if isinstance(content, list):  # some providers return content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not content:
            if message.get("refusal"):
                raise LLMError("LLM_REFUSAL", "The model refused to process the document.")
            raise LLMError("LLM_EMPTY_RESPONSE", "The model returned an empty response.")
        return LLMResponse(content, body.get("model") or model, choice.get("finish_reason"), calls, self.provider)


# Failures where trying a different provider can help. Invalid/empty model output is not here:
# the extractor handles that with a repair call on the same provider.
FALLBACK_CODES = {
    "LLM_NOT_CONFIGURED",
    "LLM_TIMEOUT",
    "LLM_CONNECTION_ERROR",
    "LLM_RATE_LIMITED",
    "LLM_SERVER_ERROR",
    "LLM_AUTH_ERROR",
    "LLM_BAD_REQUEST",
}


class MultiProviderClient:
    """Routes each request to the right provider and falls back to another provider on failure.

    * Requests with page images go to ``settings.vision_provider``; text-only requests go to
      ``settings.llm_provider``. The extractor already chose the matching model.
    * If that provider fails (after its own HTTP retries) and ``LLM_FALLBACK_PROVIDER`` is set,
      the same request is sent once to the fallback provider with the fallback model.
    """

    provider = "multi"

    def __init__(self, settings: Settings, transports: dict[str, httpx.BaseTransport] | None = None, sleep=time.sleep):
        self.settings = settings
        self._transports = transports or {}
        self._sleep = sleep
        self._clients: dict[str, OpenAICompatibleClient] = {}

    def client_for(self, provider: str) -> OpenAICompatibleClient:
        provider = normalize_provider(provider)
        if provider not in self._clients:
            self._clients[provider] = OpenAICompatibleClient(
                self.settings, transport=self._transports.get(provider), sleep=self._sleep, provider=provider
            )
        return self._clients[provider]

    def complete(self, system, user_text, images, json_schema, model) -> LLMResponse:
        s = self.settings
        primary = s.vision_provider if images else s.llm_provider
        try:
            return self.client_for(primary).complete(system, user_text, images, json_schema, model)
        except LLMError as exc:
            fallback = s.llm_fallback_provider
            if not fallback or exc.code not in FALLBACK_CODES:
                raise
            fallback_model = (s.llm_fallback_vision_model if images else s.llm_fallback_model) or model
            if fallback == primary and fallback_model == model:
                raise
            logger.warning("%s failed (%s); falling back to %s/%s", primary, exc.code, fallback, fallback_model)
            try:
                response = self.client_for(fallback).complete(system, user_text, images, json_schema, fallback_model)
            except LLMError as exc2:
                exc2.calls = exc.calls + exc2.calls
                exc2.message = f"{exc.message} Fallback {fallback} also failed: {exc2.message}"
                raise exc2
            response.calls = exc.calls + response.calls
            response.fallback_used = True
            return response

    def close(self) -> None:
        for client in self._clients.values():
            client.close()


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _safe_text(response: httpx.Response) -> str:
    try:
        return json.dumps(response.json())
    except ValueError:
        return response.text or ""


def build_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "mock":
        from backend.app.services.mock_llm import MockLLMClient

        return MockLLMClient()
    return MultiProviderClient(settings)
