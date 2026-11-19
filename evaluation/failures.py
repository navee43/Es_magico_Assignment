"""Failure analysis: turn every field error into a categorised, explainable item.

Categories follow the assignment brief. The "likely reason" is derived from
observable evidence (route, OCR stats, validation results, whether the predicted
value appears in the source text) - it is a hypothesis to verify, not a verdict.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from evaluation.matching import norm_amount
from evaluation.metrics import DocEval

CATEGORIES = [
    "Unreadable document / processing failure",
    "Invoice classification errors",
    "OCR errors",
    "LLM hallucinations",
    "Missing / incorrect vendor details",
    "Incorrect invoice dates",
    "Invoice number confusion",
    "Missing line items",
    "Line item value errors",
    "Incorrect tax extraction",
    "Totals mismatch",
    "Currency errors",
]

FIXES = {
    "Unreadable document / processing failure": "Inspect the file; add format-specific handling (e.g. password, rotation, "
    "multi-page split) or route it straight to manual review.",
    "Invoice classification errors": "Add a clearer is_invoice definition for this document type to the prompt "
    "(without copying ground-truth values) and flag that type for review.",
    "OCR errors": "Send page images to the vision model for this kind of scan (route to 'vision'), raise render DPI, "
    "or add stronger preprocessing (deskew, adaptive threshold).",
    "LLM hallucinations": "Tighten the 'null if not printed' rule, make grounding failures review-level for this route, "
    "and lower temperature/try a stronger model.",
    "Missing / incorrect vendor details": "Clarify seller vs. buyer ('Bill To') in the prompt; send the page image so "
    "logo-only vendor names are visible.",
    "Incorrect invoice dates": "Use the document locale/currency to resolve DD/MM vs MM/DD and keep invoice vs due date "
    "distinct; flag AMBIGUOUS_DATE for review.",
    "Invoice number confusion": "Prompt with explicit negatives (PO/order/GSTIN/customer id) and require the label "
    "next to the value; make UNGROUNDED_INVOICE_NUMBER review-level.",
    "Missing line items": "Send page images for multi-line tables, raise max output tokens, and use the printed subtotal "
    "check to trigger a retry.",
    "Line item value errors": "Ask for one row per printed row (no merging), keep the row's printed amount (not unit "
    "price), and compare against quantity × unit price.",
    "Incorrect tax extraction": "Specify how to aggregate split taxes (CGST+SGST, multiple VAT rates) and to use null "
    "when no tax is printed.",
    "Totals mismatch": "Distinguish grand total from subtotal / amount paid / balance due; retry on TOTAL_MISMATCH with "
    "the page image.",
    "Currency errors": "Map symbols/locale explicitly and return null only when no currency evidence exists.",
}


@dataclass
class FailureItem:
    document: str
    field: str
    category: str
    expected: Any
    predicted: Any
    likely_reason: str
    proposed_fix: str


def _source_text_hint(diag: dict[str, Any]) -> str:
    route = diag.get("route") or "n/a"
    src = diag.get("source") or {}
    conf = src.get("ocr_mean_confidence")
    parts = [f"route={route}"]
    if src.get("ocr_pages"):
        parts.append(f"OCR conf={conf}")
    return ", ".join(parts)


def _is_ocr_like(fr, diag: dict[str, Any]) -> bool:
    """A near-miss (similar strings / same digits) on a document that needed OCR or vision."""
    used_images = (diag.get("route") in ("ocr_text", "vision")) or (diag.get("source") or {}).get("ocr_pages")
    return bool(used_images and fr.similarity is not None and 0.75 <= fr.similarity < 1.0)


def _grounding_missing(diag: dict[str, Any], key: str) -> bool:
    grounding = (diag.get("validation") or {}).get("grounding") or {}
    return grounding.get(key) is False


def categorise(doc: DocEval) -> list[FailureItem]:
    diag = doc.diagnostics or {}
    hint = _source_text_hint(diag)
    items: list[FailureItem] = []

    def add(field: str, category: str, reason: str, expected: Any = None, predicted: Any = None) -> None:
        fr = doc.fields.get(field)
        items.append(FailureItem(
            doc.name, field, category,
            fr.expected if fr and expected is None else expected,
            fr.predicted if fr and predicted is None else predicted,
            f"{reason} ({hint})", FIXES[category],
        ))

    if doc.failed:
        code = diag.get("error_code") or "UNKNOWN"
        add("is_invoice", "Unreadable document / processing failure",
            f"processing failed with {code}: {diag.get('error_message') or ''}".strip(),
            expected=doc.expected.get("is_invoice"), predicted=None)
        return items

    is_inv = doc.fields["is_invoice"]
    if not is_inv.correct:
        doc_type = diag.get("document_type")
        reason = (f"model typed the document as '{doc_type}'" if doc_type else "classification disagreed with ground truth")
        add("is_invoice", "Invoice classification errors", reason)
        return items  # all other fields follow from the classification error
    if not doc.expected_invoice:
        if doc.null_compliant is False:
            add("is_invoice", "LLM hallucinations", "non-invoice classified correctly but invoice fields were filled",
                expected="all null", predicted="values present")
        return items

    fin = ((diag.get("validation") or {}).get("financial") or {})
    for name, fr in doc.fields.items():
        if fr.correct or name == "is_invoice":
            continue
        if fr.outcome == "spurious":
            add(name, "LLM hallucinations", "a value was returned where the ground truth has none")
            continue
        if name in ("vendor", "invoice_number") and _is_ocr_like(fr, diag):
            add(name, "OCR errors", f"near-miss (similarity {fr.similarity:.2f}) on an image-based document")
            continue
        if name == "vendor":
            if fr.outcome == "missing":
                reason = "vendor not extracted (possibly logo-only or unlabeled)"
            elif _grounding_missing(diag, "vendor_found"):
                add(name, "LLM hallucinations", "predicted vendor does not appear in the document text")
                continue
            else:
                reason = "a different party was extracted (buyer/ship-to or a brand vs legal name)"
            add(name, "Missing / incorrect vendor details", reason)
        elif name == "invoice_number":
            if _grounding_missing(diag, "invoice_number_found"):
                add(name, "LLM hallucinations", "predicted invoice number does not appear in the document text")
            else:
                add(name, "Invoice number confusion",
                    "invoice number missing" if fr.outcome == "missing" else "another identifier was picked (PO/order/tax id?)")
        elif name == "invoice_date":
            reason = fr.note or ("date missing" if fr.outcome == "missing" else "a different date was picked (due/order date?)")
            add(name, "Incorrect invoice dates", reason)
        elif name == "currency":
            add(name, "Currency errors", "currency missing" if fr.outcome == "missing" else "currency mis-mapped")
        elif name == "line_items":
            m = doc.line_match
            if m and m.predicted_count < m.expected_count:
                add(name, "Missing line items", f"{m.predicted_count} of {m.expected_count} rows predicted; {fr.note}")
            else:
                reason = fr.note or "row values differ"
                if m and m.amount_only_matches:
                    reason += f"; {m.amount_only_matches} row(s) have correct amounts but different description/qty"
                add(name, "Line item value errors", reason)
        elif name == "tax_total":
            add(name, "Incorrect tax extraction",
                "tax missing" if fr.outcome == "missing" else _numeric_reason(fr, fin))
        elif name == "total":
            add(name, "Totals mismatch", "total missing" if fr.outcome == "missing" else _numeric_reason(fr, fin))
    return items


def _numeric_reason(fr, fin: dict[str, Any]) -> str:
    e, p = norm_amount(fr.expected), norm_amount(fr.predicted)
    reason = "wrong amount"
    if e is not None and p is not None:
        if sorted(str(abs(e)).replace(".", "")) == sorted(str(abs(p)).replace(".", "")):
            reason = "same digits in a different order (misread/transposed digits)"
        elif abs(e) != 0 and abs(p / e - 1) < 0.25:
            reason = f"close but different (off by {p - e})"
        else:
            reason = "a different printed amount was picked (subtotal / amount paid / balance?)"
    if fin.get("status"):
        reason += f"; arithmetic check: {fin['status']}"
    return reason


def analyse(docs: list[DocEval]) -> dict[str, Any]:
    items = [item for doc in docs for item in categorise(doc)]
    counts = Counter(i.category for i in items)
    affected: dict[str, set[str]] = defaultdict(set)
    for i in items:
        affected[i.category].add(i.document)
    ranked = sorted(counts, key=lambda c: (-len(affected[c]), -counts[c], CATEGORIES.index(c)))
    priority = None
    if ranked:
        top = ranked[0]
        priority = {
            "category": top,
            "errors": counts[top],
            "documents_affected": len(affected[top]),
            "recommendation": FIXES[top],
            "rationale": "ranked by number of labelled documents affected, then by error count",
        }
    return {
        "items": [i.__dict__ for i in items],
        "category_counts": {c: {"errors": counts[c], "documents": len(affected[c])} for c in ranked},
        "highest_priority": priority,
    }
