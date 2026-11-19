"""Multiple LLM providers: Grok (xAI) + OpenAI routing, fallback, usage and pricing."""

import dataclasses
import json
from decimal import Decimal

import httpx
import pytest

from backend.app.config import Settings, normalize_provider
from backend.app.services.cost_tracker import get_pricing
from backend.app.services.extractor import InvoiceExtractor
from backend.app.services.llm_client import LLMError, MultiProviderClient, billed_output_tokens
from backend.tests.conftest import invoice_json, make_digital_pdf


def ok_body(content: dict, prompt=1000, completion=200, reasoning=0, total=None) -> dict:
    usage = {"prompt_tokens": prompt, "completion_tokens": completion,
             "total_tokens": total if total is not None else prompt + completion}
    if reasoning:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    return {"choices": [{"message": {"content": json.dumps(content)}, "finish_reason": "stop"}], "usage": usage}


@pytest.fixture
def multi(settings):
    """Text -> Grok (xai), images -> OpenAI, fallback -> OpenAI."""
    return dataclasses.replace(
        settings,
        llm_provider="xai",
        llm_base_url="https://api.x.ai/v1",
        llm_api_key="xai-key",
        llm_model="grok-4-1-fast-reasoning",
        llm_vision_provider="openai",
        llm_vision_model="gpt-4.1-mini",
        llm_fallback_provider="openai",
        llm_fallback_model="gpt-4.1-mini",
        llm_fallback_vision_model="gpt-4.1-mini",
        provider_api_keys={"openai": "openai-key", "xai": "xai-key", "custom": ""},
        provider_base_urls={"openai": "https://api.openai.com/v1", "xai": "https://api.x.ai/v1", "custom": ""},
    )


def recorder(responses):
    seen = []

    def handler(request: httpx.Request):
        seen.append({"host": request.url.host, "auth": request.headers["authorization"], **json.loads(request.content)})
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return handler, seen


def test_grok_preset_from_env(monkeypatch, tmp_path):
    for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_VISION_PROVIDER", "LLM_VISION_MODEL", "LLM_FALLBACK_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "grok")
    monkeypatch.setenv("XAI_API_KEY", "xk")
    s = Settings.from_env()
    assert s.llm_provider == "xai" and s.llm_base_url == "https://api.x.ai/v1"
    assert s.llm_api_key == "xk" and s.llm_model == "grok-4-1-fast-reasoning"
    assert s.vision_provider == "xai" and s.llm_vision_model == "grok-4-1-fast-reasoning"
    assert s.llm_configured


def test_aliases():
    assert normalize_provider("Grok") == "xai" and normalize_provider("openrouter") == "custom"
    assert normalize_provider("groq") == "groq"  # Groq (GroqCloud) is a different company from Grok (xAI)


def test_groq_preset_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    s = Settings.from_env()
    assert s.llm_provider == "groq" and s.llm_base_url == "https://api.groq.com/openai/v1"
    assert s.llm_api_key == "gsk_test"
    assert s.llm_model == "openai/gpt-oss-120b" and s.llm_vision_model == "qwen/qwen3.8-27b"
    assert s.llm_configured


