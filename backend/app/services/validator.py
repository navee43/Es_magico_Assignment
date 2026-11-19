"""Schema validation, normalisation and financial / grounding checks.

Checks never modify extracted numbers. They only *report* problems; the extractor
decides whether a retry could help and the review module decides on needs_review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from backend.app.config import Settings
from backend.app.schemas.invoice import InvoiceResult, LineItem
from backend.app.services.normalization import (
    normalize_currency,
    normalize_date,
    normalize_whitespace,
    parse_number,
)


@dataclass
class Check:
    code: str
    message: str
    severity: str = "review"  # "review" -> sets needs_review; "info" -> recorded only
    retryable: bool = False  # would a second extraction attempt plausibly fix it?


class OutputSchemaError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# ---------------------------------------------------------------------------
# 1. Raw model output -> validated InvoiceResult
# ---------------------------------------------------------------------------

NumberLike = float | int | str | None


class RawLineItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    description: str | None = None
    quantity: NumberLike = None
    unit_price: NumberLike = None
    amount: NumberLike = None


class RawLLMOutput(BaseModel):
    """Structure the model was asked to return (lenient on number formatting)."""

    model_config = ConfigDict(extra="ignore")
    document_type: str | None = None
    is_invoice: bool
    vendor: str | None = None
    invoice_number: str | int | None = None
    invoice_date: str | None = None
    invoice_date_as_printed: str | None = None
    currency: str | None = None
    line_items: list[RawLineItem] | None = None
    subtotal: NumberLike = None
    discount_total: NumberLike = None
    other_charges_total: NumberLike = None
    tax_total: NumberLike = None
    total: NumberLike = None
    issues: list[str] = []

    @field_validator("issues", mode="before")
    @classmethod
    def _issues(cls, value: Any) -> list[str]:
        if value is None:
            return []
        return [str(v) for v in value] if isinstance(value, list) else [str(value)]


@dataclass
class NormalizedOutput:
    invoice: InvoiceResult
    extras: dict[str, Any] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)


def _number(value: Any, name: str, checks: list[Check]) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    number = parse_number(value)
    if number is None:
        checks.append(Check("INVALID_NUMBER", f"{name} value '{value}' is not a number", "review", True))
    return number


def normalize_llm_output(raw: dict[str, Any], settings: Settings) -> NormalizedOutput:
    """Validate the model's JSON and convert it to the required schema."""
    try:
        parsed = RawLLMOutput.model_validate(raw)
    except ValidationError as exc:
        raise OutputSchemaError([_fmt_error(e) for e in exc.errors()]) from exc

    checks: list[Check] = []
    extras: dict[str, Any] = {
        "document_type": (parsed.document_type or "").lower() or None,
        "issues": sorted(set(parsed.issues)),
    }

    if not parsed.is_invoice:
        has_core = parsed.total is not None and parsed.invoice_number is not None
        if has_core:
            checks.append(
                Check(
                    "AMBIGUOUS_DOCUMENT_TYPE",
                    "Model classified the document as a non-invoice but still found an invoice number and total.",
                    "review",
                )
            )
        return NormalizedOutput(InvoiceResult.non_invoice(), extras, checks)

    # --- dates ---------------------------------------------------------------
    iso_date, ambiguous = normalize_date(parsed.invoice_date, settings.date_day_first)
    if parsed.invoice_date and iso_date is None:
        checks.append(Check("INVALID_DATE", f"Invoice date '{parsed.invoice_date}' is not a valid date", "review", True))
    printed = parsed.invoice_date_as_printed
    extras["invoice_date_as_printed"] = printed
    if iso_date and printed:
        printed_iso, printed_ambiguous = normalize_date(printed, settings.date_day_first)
        alt_iso, _ = normalize_date(printed, not settings.date_day_first)
        readings = {d for d in (printed_iso, alt_iso) if d}
        if readings and iso_date not in readings and re.search(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}", printed):
            checks.append(
                Check("DATE_INCONSISTENT", f"ISO date {iso_date} does not match the printed date '{printed}'", "review", True)
            )
        ambiguous = ambiguous or printed_ambiguous
    if ambiguous or "AMBIGUOUS_DATE" in extras["issues"]:
        severity = "review" if "AMBIGUOUS_DATE" in extras["issues"] else "info"
        checks.append(Check("AMBIGUOUS_DATE", "Day/month order of the printed date is ambiguous", severity))

    # --- currency --------------------------------------------------------------
    currency = normalize_currency(parsed.currency)
    if parsed.currency and currency is None:
        checks.append(Check("INVALID_CURRENCY", f"'{parsed.currency}' is not a recognised currency code", "review"))

    # --- numbers ---------------------------------------------------------------
    tax_total = _number(parsed.tax_total, "tax_total", checks)
    total = _number(parsed.total, "total", checks)
    extras["subtotal"] = _number(parsed.subtotal, "subtotal", checks)
    extras["discount_total"] = _number(parsed.discount_total, "discount_total", checks)
    extras["other_charges_total"] = _number(parsed.other_charges_total, "other_charges_total", checks)

    items: list[LineItem] | None = None
    unit_prices: list[Decimal | None] = []
    if parsed.line_items:
        items = []
        for idx, raw_item in enumerate(parsed.line_items, start=1):
            items.append(
                LineItem(
                    description=normalize_whitespace(raw_item.description),
                    quantity=_number(raw_item.quantity, f"line {idx} quantity", checks),
                    amount=_number(raw_item.amount, f"line {idx} amount", checks),
                )
            )
            unit_prices.append(_number(raw_item.unit_price, f"line {idx} unit_price", checks))
        items = [i for i in items if any(v is not None for v in (i.description, i.quantity, i.amount))] or None
    extras["unit_prices"] = unit_prices

    invoice_number = parsed.invoice_number
    try:
        invoice = InvoiceResult(
            is_invoice=True,
            vendor=normalize_whitespace(parsed.vendor),
            invoice_number=normalize_whitespace(str(invoice_number)) if invoice_number is not None else None,
            invoice_date=iso_date,
            currency=currency,
            line_items=items,
            tax_total=tax_total,
            total=total,
        )
    except ValidationError as exc:
        raise OutputSchemaError([_fmt_error(e) for e in exc.errors()]) from exc
    return NormalizedOutput(invoice, extras, checks)


