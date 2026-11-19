from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.app.schemas.invoice import InvoiceResult
from backend.app.services.normalization import normalize_currency, normalize_date, parse_number
from backend.app.services.validator import (
    OutputSchemaError,
    check_financials,
    check_grounding,
    check_required_fields,
    normalize_llm_output,
)
from backend.tests.conftest import NON_INVOICE_JSON, invoice_json


# --- schema ------------------------------------------------------------------

def test_correct_schema_serialises_to_assignment_shape(settings):
    out = normalize_llm_output(invoice_json(), settings)
    data = out.invoice.model_dump(mode="json")
    assert list(data) == ["is_invoice", "vendor", "invoice_number", "invoice_date", "currency", "line_items",
                          "tax_total", "total", "needs_review"]
    assert data["invoice_date"] == "2026-03-14"
    assert data["line_items"] == [{"description": "A4 paper, 500 sheets", "quantity": 10, "amount": 3200.0}]
    assert data["total"] == 3776.0 and isinstance(data["total"], float)


def test_non_invoice_has_all_null_fields(settings):
    out = normalize_llm_output(NON_INVOICE_JSON, settings)
    assert out.invoice.model_dump(mode="json") == {
        "is_invoice": False, "vendor": None, "invoice_number": None, "invoice_date": None, "currency": None,
        "line_items": None, "tax_total": None, "total": None, "needs_review": False,
    }


def test_non_invoice_with_values_is_rejected_by_schema():
    with pytest.raises(ValidationError):
        InvoiceResult(is_invoice=False, vendor="Acme")


def test_non_invoice_that_still_has_invoice_data_is_flagged(settings):
    raw = dict(NON_INVOICE_JSON, invoice_number="X-1", total=10)
    out = normalize_llm_output(raw, settings)
    assert out.invoice.vendor is None and out.invoice.total is None
    assert any(c.code == "AMBIGUOUS_DOCUMENT_TYPE" for c in out.checks)


def test_schema_rejects_non_iso_date_string():
    with pytest.raises(ValidationError):
        InvoiceResult(is_invoice=True, invoice_date="14/03/2026")


def test_schema_rejects_unknown_currency():
    with pytest.raises(ValidationError):
        InvoiceResult(is_invoice=True, currency="XYZ")


def test_invalid_date_from_model_becomes_null_and_flag(settings):
    out = normalize_llm_output(invoice_json(invoice_date="2026-02-30", invoice_date_as_printed=None), settings)
    assert out.invoice.invoice_date is None
    assert any(c.code == "INVALID_DATE" for c in out.checks)


def test_non_iso_date_from_model_is_normalised(settings):
    out = normalize_llm_output(invoice_json(invoice_date="14 Mar 2026", invoice_date_as_printed="14 Mar 2026"), settings)
    assert out.invoice.invoice_date.isoformat() == "2026-03-14"


def test_date_inconsistent_with_printed_date_is_flagged(settings):
    out = normalize_llm_output(invoice_json(invoice_date="2026-05-14", invoice_date_as_printed="14/03/2026"), settings)
    assert any(c.code == "DATE_INCONSISTENT" for c in out.checks)


def test_missing_is_invoice_is_a_schema_error(settings):
    raw = invoice_json()
    del raw["is_invoice"]
    with pytest.raises(OutputSchemaError):
        normalize_llm_output(raw, settings)


def test_number_strings_and_currency_symbols_are_normalised(settings):
    out = normalize_llm_output(invoice_json(total="₹ 3,776.00", tax_total="576", currency="₹"), settings)
    assert out.invoice.total == Decimal("3776.00") and out.invoice.currency == "INR"


@pytest.mark.parametrize("text,expected", [("1,23,456.50", "123456.50"), ("3.776,00", "3776.00"), ("(12.00)", "-12.00"), ("abc", None)])
def test_parse_number(text, expected):
    assert parse_number(text) == (Decimal(expected) if expected else None)


def test_normalize_date_ambiguity():
    assert normalize_date("03/04/2026", day_first=True) == ("2026-04-03", True)
    assert normalize_date("14/03/2026", day_first=True) == ("2026-03-14", False)


