"""Aggregate metrics over per-document comparisons.

Accuracy integrity rules:
* Invoice-field accuracy is computed ONLY over documents whose ground truth is an
  invoice. Non-invoice documents never contribute "free" correct nulls.
* Non-invoices are scored separately (classification + "all fields null" compliance).
* Processing failures count as errors everywhere (never as non-invoices).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, median
from typing import Any

from evaluation.matching import EVAL_FIELDS, INVOICE_ONLY_FIELDS, FieldResult, LineItemMatch, compare_document


@dataclass
class DocEval:
    name: str
    source_file: str
    expected: dict[str, Any]
    predicted: dict[str, Any] | None
    diagnostics: dict[str, Any] | None
    fields: dict[str, FieldResult] = field(default_factory=dict)
    line_match: LineItemMatch | None = None

    @property
    def failed(self) -> bool:
        return self.predicted is None or self.predicted.get("is_invoice") is None

    @property
    def expected_invoice(self) -> bool:
        return bool(self.expected.get("is_invoice"))

    @property
    def scored_fields(self) -> list[str]:
        return list(EVAL_FIELDS) if self.expected_invoice else ["is_invoice"]

    @property
    def null_compliant(self) -> bool | None:
        """For ground-truth non-invoices: did the prediction leave all invoice fields null?"""
        if self.expected_invoice or self.failed:
            return None
        return all(self.predicted.get(f) in (None, []) for f in INVOICE_ONLY_FIELDS)

    @property
    def correct_fields(self) -> int:
        n = sum(self.fields[f].correct for f in self.scored_fields)
        if not self.expected_invoice:
            n += 1 if self.null_compliant else 0
        return n

    @property
    def total_fields(self) -> int:
        return len(self.scored_fields) + (0 if self.expected_invoice else 1)

    @property
    def accuracy(self) -> float:
        return self.correct_fields / self.total_fields

    @property
    def exact_match(self) -> bool:
        return not self.failed and self.correct_fields == self.total_fields

    @property
    def has_error(self) -> bool:
        return not self.exact_match

    @property
    def flagged(self) -> bool:
        if self.predicted is None:
            return True
        return bool(self.predicted.get("needs_review"))

    @property
    def wrong_fields(self) -> list[str]:
        wrong = [f for f in self.scored_fields if not self.fields[f].correct]
        if self.null_compliant is False:
            wrong.append("non_invoice_fields_not_null")
        return wrong

    @property
    def cost_usd(self) -> float:
        d = self.diagnostics or {}
        if d.get("cached"):
            return float(d.get("original_cost_usd") or 0.0)
        return float((d.get("usage") or {}).get("cost_usd") or 0.0)

    @property
    def processing_ms(self) -> int | None:
        d = self.diagnostics or {}
        if d.get("cached") and d.get("original_processing_ms") is not None:
            return int(d["original_processing_ms"])
        return d.get("processing_ms")


def evaluate_document(name: str, source_file: str, expected: dict, predicted: dict | None, diagnostics: dict | None) -> DocEval:
    doc = DocEval(name, source_file, expected, predicted, diagnostics)
    doc.fields, doc.line_match = compare_document(expected, predicted)
    return doc


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _prf(tp: int, fp: int, fn: int) -> dict[str, float | None]:
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    f1 = None
    if precision is not None and recall is not None and precision + recall > 0:
        f1 = round(2 * precision * recall / (precision + recall), 4)
    return {"precision": precision, "recall": recall, "f1": f1}


def field_accuracy(docs: list[DocEval]) -> list[dict[str, Any]]:
    invoices = [d for d in docs if d.expected_invoice]
    rows = []
    for name in EVAL_FIELDS:
        pool = docs if name == "is_invoice" else invoices
        outcomes = [d.fields[name].outcome for d in pool]
        correct = sum(d.fields[name].correct for d in pool)
        rows.append({
            "field": name,
            "evaluated_on": "all labelled documents" if name == "is_invoice" else "ground-truth invoices",
            "n": len(pool),
            "correct": correct,
            "accuracy": _ratio(correct, len(pool)),
            "wrong": outcomes.count("wrong"),
            "missing": outcomes.count("missing"),
            "spurious": outcomes.count("spurious"),
            "failed": outcomes.count("failed"),
        })
    return rows


def classification(docs: list[DocEval]) -> dict[str, Any]:
    matrix = {"invoice": {"invoice": 0, "non_invoice": 0, "failed": 0},
              "non_invoice": {"invoice": 0, "non_invoice": 0, "failed": 0}}
    for d in docs:
        row = "invoice" if d.expected_invoice else "non_invoice"
        col = "failed" if d.failed else ("invoice" if d.predicted.get("is_invoice") else "non_invoice")
        matrix[row][col] += 1
    tp = matrix["invoice"]["invoice"]
    fp = matrix["non_invoice"]["invoice"]
    fn = matrix["invoice"]["non_invoice"] + matrix["invoice"]["failed"]
    tn = matrix["non_invoice"]["non_invoice"]
    return {
        "confusion_matrix": matrix,
        "positive_class": "invoice",
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "failed_on_non_invoices": matrix["non_invoice"]["failed"],
        **_prf(tp, fp, fn),
        "accuracy": _ratio(tp + tn, len(docs)),
        "note": "Processing failures are counted as wrong (FN for invoices); never as correct non-invoices.",
    }


def line_item_metrics(docs: list[DocEval]) -> dict[str, Any]:
    pool = [d for d in docs if d.expected_invoice and d.line_match is not None]
    tp = sum(d.line_match.matched for d in pool)
    fp = sum(d.line_match.predicted_count - d.line_match.matched for d in pool)
    fn = sum(d.line_match.expected_count - d.line_match.matched for d in pool)
    return {
        "documents": len(pool),
        "expected_rows": sum(d.line_match.expected_count for d in pool),
        "predicted_rows": sum(d.line_match.predicted_count for d in pool),
        "matched_rows": tp,
        **_prf(tp, fp, fn),
        "documents_all_rows_correct": sum(d.line_match.exact for d in pool),
        "documents_correct_row_count": sum(d.line_match.expected_count == d.line_match.predicted_count for d in pool),
        "rows_amount_matched_but_description_or_qty_differs": sum(d.line_match.amount_only_matches for d in pool),
    }


def validation_metrics(docs: list[DocEval]) -> dict[str, Any]:
    diags = [d.diagnostics or {} for d in docs]
    processed = [x for x in diags if x]
    success = [x for x in processed if x.get("status") == "success"]
    first_try = [x for x in success if (x.get("validation") or {}).get("schema_valid_first_try")]
    fin_statuses: dict[str, int] = {}
    for d in docs:
        if d.failed or not d.predicted.get("is_invoice"):
            continue
        status = ((d.diagnostics or {}).get("validation") or {}).get("financial", {}).get("status", "unknown")
        fin_statuses[status] = fin_statuses.get(status, 0) + 1
    checkable = sum(v for k, v in fin_statuses.items() if k in ("pass", "pass_with_adjustments", "mismatch"))
    passed = fin_statuses.get("pass", 0) + fin_statuses.get("pass_with_adjustments", 0)
    return {
        "documents": len(docs),
        "schema_valid": len(success),
        "schema_validation_success_rate": _ratio(len(success), len(docs)),
        "schema_valid_first_try_rate": _ratio(len(first_try), len(docs)),
        "financial_status_counts": fin_statuses,
        "financial_validation_pass_rate": _ratio(passed, checkable),
        "financial_validation_note": "pass rate over predicted invoices whose totals and line amounts were all present",
    }


def review_effectiveness(docs: list[DocEval]) -> dict[str, Any]:
    tp = sum(d.flagged and d.has_error for d in docs)
    fp = sum(d.flagged and not d.has_error for d in docs)
    fn = sum(not d.flagged and d.has_error for d in docs)
    tn = sum(not d.flagged and not d.has_error for d in docs)
    prf = _prf(tp, fp, fn)
    return {
        "definition": "A document 'contains an error' if it is not a full exact match (or processing failed).",
        "documents": len(docs),
        "flagged": tp + fp,
        "documents_with_errors": tp + fn,
        "incorrect_caught": tp,
        "incorrect_missed": fn,
        "correct_but_flagged": fp,
        "correct_not_flagged": tn,
        "precision": prf["precision"],
        "recall": prf["recall"],
        "f1": prf["f1"],
        "pct_flagged_with_errors": None if prf["precision"] is None else round(prf["precision"] * 100, 1),
        "missed_documents": [d.name for d in docs if not d.flagged and d.has_error],
    }


def performance(docs: list[DocEval]) -> dict[str, Any]:
    times = [d.processing_ms for d in docs if d.processing_ms is not None]
    costs = [d.cost_usd for d in docs if d.diagnostics]
    tokens = []
    for d in docs:
        attempts = (d.diagnostics or {}).get("attempts") or []
        tokens.append(sum(a.get("input_tokens", 0) + a.get("output_tokens", 0) for a in attempts))
    return {
        "avg_processing_ms": round(mean(times)) if times else None,
        "median_processing_ms": round(median(times)) if times else None,
        "max_processing_ms": max(times) if times else None,
        "avg_cost_usd": round(mean(costs), 6) if costs else None,
        "total_cost_usd": round(sum(costs), 6),
        "avg_tokens": round(mean(tokens)) if tokens else None,
    }


def per_document(docs: list[DocEval]) -> list[dict[str, Any]]:
    rows = []
    for d in docs:
        diag = d.diagnostics or {}
        rows.append({
            "document": d.name,
            "source_file": d.source_file,
            "expected_is_invoice": d.expected_invoice,
            "status": "failed" if d.failed else "success",
            "error_code": diag.get("error_code"),
            "route": diag.get("route"),
            "correct_fields": d.correct_fields,
            "total_fields": d.total_fields,
            "accuracy": round(d.accuracy, 4),
            "exact_match": d.exact_match,
            "wrong_fields": d.wrong_fields,
            "needs_review": d.flagged,
            "review_reasons": diag.get("review_reasons", []),
            "cost_usd": round(d.cost_usd, 6),
            "processing_ms": d.processing_ms,
            "cached": bool(diag.get("cached")),
        })
    return rows


def compute_all(docs: list[DocEval]) -> dict[str, Any]:
    invoices = [d for d in docs if d.expected_invoice]
    non_invoices = [d for d in docs if not d.expected_invoice]
    return {
        "counts": {
            "labelled_documents": len(docs),
            "ground_truth_invoices": len(invoices),
            "ground_truth_non_invoices": len(non_invoices),
            "processing_failures": sum(d.failed for d in docs),
        },
        "field_accuracy": field_accuracy(docs),
        "overall_field_accuracy_on_invoices": _ratio(
            sum(d.fields[f].correct for d in invoices for f in EVAL_FIELDS), len(invoices) * len(EVAL_FIELDS)
        ),
        "full_document_exact_match_rate": _ratio(sum(d.exact_match for d in docs), len(docs)),
        "exact_match_invoices": _ratio(sum(d.exact_match for d in invoices), len(invoices)),
        "exact_match_non_invoices": _ratio(sum(d.exact_match for d in non_invoices), len(non_invoices)),
        "non_invoice_null_compliance": _ratio(sum(bool(d.null_compliant) for d in non_invoices), len(non_invoices)),
        "classification": classification(docs),
        "line_items": line_item_metrics(docs),
        "validation": validation_metrics(docs),
        "review": review_effectiveness(docs),
        "performance": performance(docs),
        "per_document": per_document(docs),
    }