def test_groq_text_and_vision_models_are_called(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    s = dataclasses.replace(Settings.from_env(), cache_enabled=False)
    handler, seen = recorder([httpx.Response(200, json=ok_body(invoice_json(), prompt=1500, completion=300))])
    llm = MultiProviderClient(s, {"groq": httpx.MockTransport(handler)}, sleep=lambda _s: None)
    out = InvoiceExtractor(s, llm_client=llm).extract_bytes(make_digital_pdf())
    assert seen[0]["host"] == "api.groq.com" and seen[0]["auth"] == "Bearer gsk_test"
    assert seen[0]["model"] == "openai/gpt-oss-120b" and seen[0]["response_format"]["type"] == "json_schema"
    a = out.diagnostics.attempts[0]
    assert a.model == "groq:openai/gpt-oss-120b"
    assert a.cost_usd == pytest.approx(1500 * 0.15e-6 + 300 * 0.60e-6)


def test_text_goes_to_grok_and_images_go_to_openai(multi):
    xai_handler, xai_seen = recorder([httpx.Response(200, json=ok_body(invoice_json()))])
    oai_handler, oai_seen = recorder([httpx.Response(200, json=ok_body(invoice_json()))])
    client = MultiProviderClient(multi, {"xai": httpx.MockTransport(xai_handler), "openai": httpx.MockTransport(oai_handler)},
                                 sleep=lambda _s: None)
    r1 = client.complete("sys", "text only", [], {"type": "object"}, "grok-4-1-fast-reasoning")
    r2 = client.complete("sys", "with image", [b"\xff\xd8\xff"], {"type": "object"}, "gpt-4.1-mini")
    assert (r1.provider, r2.provider) == ("xai", "openai")
    assert xai_seen[0]["host"] == "api.x.ai" and xai_seen[0]["auth"] == "Bearer xai-key"
    assert xai_seen[0]["model"] == "grok-4-1-fast-reasoning"
    assert oai_seen[0]["host"] == "api.openai.com" and oai_seen[0]["auth"] == "Bearer openai-key"


def test_falls_back_to_openai_when_grok_is_down(multi):
    xai_handler, xai_seen = recorder([httpx.Response(503, json={"error": "overloaded"})] * 3)
    oai_handler, oai_seen = recorder([httpx.Response(200, json=ok_body(invoice_json()))])
    client = MultiProviderClient(multi, {"xai": httpx.MockTransport(xai_handler), "openai": httpx.MockTransport(oai_handler)},
                                 sleep=lambda _s: None)
    r = client.complete("sys", "text", [], {"type": "object"}, "grok-4-1-fast-reasoning")
    assert r.fallback_used and r.provider == "openai"
    assert len(xai_seen) == 3 and oai_seen[0]["model"] == "gpt-4.1-mini"
    assert [c.provider for c in r.calls] == ["xai", "xai", "xai", "openai"]


def test_both_providers_failing_raises_with_all_calls(multi):
    xai_handler, _ = recorder([httpx.Response(401, json={"error": "bad key"})])
    oai_handler, _ = recorder([httpx.Response(500, json={})] * 3)
    client = MultiProviderClient(multi, {"xai": httpx.MockTransport(xai_handler), "openai": httpx.MockTransport(oai_handler)},
                                 sleep=lambda _s: None)
    with pytest.raises(LLMError) as exc:
        client.complete("sys", "text", [], {"type": "object"}, "grok-4-1-fast-reasoning")
    assert exc.value.code == "LLM_SERVER_ERROR" and len(exc.value.calls) == 4
    assert "Fallback openai also failed" in exc.value.message


def test_empty_output_does_not_switch_provider(multi):
    empty = {"choices": [{"message": {"content": ""}}], "usage": {"prompt_tokens": 5, "completion_tokens": 0}}
    xai_handler, _ = recorder([httpx.Response(200, json=empty)])
    client = MultiProviderClient(multi, {"xai": httpx.MockTransport(xai_handler)}, sleep=lambda _s: None)
    with pytest.raises(LLMError) as exc:
        client.complete("sys", "text", [], {"type": "object"}, "grok-4-1-fast-reasoning")
    assert exc.value.code == "LLM_EMPTY_RESPONSE"  # handled by the extractor's repair call instead


def test_missing_primary_key_uses_fallback(multi):
    s = dataclasses.replace(multi, llm_api_key="", provider_api_keys={"openai": "openai-key", "xai": "", "custom": ""})
    assert s.llm_configured
    oai_handler, _ = recorder([httpx.Response(200, json=ok_body(invoice_json()))])
    client = MultiProviderClient(s, {"openai": httpx.MockTransport(oai_handler)}, sleep=lambda _s: None)
    assert client.complete("sys", "t", [], {}, "grok-4-1-fast-reasoning").provider == "openai"


def test_rejected_temperature_is_dropped_and_resent(multi):
    xai_handler, seen = recorder([
        httpx.Response(400, json={"error": {"message": "Argument not supported on this model: temperature"}}),
        httpx.Response(200, json=ok_body(invoice_json())),
    ])
    client = MultiProviderClient(multi, {"xai": httpx.MockTransport(xai_handler)}, sleep=lambda _s: None)
    client.complete("sys", "t", [], {}, "grok-4-1-fast-reasoning")
    assert "temperature" in seen[0] and "temperature" not in seen[1]


@pytest.mark.parametrize("usage,expected", [
    ({"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 686,
      "completion_tokens_details": {"reasoning_tokens": 556}}, (586, 556)),   # reported separately (xAI style)
    ({"prompt_tokens": 100, "completion_tokens": 586, "total_tokens": 686,
      "completion_tokens_details": {"reasoning_tokens": 556}}, (586, 556)),   # already included (OpenAI style)
    ({"prompt_tokens": 100, "completion_tokens": 30}, (30, 0)),
])
def test_reasoning_tokens_are_billed_exactly_once(usage, expected):
    assert billed_output_tokens(usage) == expected


def test_pricing_lookup(settings):
    grok = get_pricing("xai:grok-4-1-fast-reasoning", settings)
    assert (grok.input_per_1m, grok.cached_input_per_1m, grok.output_per_1m, grok.source) == (
        Decimal("0.20"), Decimal("0.05"), Decimal("0.50"), "built-in")
    s = dataclasses.replace(settings, model_pricing_overrides={"grok-4-1-fast-reasoning": {"input": 1, "output": 2}})
    assert get_pricing("grok-4-1-fast-reasoning", s).source == "override"
    assert get_pricing("some-new-model", settings).source == "generic"


def test_extractor_prices_each_call_by_the_model_that_served_it(multi):
    """Grok fails 3x (no tokens), OpenAI answers: cost must use gpt-4.1-mini rates only."""
    xai_handler, _ = recorder([httpx.Response(503, json={})] * 3)
    oai_handler, _ = recorder([httpx.Response(200, json=ok_body(invoice_json(), prompt=1000, completion=200))])
    llm = MultiProviderClient(multi, {"xai": httpx.MockTransport(xai_handler), "openai": httpx.MockTransport(oai_handler)},
                              sleep=lambda _s: None)
    out = InvoiceExtractor(multi, llm_client=llm).extract_bytes(make_digital_pdf())
    attempt = out.diagnostics.attempts[0]
    assert out.ok and attempt.model == "openai:gpt-4.1-mini" and attempt.outcome == "fallback"
    assert attempt.http_calls == 4
    assert attempt.cost_usd == pytest.approx(1000 * 0.40e-6 + 200 * 1.60e-6)


def test_extractor_grok_cost_with_reasoning_tokens(multi):
    body = ok_body(invoice_json(), prompt=2000, completion=150, reasoning=850, total=3000)
    xai_handler, _ = recorder([httpx.Response(200, json=body)])
    llm = MultiProviderClient(multi, {"xai": httpx.MockTransport(xai_handler)}, sleep=lambda _s: None)
    out = InvoiceExtractor(multi, llm_client=llm).extract_bytes(make_digital_pdf())
    a = out.diagnostics.attempts[0]
    assert a.model == "xai:grok-4-1-fast-reasoning" and a.output_tokens == 1000
    assert a.cost_usd == pytest.approx(2000 * 0.20e-6 + 1000 * 0.50e-6)


def test_groq_key_sent_to_grok_gives_clear_error(multi):
    s = dataclasses.replace(multi, llm_api_key="gsk_abc", llm_fallback_provider="")
    with pytest.raises(LLMError) as exc:
        MultiProviderClient(s).complete("sys", "t", [], {}, "grok-4-1-fast-reasoning")
    assert exc.value.code == "LLM_AUTH_ERROR" and "LLM_PROVIDER=groq" in exc.value.message


def test_xai_400_bad_key_is_an_auth_error(multi):
    body = {"code": "invalid-argument", "error": "Incorrect API key provided."}
    handler, seen = recorder([httpx.Response(400, json=body)])
    s = dataclasses.replace(multi, llm_fallback_provider="")
    with pytest.raises(LLMError) as exc:
        MultiProviderClient(s, {"xai": httpx.MockTransport(handler)}, sleep=lambda _s: None).complete("sys", "t", [], {}, "m")
    assert exc.value.code == "LLM_AUTH_ERROR" and len(seen) == 1


def test_groq_json_validate_failed_recovers_in_json_mode(monkeypatch, tmp_path):
    """Groq 400 json_validate_failed on the vision model -> resend once in JSON mode with the schema in the prompt."""
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    s = dataclasses.replace(Settings.from_env(), cache_enabled=False, ocr_enabled=False)
    failed = {"error": {"message": "Failed to generate JSON. Please adjust your prompt. See 'failed_generation' for more details.",
                        "type": "invalid_request_error", "code": "json_validate_failed", "failed_generation": "{..."}}
    handler, seen = recorder([
        httpx.Response(400, json=failed),
        httpx.Response(200, json=ok_body(invoice_json())),
        httpx.Response(200, json=ok_body(invoice_json())),
    ])
    llm = MultiProviderClient(s, {"groq": httpx.MockTransport(handler)}, sleep=lambda _s: None)
    from backend.tests.conftest import make_invoice_image

    out = InvoiceExtractor(s, llm_client=llm).extract_bytes(make_invoice_image())
    assert out.ok and out.invoice.total == Decimal("3776.00")
    assert seen[0]["response_format"]["type"] == "json_schema" and seen[0]["model"] == "qwen/qwen3.8-27b"
    assert seen[1]["response_format"]["type"] == "json_object"
    assert "matches this JSON Schema" in seen[1]["messages"][0]["content"]
    assert out.diagnostics.attempts[0].http_calls == 2
    # the text model on the same provider keeps strict schemas
    llm.complete("sys", "text", [], {"type": "object"}, "openai/gpt-oss-120b")
    assert seen[2]["response_format"]["type"] == "json_schema"