def test_normalize_currency():
    assert normalize_currency("rs.") == "INR" and normalize_currency("eur") == "EUR" and normalize_currency("??") is None


# --- financial validation ---------------------------------------------------------

def _inv(settings, **kw):
    return normalize_llm_output(invoice_json(**kw), settings)


def test_valid_totals_pass(settings):
    out = _inv(settings)
    report, checks = check_financials(out.invoice, out.extras, settings)
    assert report["status"] == "pass" and checks == []


def test_incorrect_totals_flagged(settings):
    out = _inv(settings, total=3900.00)
    report, checks = check_financials(out.invoice, out.extras, settings)
    assert report["status"] == "mismatch"
    assert [c.code for c in checks] == ["TOTAL_MISMATCH"] and checks[0].retryable


def test_rounding_within_tolerance_passes(settings):
    items = [{"description": f"item {i}", "quantity": 1, "unit_price": 33.33, "amount": 33.33} for i in range(3)]
    out = _inv(settings, line_items=items, tax_total=0, total=100.00)  # 99.99 vs 100.00
    report, _ = check_financials(out.invoice, out.extras, settings)
    assert report["status"] == "pass"


def test_rounding_beyond_tolerance_fails(settings):
    out = _inv(settings, total=3776.50)
    report, _ = check_financials(out.invoice, out.extras, settings)
    assert report["status"] == "mismatch"


def test_discount_explains_difference_without_changing_numbers(settings):
    out = _inv(settings, discount_total=200, total=3576.00)
    report, checks = check_financials(out.invoice, out.extras, settings)
    assert report["status"] == "pass_with_adjustments"
    assert out.invoice.total == Decimal("3576.00")  # extracted value untouched
    assert all(c.severity == "info" for c in checks)


def test_tax_inclusive_line_amounts_are_explained(settings):
    items = [{"description": "Item", "quantity": 1, "unit_price": 3776, "amount": 3776.00}]
    out = _inv(settings, line_items=items, tax_total=576.00, total=3776.00)
    report, _ = check_financials(out.invoice, out.extras, settings)
    assert report["status"] == "pass_with_adjustments"


def test_missing_tax_is_treated_as_zero_for_math(settings):
    out = _inv(settings, tax_total=None, total=3200.00)
    report, _ = check_financials(out.invoice, out.extras, settings)
    assert report["status"] == "pass"


def test_missing_line_amount_gives_insufficient_data(settings):
    out = _inv(settings, line_items=[{"description": "x", "quantity": 1, "unit_price": None, "amount": None}])
    report, _ = check_financials(out.invoice, out.extras, settings)
    assert report["status"] == "insufficient_data"


def test_missing_fields_are_reported(settings):
    out = _inv(settings, vendor=None, invoice_date=None, invoice_date_as_printed=None, line_items=None, tax_total=None)
    codes = {c.code for c in check_required_fields(out.invoice)}
    assert {"MISSING_VENDOR", "MISSING_INVOICE_DATE", "MISSING_LINE_ITEMS", "TAX_NOT_FOUND"} <= codes


# --- grounding ---------------------------------------------------------------------------

def test_grounding_detects_invented_invoice_number_on_digital_text(settings):
    out = _inv(settings, invoice_number="INV-9999")
    text = "Acme Supplies Pvt Ltd\nInvoice No: INV-2041\nGrand Total 3,776.00"
    result, checks = check_grounding(out.invoice, text, "digital_text")
    assert result["invoice_number_found"] is False
    assert any(c.code == "UNGROUNDED_INVOICE_NUMBER" and c.severity == "review" for c in checks)


def test_grounding_passes_when_values_present(settings):
    out = _inv(settings)
    text = "Acme Supplies Pvt Ltd\nInvoice No: INV-2041\nGrand Total Rs. 3,776.00"
    _, checks = check_grounding(out.invoice, text, "digital_text")
    assert checks == []


def test_grounding_is_info_only_for_ocr_routes(settings):
    out = _inv(settings, invoice_number="INV-9999")
    _, checks = check_grounding(out.invoice, "Invoice No: INV-2041 Total 3776.00", "vision")
    assert all(c.severity == "info" for c in checks)
