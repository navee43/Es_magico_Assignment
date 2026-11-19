"""Shared fixtures. All tests use a fake LLM: no network, no spending."""

from __future__ import annotations

import dataclasses
import io
import json
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from backend.app.config import Settings
from backend.app.services.llm_client import HTTPCall, LLMError, LLMResponse
from backend.app.services.ocr import tesseract_available

requires_tesseract = pytest.mark.skipif(not tesseract_available(), reason="tesseract not installed")


@pytest.fixture(autouse=True)
def _isolate_from_local_env(monkeypatch):
    """Never read the developer's .env or real API keys during tests."""
    monkeypatch.setenv("INVOICE_AI_SKIP_DOTENV", "1")
    for key in ("LLM_API_KEY", "LLM_BASE_URL", "OPENAI_API_KEY", "XAI_API_KEY", "GROQ_API_KEY", "CUSTOM_LLM_API_KEY",
                "LLM_PROVIDER", "LLM_MODEL", "LLM_VISION_PROVIDER", "LLM_VISION_MODEL", "LLM_FALLBACK_PROVIDER",
                "LLM_FALLBACK_MODEL", "LLM_FALLBACK_VISION_MODEL", "MODEL_PRICING_JSON", "ROUTING_MODE"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch) -> Settings:
    for key in ("LLM_API_KEY", "OPENAI_API_KEY", "APP_PASSWORD", "SECRET_KEY", "LLM_PROVIDER", "ROUTING_MODE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path / "no-frontend"))
    base = Settings.from_env()
    return dataclasses.replace(
        base,
        llm_api_key="test-key",
        llm_provider="openai",
        llm_model="test-model",
        llm_vision_model="test-vision-model",
        cache_enabled=False,
        app_password="pw",
        secret_key="secret",
        auth_enabled=True,
    )


class FakeLLM:
    """Returns queued responses in order. Each item: dict (JSON), str (raw text) or LLMError."""

    provider = "fake"

    def __init__(self, *responses: Any, input_tokens: int = 1000, output_tokens: int = 200):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def complete(self, system, user_text, images, json_schema, model) -> LLMResponse:
        self.calls.append({"user": user_text, "images": len(images), "model": model})
        if not self.responses:
            raise AssertionError("FakeLLM ran out of responses")
        item = self.responses.pop(0)
        if isinstance(item, LLMError):
            item.calls = [HTTPCall(False, 10, 500, error=item.code)]
            raise item
        content = item if isinstance(item, str) else json.dumps(item)
        return LLMResponse(content, model, "stop", [HTTPCall(True, 25, 200, input_tokens=self.input_tokens, output_tokens=self.output_tokens)])


def invoice_json(**overrides: Any) -> dict[str, Any]:
    data = {
        "document_type": "invoice",
        "is_invoice": True,
        "vendor": "Acme Supplies Pvt Ltd",
        "invoice_number": "INV-2041",
        "invoice_date": "2026-03-14",
        "invoice_date_as_printed": "14/03/2026",
        "currency": "INR",
        "line_items": [{"description": "A4 paper, 500 sheets", "quantity": 10, "unit_price": 320, "amount": 3200.00}],
        "subtotal": 3200.00,
        "discount_total": None,
        "other_charges_total": None,
        "tax_total": 576.00,
        "total": 3776.00,
        "issues": [],
    }
    data.update(overrides)
    return data


NON_INVOICE_JSON = {
    "document_type": "other", "is_invoice": False, "vendor": None, "invoice_number": None, "invoice_date": None,
    "invoice_date_as_printed": None, "currency": None, "line_items": None, "subtotal": None, "discount_total": None,
    "other_charges_total": None, "tax_total": None, "total": None, "issues": [],
}

INVOICE_TEXT_LINES = [
    "Acme Supplies Pvt Ltd",
    "TAX INVOICE",
    "Invoice No: INV-2041",
    "Invoice Date: 14/03/2026",
    "Description            Qty   Rate     Amount",
    "A4 paper, 500 sheets    10   320.00   3,200.00",
    "Subtotal: 3,200.00",
    "GST 18%: 576.00",
    "Grand Total: Rs. 3,776.00",
]


def make_digital_pdf(lines: list[str] = INVOICE_TEXT_LINES) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for i, line in enumerate(lines):
        page.insert_text((50, 60 + i * 16), line, fontname="helv", fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def make_invoice_image(lines: list[str] = INVOICE_TEXT_LINES, fmt: str = "PNG") -> bytes:
    img = Image.new("RGB", (1240, 900), "white")
    draw = ImageDraw.Draw(img)
    font = None
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        if Path(path).is_file():
            font = ImageFont.truetype(path, 30)
            break
    font = font or ImageFont.load_default(size=30)
    for i, line in enumerate(lines):
        draw.text((60, 60 + i * 50), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def make_scanned_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=make_invoice_image(fmt="JPEG"))
    data = doc.tobytes()
    doc.close()
    return data
