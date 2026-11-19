"""Extractor behaviour: routing, retries, repairs, failures, review flag, cost and cache."""

import dataclasses
from decimal import Decimal

import pymupdf
import pytest

from backend.app.services.cost_tracker import BudgetGuard, ModelPricing, ResultCache, compute_cost
from backend.app.services.extractor import InvoiceExtractor
from backend.app.services.file_validation import DocumentError, sanitize_filename, validate_bytes
from backend.app.services.llm_client import LLMError
from backend.app.services.pdf_processor import load_document
from backend.tests.conftest import (
    NON_INVOICE_JSON,
    FakeLLM,
    invoice_json,
    make_digital_pdf,
    make_invoice_image,
    make_scanned_pdf,
    requires_tesseract,
)


def extractor(settings, *responses, **kw):
    llm = FakeLLM(*responses)
    return InvoiceExtractor(settings, llm_client=llm, **kw), llm


# --- file handling -----------------------------------------------------------------

def test_corrupted_pdf_is_a_failure_not_a_non_invoice(settings):
    ex, llm = extractor(settings)
    out = ex.extract_bytes(b"%PDF-1.7\n garbage \x00\x01 not really a pdf")
    assert not out.ok and out.diagnostics.error_code == "CORRUPTED_FILE"
    assert out.public_json()["is_invoice"] is None and out.public_json()["needs_review"] is True
    assert llm.calls == []


@pytest.mark.parametrize("data,code", [(b"", "EMPTY_FILE"), (b"hello world, plain text", "UNSUPPORTED_FILE_TYPE"),
                                       (b"GIF89a....", "UNSUPPORTED_FILE_TYPE")])
def test_unsupported_and_empty_files(settings, data, code):
    ex, _ = extractor(settings)
    assert ex.extract_bytes(data).diagnostics.error_code == code


def test_file_too_large(settings):
    with pytest.raises(DocumentError) as exc:
        validate_bytes(b"%PDF-" + b"0" * 100, max_bytes=50)
    assert exc.value.code == "FILE_TOO_LARGE"


