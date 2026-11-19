"""Field comparison rules used by the evaluation harness.

The rules are deliberately conservative: they only forgive differences that do not
change meaning (case, whitespace, punctuation, number formatting, legal-suffix
spelling). Anything else counts as an error.

| Field          | Normalisation before exact comparison                                        |
|----------------|-------------------------------------------------------------------------------|
| is_invoice     | boolean equality; a processing failure (null) is always wrong                |
| vendor         | lower-case, '&'->'and', punctuation removed, whitespace collapsed, and        |
|                | trailing legal-form words dropped (Pvt, Private, Ltd, Limited, Inc, LLC, LLP, |
|                | Co, Company, Corp, GmbH, PLC...): "Acme Pvt. Ltd." == "ACME" but             |
|                | "Acme Supplies" != "Acme Traders"                                              |
| invoice_number | whitespace removed, upper-cased, leading '#' or 'NO.' removed                |
| invoice_date   | parsed to a calendar date (ISO); compared as dates                           |
| currency       | ISO code after mapping unambiguous symbols (₹->INR, €->EUR, £->GBP, $->USD)  |
| tax_total/total| Decimal, |expected - predicted| <= 0.01                                        |
| line_items     | one-to-one maximum matching, order ignored; a pair matches only if            |
|                | description similarity >= 0.85, quantity equal, amount within 0.01           |
| nulls          | null == null is correct; null vs value is "missing", value vs null "spurious"|
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from backend.app.services.normalization import normalize_currency, normalize_date, parse_number

AMOUNT_TOLERANCE = Decimal("0.01")
DESCRIPTION_THRESHOLD = 0.85

EVAL_FIELDS = ("is_invoice", "vendor", "invoice_number", "invoice_date", "currency", "line_items", "tax_total", "total")
INVOICE_ONLY_FIELDS = EVAL_FIELDS[1:]

# Legal-form words that don't change which company is meant. Only stripped from the END of the name.
_LEGAL_SUFFIXES = {
    "pvt", "private", "ltd", "limited", "inc", "incorporated", "co", "company", "corp", "corporation",
    "llc", "llp", "lp", "gmbh", "ag", "plc", "pte", "sa", "bv", "opc",
}


def _ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def norm_vendor(value: Any) -> str | None:
    if value is None:
        return None
    text = _ascii_fold(str(value)).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    words = text.split()
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words) or None


def norm_invoice_number(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", "", str(value)).upper()
    text = re.sub(r"^(#|NO\.?:?|NO:)", "", text)
    return text or None


def norm_date(value: Any) -> str | None:
    if value is None:
        return None
    iso, _ = normalize_date(value, day_first=True)
    return iso


def norm_currency(value: Any) -> str | None:
    if value is None:
        return None
    return normalize_currency(value) or str(value).strip().upper() or None


def norm_amount(value: Any) -> Decimal | None:
    return parse_number(value)


def norm_description(value: Any) -> str:
    text = _ascii_fold(str(value or "")).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def description_similarity(a: Any, b: Any) -> float:
    na, nb = norm_description(a), norm_description(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def amounts_equal(a: Decimal | None, b: Decimal | None, tol: Decimal = AMOUNT_TOLERANCE) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol


@dataclass
class FieldResult:
    field: str
    expected: Any
    predicted: Any
    correct: bool
    outcome: str  # correct | wrong | missing | spurious | failed
    note: str | None = None
    similarity: float | None = None


def _outcome(exp_norm: Any, pred_norm: Any, equal: bool) -> str:
    if equal:
        return "correct"
    if pred_norm is None:
        return "missing"
    if exp_norm is None:
        return "spurious"
    return "wrong"


def compare_scalar(name: str, expected: Any, predicted: Any) -> FieldResult:
    if name == "vendor":
        e, p = norm_vendor(expected), norm_vendor(predicted)
        equal = e == p
        sim = SequenceMatcher(None, e or "", p or "").ratio() if e and p else None
        return FieldResult(name, expected, predicted, equal, _outcome(e, p, equal), similarity=sim)
    if name == "invoice_number":
        e, p = norm_invoice_number(expected), norm_invoice_number(predicted)
        equal = e == p
        sim = SequenceMatcher(None, e or "", p or "").ratio() if e and p else None
        return FieldResult(name, expected, predicted, equal, _outcome(e, p, equal), similarity=sim)
    if name == "invoice_date":
        e, p = norm_date(expected), norm_date(predicted)
        equal = e == p
        note = None
        if e and p and not equal and e[:4] == p[:4] and e[5:7] == p[8:10] and e[8:10] == p[5:7]:
            note = "day/month swapped"
        return FieldResult(name, expected, predicted, equal, _outcome(e, p, equal), note=note)
    if name == "currency":
        e, p = norm_currency(expected), norm_currency(predicted)
        equal = e == p
        return FieldResult(name, expected, predicted, equal, _outcome(e, p, equal))
    if name in ("tax_total", "total"):
        e, p = norm_amount(expected), norm_amount(predicted)
        equal = amounts_equal(e, p)
        return FieldResult(name, expected, predicted, equal, _outcome(e, p, equal))
    raise ValueError(f"unknown field {name}")


# ---------------------------------------------------------------------------
# Line items: one-to-one maximum bipartite matching (order independent)
# ---------------------------------------------------------------------------

@dataclass
class LineItemMatch:
    expected_count: int
    predicted_count: int
    matched: int
    pairs: list[tuple[int, int]] = field(default_factory=list)
    unmatched_expected: list[int] = field(default_factory=list)
    unmatched_predicted: list[int] = field(default_factory=list)
    amount_only_matches: int = 0  # extra diagnostic: same amount but description/qty differ

    @property
    def exact(self) -> bool:
        return self.matched == self.expected_count == self.predicted_count


def _items(value: Any) -> list[dict[str, Any]]:
    return [i for i in (value or []) if isinstance(i, dict)]


def items_match(expected: dict[str, Any], predicted: dict[str, Any]) -> bool:
    if description_similarity(expected.get("description"), predicted.get("description")) < DESCRIPTION_THRESHOLD:
        return False
    if not amounts_equal(norm_amount(expected.get("quantity")), norm_amount(predicted.get("quantity")), Decimal("0.0001")):
        return False
    return amounts_equal(norm_amount(expected.get("amount")), norm_amount(predicted.get("amount")))


def _max_matching(adjacency: list[list[int]], n_right: int) -> dict[int, int]:
    """Kuhn's augmenting-path algorithm. Returns right_index -> left_index."""
    match_right: dict[int, int] = {}

    def try_assign(left: int, seen: set[int]) -> bool:
        for right in adjacency[left]:
            if right in seen:
                continue
            seen.add(right)
            if right not in match_right or try_assign(match_right[right], seen):
                match_right[right] = left
                return True
        return False

    for left in range(len(adjacency)):
        try_assign(left, set())
    return match_right


