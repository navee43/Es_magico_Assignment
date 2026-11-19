"""HTTP-level behaviour of the OpenAI-compatible client, using httpx.MockTransport."""

import dataclasses
import json

import httpx
import pytest

from backend.app.services.llm_client import LLMError, OpenAICompatibleClient

OK_BODY = {
    "model": "test-model-2026",
    "choices": [{"message": {"content": '{"is_invoice": false}'}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1200, "completion_tokens": 80, "prompt_tokens_details": {"cached_tokens": 1024}},
}


def client(settings, handler, **overrides):
    s = dataclasses.replace(settings, **overrides)
    return OpenAICompatibleClient(s, transport=httpx.MockTransport(handler), sleep=lambda _s: None)


def call(c, images=()):
    return c.complete("system", "user", list(images), {"type": "object"}, "test-model")


def test_success_returns_actual_usage(settings):
    seen = {}

    def handler(request: httpx.Request):
        seen.update(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json=OK_BODY)

    r = call(client(settings, handler), images=[b"\xff\xd8\xff fake jpeg"])
    assert r.content == '{"is_invoice": false}' and r.model == "test-model-2026"
    assert (r.input_tokens, r.cached_input_tokens, r.output_tokens) == (1200, 1024, 80)
    assert seen["response_format"]["type"] == "json_schema" and seen["temperature"] == 0
    assert seen["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_timeout_is_retried_then_succeeds(settings):
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=OK_BODY)

    r = call(client(settings, handler))
    assert attempts["n"] == 2 and len(r.calls) == 2 and not r.calls[0].ok


def test_timeouts_exhaust_limited_retries(settings):
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMError) as exc:
        call(client(settings, handler, llm_max_api_retries=2))
    assert exc.value.code == "LLM_TIMEOUT" and attempts["n"] == 3 and len(exc.value.calls) == 3


def test_rate_limit_retried(settings):
    responses = [httpx.Response(429, json={"error": "slow down"}, headers={"retry-after": "1"}), httpx.Response(200, json=OK_BODY)]
    r = call(client(settings, lambda req: responses.pop(0)))
    assert r.calls[0].status_code == 429 and r.calls[1].ok


def test_auth_error_is_not_retried(settings):
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(401, json={"error": "bad key"})

    with pytest.raises(LLMError) as exc:
        call(client(settings, handler))
    assert exc.value.code == "LLM_AUTH_ERROR" and attempts["n"] == 1


def test_falls_back_to_json_object_when_schema_unsupported(settings):
    formats = []

    def handler(request):
        body = json.loads(request.content)
        formats.append(body["response_format"]["type"])
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": {"message": "response_format json_schema not supported"}})
        return httpx.Response(200, json=OK_BODY)

    call(client(settings, handler))
    assert formats == ["json_schema", "json_object"]


def test_empty_content_raises_specific_error(settings):
    body = {"choices": [{"message": {"content": None}}], "usage": {"prompt_tokens": 5, "completion_tokens": 0}}
    with pytest.raises(LLMError) as exc:
        call(client(settings, lambda r: httpx.Response(200, json=body)))
    assert exc.value.code == "LLM_EMPTY_RESPONSE"