def test_truncated_image_is_corrupted(settings):
    ex, _ = extractor(settings)
    png = make_invoice_image()
    assert ex.extract_bytes(png[: len(png) // 3]).diagnostics.error_code == "CORRUPTED_FILE"


def test_encrypted_pdf(settings):
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "secret")
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    ex, _ = extractor(settings)
    assert ex.extract_bytes(data).diagnostics.error_code == "ENCRYPTED_PDF"


def test_signature_detection_ignores_extension(settings):
    assert validate_bytes(make_invoice_image(fmt="JPEG"), 10**7).mime_type == "image/jpeg"
    assert validate_bytes(make_digital_pdf(), 10**7).kind == "pdf"


@pytest.mark.parametrize("name,expected", [("../../etc/passwd", "passwd"), ("C:\\x\\inv<1>.pdf", "inv_1_.pdf"), ("", "document"), ("...", "document")])
def test_sanitize_filename(name, expected):
    assert sanitize_filename(name) == expected


# --- routing --------------------------------------------------------------------------

def test_digital_pdf_routes_to_text_only(settings):
    ex, llm = extractor(settings, invoice_json())
    out = ex.extract_bytes(make_digital_pdf())
    assert out.ok and out.diagnostics.route == "digital_text"
    assert llm.calls[0]["images"] == 0 and llm.calls[0]["model"] == "test-model"
    assert "INV-2041" in llm.calls[0]["user"]


@requires_tesseract
def test_scanned_pdf_routes_to_vision_with_ocr_hint(settings):
    ex, llm = extractor(settings, invoice_json())
    out = ex.extract_bytes(make_scanned_pdf())
    assert out.diagnostics.route == "vision"
    assert llm.calls[0]["images"] == 1 and llm.calls[0]["model"] == "test-vision-model"
    assert "OCR / TEXT-LAYER HINT" in llm.calls[0]["user"]
    assert out.diagnostics.source.ocr_pages == 1 and out.diagnostics.source.ocr_mean_confidence > 50


@requires_tesseract
def test_ocr_first_uses_text_when_ocr_is_good(settings):
    s = dataclasses.replace(settings, routing_mode="ocr_first")
    ex, llm = extractor(s, invoice_json())
    out = ex.extract_bytes(make_invoice_image())
    assert out.diagnostics.route == "ocr_text" and llm.calls[0]["images"] == 0


def test_text_only_mode_fails_cleanly_without_text(settings):
    s = dataclasses.replace(settings, routing_mode="text_only", ocr_enabled=False)
    ex, llm = extractor(s)
    out = ex.extract_bytes(make_invoice_image())
    assert out.diagnostics.error_code == "NO_TEXT_EXTRACTED" and llm.calls == []


def test_vision_always(settings):
    s = dataclasses.replace(settings, routing_mode="vision_always")
    ex, llm = extractor(s, invoice_json())
    assert ex.extract_bytes(make_digital_pdf()).diagnostics.route == "vision"
    assert llm.calls[0]["images"] == 1


def test_digital_pages_detected(settings):
    content = load_document(make_digital_pdf(), validate_bytes(make_digital_pdf(), 10**7), settings)
    assert content.all_digital and not content.ocr_used


# --- non-invoices & review flag ----------------------------------------------------------

def test_non_invoice_document(settings):
    ex, _ = extractor(settings, NON_INVOICE_JSON)
    out = ex.extract_bytes(make_digital_pdf(["Dear partner,", "our office address is changing next month.", "Regards"] * 3))
    assert out.ok and out.invoice.is_invoice is False and out.invoice.needs_review is False
    assert out.public_json()["vendor"] is None


def test_clean_invoice_is_not_flagged(settings):
    ex, llm = extractor(settings, invoice_json())
    out = ex.extract_bytes(make_digital_pdf())
    assert out.invoice.needs_review is False and out.diagnostics.review_reasons == []
    assert len(llm.calls) == 1  # no retry needed


def test_borderline_document_type_is_flagged(settings):
    ex, _ = extractor(settings, invoice_json(document_type="proforma_invoice"))
    out = ex.extract_bytes(make_digital_pdf())
    assert "AMBIGUOUS_DOCUMENT_TYPE" in out.diagnostics.review_reasons and out.invoice.needs_review


def test_model_reported_illegible_text_is_flagged(settings):
    ex, _ = extractor(settings, invoice_json(issues=["ILLEGIBLE_TEXT"]))
    out = ex.extract_bytes(make_digital_pdf())
    assert "MODEL_REPORTED_ISSUE" in out.diagnostics.review_reasons


# --- retries -----------------------------------------------------------------------------------

def test_total_mismatch_triggers_one_vision_retry_that_fixes_it(settings):
    bad = invoice_json(total=3876.00)  # misread total, but it is not in the text either
    ex, llm = extractor(settings, bad, invoice_json())
    out = ex.extract_bytes(make_digital_pdf())
    assert len(llm.calls) == 2
    assert llm.calls[1]["images"] == 1  # escalated to page image
    assert "TOTAL_MISMATCH" in llm.calls[1]["user"]
    assert out.invoice.total == Decimal("3776.00") and out.invoice.needs_review is False
    assert [a.purpose for a in out.diagnostics.attempts] == ["initial", "validation_retry"]
    assert out.diagnostics.usage.retry_cost_usd > 0


def test_unresolved_mismatch_after_retry_is_flagged_with_conflict(settings):
    ex, llm = extractor(settings, invoice_json(total=3876.00), invoice_json(total=3976.00))
    out = ex.extract_bytes(make_digital_pdf())
    assert len(llm.calls) == 2  # exactly one validation retry
    assert out.invoice.needs_review
    assert "TOTAL_MISMATCH" in out.diagnostics.review_reasons
    assert "LLM_RESULT_CONFLICT" in out.diagnostics.review_reasons


def test_invalid_json_gets_one_repair_call(settings):
    ex, llm = extractor(settings, "this is not json", invoice_json())
    out = ex.extract_bytes(make_digital_pdf())
    assert out.ok and len(llm.calls) == 2
    assert out.diagnostics.validation["schema_valid_first_try"] is False
    assert out.diagnostics.attempts[1].purpose == "repair"


def test_invalid_json_twice_fails(settings):
    ex, _ = extractor(settings, "nope", '{"is_invoice": "maybe"}')
    out = ex.extract_bytes(make_digital_pdf())
    assert not out.ok and out.diagnostics.error_code == "LLM_INVALID_OUTPUT"


def test_markdown_fenced_json_is_accepted(settings):
    import json

    ex, llm = extractor(settings, "```json\n" + json.dumps(invoice_json()) + "\n```")
    assert ex.extract_bytes(make_digital_pdf()).ok and len(llm.calls) == 1


def test_llm_timeout_is_a_processing_failure(settings):
    ex, _ = extractor(settings, LLMError("LLM_TIMEOUT", "timed out", retryable=True))
    out = ex.extract_bytes(make_digital_pdf())
    assert not out.ok and out.diagnostics.error_code == "LLM_TIMEOUT"
    assert out.public_json()["is_invoice"] is None  # never reported as a non-invoice


def test_missing_api_key_is_reported(settings):
    s = dataclasses.replace(settings, llm_api_key="")
    out = InvoiceExtractor(s).extract_bytes(make_digital_pdf())
    assert out.diagnostics.error_code == "LLM_NOT_CONFIGURED"


def test_validation_retry_can_be_disabled(settings):
    s = dataclasses.replace(settings, validation_retry=False)
    ex, llm = extractor(s, invoice_json(total=3876.00))
    out = ex.extract_bytes(make_digital_pdf())
    assert len(llm.calls) == 1 and "TOTAL_MISMATCH" in out.diagnostics.review_reasons


# --- cost, cache, budget -------------------------------------------------------------------------

def test_cost_calculation_with_cached_tokens():
    pricing = ModelPricing(Decimal("0.40"), Decimal("0.10"), Decimal("1.60"))
    # 1000 prompt tokens of which 400 cached, 500 output
    cost = compute_cost(1000, 500, pricing, cached_input_tokens=400)
    assert cost == Decimal("0.00108000")  # 600*0.4e-6 + 400*0.1e-6 + 500*1.6e-6


def test_usage_and_cost_are_recorded_per_attempt(settings):
    ex, _ = extractor(settings, invoice_json())
    out = ex.extract_bytes(make_digital_pdf())
    u = out.diagnostics.usage
    assert (u.input_tokens, u.output_tokens, u.total_tokens) == (1000, 200, 1200)
    assert u.cost_usd == pytest.approx(1000 * 0.40e-6 + 200 * 1.60e-6)


def test_cache_hit_costs_nothing(settings, tmp_path):
    s = dataclasses.replace(settings, cache_enabled=True)
    cache = ResultCache(tmp_path / "cache")
    ex, llm = extractor(s, invoice_json(), cache=cache)
    pdf = make_digital_pdf()
    first = ex.extract_bytes(pdf)
    second = ex.extract_bytes(pdf)
    assert len(llm.calls) == 1
    assert second.diagnostics.cached and second.diagnostics.usage.cost_usd == 0
    assert second.diagnostics.original_cost_usd == pytest.approx(first.diagnostics.usage.cost_usd)
    assert second.public_json() == first.public_json()


def test_budget_guard_stops_llm_calls(settings):
    budget = BudgetGuard(Decimal("0"))
    ex, llm = extractor(settings, invoice_json(), budget=budget)
    out = ex.extract_bytes(make_digital_pdf())
    assert out.diagnostics.error_code == "BUDGET_EXCEEDED" and llm.calls == []


def test_missing_tesseract_is_not_a_review_reason(settings):
    """OCR that could not run is a setup issue: record it, but don't flag a correct extraction."""
    s = dataclasses.replace(settings, ocr_enabled=False)
    ex, llm = extractor(s, invoice_json())
    out = ex.extract_bytes(make_invoice_image())
    assert out.diagnostics.route == "vision" and llm.calls[0]["images"] == 1
    assert "LOW_OCR_QUALITY" not in out.diagnostics.review_reasons
    assert out.invoice.needs_review is False
    assert out.diagnostics.source.ocr_error == "OCR_DISABLED"
    assert any(i.code == "OCR_UNAVAILABLE" and i.severity == "info" for i in out.diagnostics.review_details)
