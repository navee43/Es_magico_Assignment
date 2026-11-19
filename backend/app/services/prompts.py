"""Extraction prompt and the JSON schema the model must follow.

PROMPT_VERSION must be bumped whenever the wording changes; it is part of the
cache key and is recorded in every evaluation report (see reports/prompt_changelog.md).
"""

from __future__ import annotations

import hashlib

PROMPT_VERSION = "v1"

DOCUMENT_TYPES = [
    "invoice",
    "receipt",
    "proforma_invoice",
    "credit_note",
    "purchase_order",
    "quotation",
    "delivery_note",
    "statement",
    "other",
]

ISSUE_CODES = [
    "ILLEGIBLE_TEXT",
    "PARTIAL_DOCUMENT",
    "MULTIPLE_INVOICES",
    "HANDWRITTEN_VALUES",
    "AMBIGUOUS_TOTAL",
    "AMBIGUOUS_DATE",
    "AMBIGUOUS_VENDOR",
    "NO_LINE_ITEM_TABLE",
    "TAX_INCLUSIVE_PRICES",
]

SYSTEM_PROMPT = """You are a meticulous accounts-payable data extraction engine.
You receive one document (its text and/or page images) and return a single JSON object.

STEP 1 - CLASSIFY
- document_type: one of """ + ", ".join(DOCUMENT_TYPES) + """.
- is_invoice = true only for documents that bill a buyer for goods or services: an invoice,
  tax invoice, bill, commercial invoice, or a sales receipt / cash memo that itemises a completed
  sale with amounts. Purchase orders, quotations/estimates, proforma invoices, delivery notes,
  statements of account, credit notes, payment confirmations, letters, forms, resumes, IDs,
  menus, tickets and any other documents are NOT invoices.
- If is_invoice is false, set every invoice field (vendor ... total, line_items, subtotal,
  discount_total, other_charges_total) to null.

STEP 2 - EXTRACT (only when is_invoice is true)
Golden rule: copy values that are printed in the document. Never invent, guess or "fix" a value.
If a field is absent, unreadable or you are not sure, return null for it.
- vendor: the seller / issuer that is being paid (usually the letterhead or "From"), NOT the
  buyer, "Bill To" or "Ship To" party. Company or person name only, without address.
- invoice_number: the identifier labelled Invoice No / Invoice # / Bill No / Receipt No.
  Not the PO number, order number, GSTIN/VAT/tax id, customer id or account number.
  Copy it exactly, keeping prefixes, hyphens and slashes.
- invoice_date: the issue/invoice date (not due, order, delivery or print date), as YYYY-MM-DD.
  Resolve numeric dates using the document's locale (e.g. Indian/European documents use
  DD/MM/YYYY, US documents MM/DD/YYYY). If the order is genuinely unclear add AMBIGUOUS_DATE.
  Also copy the date exactly as printed into invoice_date_as_printed.
- currency: ISO-4217 code (INR, USD, EUR, GBP, ...). Use printed codes or symbols
  (₹/Rs → INR, € → EUR, £ → GBP, $ → USD unless the document indicates another dollar such as
  AUD/CAD/SGD). If there is no currency evidence at all, return null.
- line_items: one entry per billed row of the items table, in document order.
  description = the row's item text (join wrapped lines with a space);
  quantity = the numeric quantity if printed, else null;
  unit_price = the unit rate if printed, else null;
  amount = the row's line total exactly as printed (not the unit price).
  Do not include subtotal, tax, discount, shipping summary or total rows as line items.
  If there is no itemised table, return null and add NO_LINE_ITEM_TABLE.
- subtotal: the printed subtotal / taxable value before tax, else null.
- discount_total: total discount printed in the summary, as a positive number, else null.
- other_charges_total: shipping, handling, service fees or round-off adjustments printed in the
  summary (signed: negative round-off stays negative), else null.
- tax_total: the total tax amount. If a single total tax is printed use it; if only separate tax
  lines are printed (e.g. CGST + SGST, or several VAT rates) add them up. If no tax amount is
  printed return null (use 0 only if the document explicitly shows zero tax).
- total: the final invoice total / grand total / amount payable for this invoice (after tax,
  discounts and round-off). Not an amount already paid and not a running account balance.
- Numbers: plain JSON numbers, no currency symbols or thousands separators (e.g. 123456.78).

STEP 3 - REPORT PROBLEMS
issues: list any of """ + ", ".join(ISSUE_CODES) + """ that apply, else [].
Report problems honestly instead of guessing; a reviewer will check flagged documents.

Return ONLY the JSON object matching the schema."""


def _nullable(kind: str) -> dict:
    return {"type": [kind, "null"]}


EXTRACTION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "document_type": {"type": "string", "enum": DOCUMENT_TYPES},
        "is_invoice": {"type": "boolean"},
        "vendor": _nullable("string"),
        "invoice_number": _nullable("string"),
        "invoice_date": _nullable("string"),
        "invoice_date_as_printed": _nullable("string"),
        "currency": _nullable("string"),
        "line_items": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "description": _nullable("string"),
                    "quantity": _nullable("number"),
                    "unit_price": _nullable("number"),
                    "amount": _nullable("number"),
                },
                "required": ["description", "quantity", "unit_price", "amount"],
            },
        },
        "subtotal": _nullable("number"),
        "discount_total": _nullable("number"),
        "other_charges_total": _nullable("number"),
        "tax_total": _nullable("number"),
        "total": _nullable("number"),
        "issues": {"type": "array", "items": {"type": "string", "enum": ISSUE_CODES}},
    },
    "required": [
        "document_type",
        "is_invoice",
        "vendor",
        "invoice_number",
        "invoice_date",
        "invoice_date_as_printed",
        "currency",
        "line_items",
        "subtotal",
        "discount_total",
        "other_charges_total",
        "tax_total",
        "total",
        "issues",
    ],
}

SOURCE_DESCRIPTIONS = {
    "digital_text": "DOCUMENT TEXT (extracted from the PDF's embedded text layer; exact):",
    "ocr_text": "DOCUMENT TEXT (from OCR; may contain recognition errors such as 0/O, 1/l, 5/S, 8/B):",
    "vision_hint": (
        "The page images are the source of truth. OCR text is provided only as a hint and may "
        "contain recognition errors; when they disagree, trust the image."
    ),
}


def build_user_message(route: str, text: str, n_images: int, pages_total: int, feedback: str | None) -> str:
    lines = [f"The document has {pages_total} page(s)."]
    if route == "vision":
        lines.append(f"{n_images} page image(s) are attached. {SOURCE_DESCRIPTIONS['vision_hint']}")
        if text.strip():
            lines += ["", "OCR / TEXT-LAYER HINT:", "<document_text>", text, "</document_text>"]
    else:
        lines += ["", SOURCE_DESCRIPTIONS[route], "<document_text>", text, "</document_text>"]
    if feedback:
        lines += ["", feedback]
    return "\n".join(lines)


def prompt_fingerprint() -> str:
    """Short hash of the prompt + schema, recorded in reports for traceability."""
    import json

    blob = SYSTEM_PROMPT + json.dumps(EXTRACTION_SCHEMA, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]