def match_line_items(expected: Any, predicted: Any) -> LineItemMatch:
    exp, pred = _items(expected), _items(predicted)
    adjacency = [[j for j, p in enumerate(pred) if items_match(e, p)] for e in exp]
    match_right = _max_matching(adjacency, len(pred))
    pairs = sorted((left, right) for right, left in match_right.items())
    matched_e = {l for l, _ in pairs}
    matched_p = {r for _, r in pairs}
    result = LineItemMatch(
        expected_count=len(exp),
        predicted_count=len(pred),
        matched=len(pairs),
        pairs=pairs,
        unmatched_expected=[i for i in range(len(exp)) if i not in matched_e],
        unmatched_predicted=[j for j in range(len(pred)) if j not in matched_p],
    )
    # diagnostic only: unmatched GT rows whose amount appears among unmatched predictions
    leftover = [norm_amount(pred[j].get("amount")) for j in result.unmatched_predicted]
    for i in result.unmatched_expected:
        amount = norm_amount(exp[i].get("amount"))
        for k, cand in enumerate(leftover):
            if amount is not None and amounts_equal(amount, cand):
                result.amount_only_matches += 1
                leftover.pop(k)
                break
    return result


def compare_line_items(expected: Any, predicted: Any) -> tuple[FieldResult, LineItemMatch]:
    exp_items, pred_items = _items(expected), _items(predicted)
    match = match_line_items(exp_items, pred_items)
    if not exp_items and not pred_items:  # null and [] are equivalent
        return FieldResult("line_items", expected, predicted, True, "correct"), match
    correct = match.exact
    if correct:
        outcome = "correct"
    elif not pred_items:
        outcome = "missing"
    elif not exp_items:
        outcome = "spurious"
    else:
        outcome = "wrong"
    note = f"{match.matched}/{match.expected_count} expected rows matched; {match.predicted_count} predicted"
    return FieldResult("line_items", expected, predicted, correct, outcome, note=note), match


def compare_document(expected: dict[str, Any], predicted: dict[str, Any] | None) -> tuple[dict[str, FieldResult], LineItemMatch]:
    """Compare all evaluated fields of one document."""
    results: dict[str, FieldResult] = {}
    if predicted is None or predicted.get("is_invoice") is None:
        # processing failure: never counted as a non-invoice
        for name in EVAL_FIELDS:
            exp_value = expected.get(name)
            empty = exp_value in (None, []) and name != "is_invoice"
            results[name] = FieldResult(name, exp_value, None, empty, "correct" if empty else "failed")
        return results, match_line_items(expected.get("line_items"), None)

    exp_inv, pred_inv = bool(expected.get("is_invoice")), bool(predicted.get("is_invoice"))
    results["is_invoice"] = FieldResult("is_invoice", exp_inv, pred_inv, exp_inv == pred_inv,
                                        "correct" if exp_inv == pred_inv else "wrong")
    for name in ("vendor", "invoice_number", "invoice_date", "currency", "tax_total", "total"):
        results[name] = compare_scalar(name, expected.get(name), predicted.get(name))
    results["line_items"], match = compare_line_items(expected.get("line_items"), predicted.get("line_items"))
    return results, match
