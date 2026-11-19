"""Human-review decision built only from observable quality signals.

No invented confidence percentages: every reason maps to something we measured
(a failed check, an OCR statistic, the model's own reported problems, or a
disagreement between two extraction attempts).
"""

from __future__ import annotations

from typing import Any

from backend.app.config import Settings
from backend.app.schemas.invoice import InvoiceResult, ReviewIssue
from backend.app.services.pdf_processor import DocumentContent
from backend.app.services.validator import Check

# Document types that are frequently confused with invoices.
BORDERLINE_TYPES = {"receipt", "proforma_invoice", "credit_note"}

# Problems the model itself reported that justify a human look.
REVIEW_WORTHY_MODEL_ISSUES = {
    "ILLEGIBLE_TEXT": "the model reported illegible text",
    "PARTIAL_DOCUMENT": "the document appears cut off or incomplete",
    "MULTIPLE_INVOICES": "the file appears to contain more than one invoice",
    "AMBIGUOUS_TOTAL": "the model could not tell which amount is the invoice total",
    "AMBIGUOUS_VENDOR": "the model could not tell which party is the vendor",
}

REVIEW_REASON_LABELS = {
    "MISSING_VENDOR": "Vendor missing",
    "MISSING_INVOICE_NUMBER": "Invoice number missing",
    "MISSING_INVOICE_DATE": "Invoice date missing",
    "MISSING_TOTAL": "Total missing",
    "MISSING_CURRENCY": "Currency missing",
    "MISSING_LINE_ITEMS": "Line items missing",
    "INCOMPLETE_LINE_ITEMS": "Incomplete line items",
    "TOTAL_MISMATCH": "Totals don't add up",
    "INVALID_DATE": "Invalid date",
    "DATE_INCONSISTENT": "Date inconsistent with printed date",
    "AMBIGUOUS_DATE": "Ambiguous date format",
    "INVALID_CURRENCY": "Unknown currency",
    "INVALID_NUMBER": "Unparseable number",
    "LOW_OCR_QUALITY": "Poor OCR quality",
    "AMBIGUOUS_DOCUMENT_TYPE": "Ambiguous document type",
    "LLM_RESULT_CONFLICT": "Extraction attempts disagree",
    "UNGROUNDED_INVOICE_NUMBER": "Invoice number not in source",
    "UNGROUNDED_TOTAL": "Total not in source",
    "UNGROUNDED_VENDOR": "Vendor not in source",
    "MODEL_REPORTED_ISSUE": "Model reported a problem",
    "PAGES_TRUNCATED": "Only first pages processed",
    "PROCESSING_FAILED": "Processing failed",
}


def document_level_checks(
    invoice: InvoiceResult, extras: dict[str, Any], content: DocumentContent, route: str, settings: Settings
) -> list[Check]:
    checks: list[Check] = []

    # OCR quality (only meaningful when OCR was needed AND could run). If Tesseract is missing,
    # that's a server setup problem, not evidence the document is hard to read.
    if content.ocr_used and content.ocr_unavailable:
        checks.append(
            Check(
                "OCR_UNAVAILABLE",
                "OCR could not run (Tesseract is not installed or OCR is disabled); the model read the page image "
                "without an OCR hint, and OCR-based checks were skipped",
                "info",
            )
        )
    elif content.ocr_used and settings.review_on_low_ocr:
        conf = content.ocr_mean_confidence
        words = content.ocr_word_count
        if conf is None or conf < settings.ocr_review_min_confidence or words < settings.ocr_review_min_words:
            checks.append(
                Check(
                    "LOW_OCR_QUALITY",
                    f"OCR confidence {conf if conf is not None else 'n/a'} over {words} words "
                    f"(thresholds: {settings.ocr_review_min_confidence} / {settings.ocr_review_min_words} words)",
                    "review",
                )
            )

    doc_type = extras.get("document_type")
    if doc_type in BORDERLINE_TYPES:
        checks.append(
            Check("AMBIGUOUS_DOCUMENT_TYPE", f"document type '{doc_type}' is often confused with invoices", "review")
        )
    if doc_type == "invoice" and not invoice.is_invoice:
        checks.append(Check("AMBIGUOUS_DOCUMENT_TYPE", "typed as 'invoice' but classified as non-invoice", "review"))

    reported = [code for code in extras.get("issues", []) if code in REVIEW_WORTHY_MODEL_ISSUES]
    if reported and invoice.is_invoice:
        checks.append(
            Check("MODEL_REPORTED_ISSUE", "; ".join(REVIEW_WORTHY_MODEL_ISSUES[c] for c in reported), "review")
        )
    elif extras.get("issues") and "ILLEGIBLE_TEXT" in extras["issues"]:
        checks.append(Check("MODEL_REPORTED_ISSUE", REVIEW_WORTHY_MODEL_ISSUES["ILLEGIBLE_TEXT"], "review"))

    if content.truncated:
        checks.append(
            Check("PAGES_TRUNCATED", f"only the first {len(content.pages)} of {content.pages_total} pages were processed", "review")
        )
    return checks


KEY_FIELDS = ("is_invoice", "vendor", "invoice_number", "invoice_date", "currency", "tax_total", "total")


def compare_attempts(first: InvoiceResult, second: InvoiceResult) -> list[str]:
    """Return the key fields on which two extraction attempts disagree."""
    a = first.model_dump(mode="json")
    b = second.model_dump(mode="json")
    diffs = [f for f in KEY_FIELDS if a.get(f) != b.get(f)]
    count_a = len(first.line_items or [])
    count_b = len(second.line_items or [])
    if count_a != count_b:
        diffs.append("line_items")
    return diffs


def to_review_issues(checks: list[Check]) -> list[ReviewIssue]:
    seen: dict[tuple[str, str], ReviewIssue] = {}
    for check in checks:
        key = (check.code, check.message)
        if key not in seen:
            seen[key] = ReviewIssue(code=check.code, message=check.message, severity=check.severity)
    return list(seen.values())


def review_reasons(issues: list[ReviewIssue]) -> list[str]:
    reasons: list[str] = []
    for issue in issues:
        if issue.severity == "review" and issue.code not in reasons:
            reasons.append(issue.code)
    return reasons


def issue_score(checks: list[Check]) -> tuple[int, int]:
    """Lower is better: (#review-level problems, #info-level problems)."""
    return (sum(c.severity == "review" for c in checks), sum(c.severity == "info" for c in checks))
