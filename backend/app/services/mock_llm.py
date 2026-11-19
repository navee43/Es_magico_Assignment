"""Offline stand-in for the LLM, for UI development and smoke tests ONLY.

Enabled with LLM_PROVIDER=mock. It applies a few regexes to the text it is given,
costs nothing, and is clearly labelled as "mock" everywhere (model name, UI banner).
It is never used for evaluation results.
"""

from __future__ import annotations

import json
import re

from backend.app.services.llm_client import HTTPCall, LLMResponse

_AMOUNT = r"([\d][\d,]*\.\d{2})"


class MockLLMClient:
    provider = "mock"

    def complete(self, system, user_text, images, json_schema, model) -> LLMResponse:
        match = re.search(r"<document_text>\n?(.*?)</document_text>", user_text, flags=re.DOTALL)
        text = match.group(1) if match else ""
        return LLMResponse(json.dumps(self._extract(text)), "mock-regex", "stop", [HTTPCall(True, 5, 200)])

    @staticmethod
    def _extract(text: str) -> dict:
        lower = text.lower()
        is_invoice = bool(re.search(r"\b(tax\s+)?invoice\b|\bbill\s+no\b", lower))
        empty = {
            "document_type": "invoice" if is_invoice else "other", "is_invoice": is_invoice,
            "vendor": None, "invoice_number": None, "invoice_date": None, "invoice_date_as_printed": None,
            "currency": None, "line_items": None, "subtotal": None, "discount_total": None,
            "other_charges_total": None, "tax_total": None, "total": None, "issues": [],
        }
        if not is_invoice:
            return empty
        lines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("--- page")]
        out = dict(empty)
        out["vendor"] = lines[0] if lines else None
        m = re.search(r"invoice\s*(?:no\.?|number|#)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]*)", text, re.I)
        out["invoice_number"] = m.group(1) if m else None
        m = re.search(r"date\s*[:\-]?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})", text, re.I)
        out["invoice_date"] = out["invoice_date_as_printed"] = m.group(1) if m else None
        if "₹" in text or "inr" in lower or "rs." in lower:
            out["currency"] = "INR"
        elif "€" in text or "eur" in lower:
            out["currency"] = "EUR"
        elif "$" in text or "usd" in lower:
            out["currency"] = "USD"
        items = []
        for line in lines:
            row = re.match(rf"^(.+?)\s+(\d+(?:\.\d+)?)\s+{_AMOUNT}\s+{_AMOUNT}$", line)
            if row and not re.search(r"total|tax|gst|vat", row.group(1), re.I):
                items.append({
                    "description": row.group(1).strip(), "quantity": float(row.group(2)),
                    "unit_price": float(row.group(3).replace(",", "")),
                    "amount": float(row.group(4).replace(",", "")),
                })
        out["line_items"] = items or None
        m = re.findall(rf"(?:tax|gst|vat)[^\n]*?{_AMOUNT}", text, re.I)
        out["tax_total"] = float(m[-1].replace(",", "")) if m else None
        m = re.findall(rf"(?:grand\s+total|total\s+due|total)[^\n\d]*{_AMOUNT}", text, re.I)
        out["total"] = float(m[-1].replace(",", "")) if m else None
        return out