def _fmt_error(error: dict[str, Any]) -> str:
    loc = ".".join(str(p) for p in error.get("loc", ())) or "root"
    return f"{loc}: {error.get('msg')}"


# ---------------------------------------------------------------------------
# 2. Required-field completeness
# ---------------------------------------------------------------------------

def check_required_fields(invoice: InvoiceResult) -> list[Check]:
    if not invoice.is_invoice:
        return []
    checks = []
    for name, retry in (
        ("vendor", True),
        ("invoice_number", True),
        ("invoice_date", True),
        ("total", True),
        ("currency", False),
    ):
        if getattr(invoice, name) is None:
            checks.append(Check(f"MISSING_{name.upper()}", f"{name.replace('_', ' ')} could not be extracted", "review", retry))
    if not invoice.line_items:
        checks.append(Check("MISSING_LINE_ITEMS", "no line items were extracted", "review", True))
    else:
        incomplete = [i for i, item in enumerate(invoice.line_items, 1) if item.amount is None or not item.description]
        if incomplete:
            checks.append(
                Check(
                    "INCOMPLETE_LINE_ITEMS",
                    f"line item(s) {', '.join(map(str, incomplete))} are missing a description or amount",
                    "review",
                    True,
                )
            )
    if invoice.tax_total is None:
        checks.append(Check("TAX_NOT_FOUND", "no tax amount was found on the document", "info"))
    return checks


# ---------------------------------------------------------------------------
# 3. Financial validation:  sum(line_items.amount) + tax_total ≈ total
# ---------------------------------------------------------------------------

def _money(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def check_financials(invoice: InvoiceResult, extras: dict[str, Any], settings: Settings) -> tuple[dict[str, Any], list[Check]]:
    if not invoice.is_invoice:
        return {"status": "not_applicable"}, []
    items = invoice.line_items or []
    if invoice.total is None or not items or any(i.amount is None for i in items):
        return {
            "status": "insufficient_data",
            "message": "total or line-item amounts are missing, so the arithmetic cannot be checked",
        }, []

    line_sum = sum((i.amount for i in items), Decimal(0))
    tax = invoice.tax_total or Decimal(0)
    expected = line_sum + tax
    total = invoice.total
    tolerance = settings.math_abs_tolerance + settings.math_per_line_tolerance * len(items)
    diff = total - expected
    report: dict[str, Any] = {
        "line_items_sum": _money(line_sum),
        "tax_total": _money(invoice.tax_total),
        "expected_total": _money(expected),
        "total": _money(total),
        "difference": _money(diff),
        "tolerance": _money(tolerance),
        "explanation": None,
    }
    if abs(diff) <= tolerance:
        report["status"] = "pass"
        return report, []

    discount = extras.get("discount_total") or Decimal(0)
    other = extras.get("other_charges_total") or Decimal(0)
    adjusted = expected - abs(discount) + other
    if (discount or other) and abs(total - adjusted) <= tolerance:
        report.update(status="pass_with_adjustments", adjusted_expected_total=_money(adjusted),
                      explanation=f"explained by printed discount ({_money(discount)}) / other charges ({_money(other)}) "
                                  "that the required schema cannot represent")
        return report, [Check("TOTAL_EXPLAINED_BY_ADJUSTMENTS", report["explanation"], "info")]

    if invoice.tax_total and abs(total - line_sum) <= tolerance:
        report.update(status="pass_with_adjustments",
                      explanation="line amounts appear to already include tax (tax-inclusive pricing)")
        return report, [Check("TAX_INCLUSIVE_LINE_AMOUNTS", report["explanation"], "info")]

    subtotal = extras.get("subtotal")
    hint = ""
    if subtotal is not None and abs(subtotal - line_sum) > tolerance:
        hint = f"; line items sum to {line_sum} but the printed subtotal is {subtotal} (missing or misread line items?)"
    report["status"] = "mismatch"
    report["explanation"] = f"line items + tax = {expected}, but total = {total} (difference {diff}){hint}"
    return report, [Check("TOTAL_MISMATCH", report["explanation"], "review", True)]


def check_line_arithmetic(invoice: InvoiceResult, extras: dict[str, Any]) -> list[Check]:
    """quantity × unit_price ≈ amount, for rows where all three were printed."""
    if not invoice.is_invoice or not invoice.line_items:
        return []
    bad = []
    for idx, (item, unit) in enumerate(zip(invoice.line_items, extras.get("unit_prices") or []), start=1):
        if item.quantity is None or unit is None or item.amount is None:
            continue
        expected = item.quantity * unit
        if abs(expected - item.amount) > max(Decimal("0.05"), abs(item.amount) * Decimal("0.01")):
            bad.append(str(idx))
    if bad:
        return [Check("LINE_ITEM_ARITHMETIC", f"quantity × unit price ≠ amount on line(s) {', '.join(bad)} "
                      "(may be per-line discounts or tax)", "info")]
    return []


# ---------------------------------------------------------------------------
# 4. Grounding: are key values actually present in the source text?
# ---------------------------------------------------------------------------

_NUMBER_TOKEN = re.compile(r"\(?-?[\d][\d,.\s]*\d\)?|\d")


def _numbers_in_text(text: str) -> list[Decimal]:
    found = []
    for match in _NUMBER_TOKEN.finditer(text):
        token = match.group(0).strip()
        for candidate in {token, token.replace(" ", "")} | set(token.split()):
            number = parse_number(candidate)
            if number is not None:
                found.append(abs(number))
    return found


def _compact(text: str) -> str:
    return re.sub(r"[\s]", "", text).upper()


def _fuzzy_contains(needle: str, haystack_tokens: list[str], threshold: float) -> bool:
    n = len(needle)
    for token in haystack_tokens:
        if abs(len(token) - n) <= 2 and SequenceMatcher(None, needle, token).ratio() >= threshold:
            return True
    return False


def check_grounding(invoice: InvoiceResult, source_text: str, route: str) -> tuple[dict[str, Any], list[Check]]:
    """Hallucination guard.

    For the digital-text route the model saw *only* this exact text, so a value that
    is absent from it was invented -> review. For OCR/vision routes the text is
    noisy, so misses are recorded as info only.
    """
    if not invoice.is_invoice or not source_text.strip():
        return {"checked": False}, []
    exact = route == "digital_text"
    severity = "review" if exact else "info"
    compact = _compact(source_text)
    tokens = [_compact(t) for t in re.split(r"\s+", source_text) if t]
    result: dict[str, Any] = {"checked": True, "exact_text": exact}
    checks: list[Check] = []

    if invoice.invoice_number:
        needle = _compact(invoice.invoice_number)
        ok = needle in compact or (not exact and _fuzzy_contains(needle, tokens, 0.8))
        result["invoice_number_found"] = ok
        if not ok:
            checks.append(Check("UNGROUNDED_INVOICE_NUMBER",
                                f"invoice number '{invoice.invoice_number}' was not found in the document text", severity, True))

    if invoice.total is not None:
        numbers = _numbers_in_text(source_text)
        ok = any(abs(n - abs(invoice.total)) <= Decimal("0.01") for n in numbers)
        result["total_found"] = ok
        if not ok:
            checks.append(Check("UNGROUNDED_TOTAL", f"total {invoice.total} was not found in the document text", severity, True))

    if invoice.vendor and exact:
        words = [w for w in re.findall(r"[A-Za-z0-9]{3,}", invoice.vendor.upper())
                 if w not in {"PVT", "LTD", "LLC", "INC", "THE", "AND", "PRIVATE", "LIMITED", "CORP", "CO"}]
        if words:
            hits = sum(1 for w in words if w in compact)
            ok = hits / len(words) >= 0.5
            result["vendor_found"] = ok
            if not ok:
                checks.append(Check("UNGROUNDED_VENDOR", f"vendor '{invoice.vendor}' was not found in the document text",
                                    severity, True))
    return result, checks
