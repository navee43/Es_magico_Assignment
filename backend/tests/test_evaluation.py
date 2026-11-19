"""Evaluation harness: matching rules and metric integrity."""

import json

from evaluation.failures import analyse
from evaluation.matching import compare_document, compare_scalar, match_line_items
from evaluation.metrics import compute_all, evaluate_document
from evaluation.run import load_ground_truth

GT = {
    "is_invoice": True, "vendor": "Acme Supplies Pvt Ltd", "invoice_number": "INV-2041", "invoice_date": "2026-03-14",
    "currency": "INR",
    "line_items": [{"description": "A4 paper, 500 sheets", "quantity": 10, "amount": 3200.00},
                   {"description": "Stapler", "quantity": 2, "amount": 900.00}],
    "tax_total": 738.00, "total": 4838.00, "needs_review": False,
}
NON = {k: None for k in GT} | {"is_invoice": False, "needs_review": False}


def test_vendor_normalisation_is_lenient_only_on_formatting():
    assert compare_scalar("vendor", "Acme Supplies Pvt. Ltd.", "ACME SUPPLIES PRIVATE LIMITED").correct
    assert compare_scalar("vendor", "Acme & Co", "acme and company").correct
    assert compare_scalar("vendor", "Acme Pvt Ltd", "Acme").correct
    assert compare_scalar("vendor", "BluePeak Office Supplies Pvt. Ltd.", "Bluepeak Office Supplies").correct
    assert not compare_scalar("vendor", "Acme Co-operative Society", "Acme").correct
    assert not compare_scalar("vendor", "Acme Supplies", "Acme Traders").correct


def test_invoice_number_whitespace_only():
    assert compare_scalar("invoice_number", "INV - 2041", "inv-2041").correct
    assert not compare_scalar("invoice_number", "INV-2041", "INV2041").correct


def test_dates_numbers_currency():
    assert compare_scalar("invoice_date", "14/03/2026", "2026-03-14").correct
    r = compare_scalar("invoice_date", "2026-03-04", "2026-04-03")
    assert not r.correct and r.note == "day/month swapped"
    assert compare_scalar("total", "4,838.00", 4838.004).correct
    assert not compare_scalar("total", 4838.00, 4838.05).correct
    assert compare_scalar("currency", "₹", "INR").correct


def test_null_handling():
    assert compare_scalar("tax_total", None, None).outcome == "correct"
    assert compare_scalar("tax_total", 10, None).outcome == "missing"
    assert compare_scalar("tax_total", None, 10).outcome == "spurious"


def test_line_items_order_independent_and_strict():
    pred = list(reversed(GT["line_items"]))
    assert match_line_items(GT["line_items"], pred).exact
    wrong_amount = [GT["line_items"][0], {"description": "Stapler", "quantity": 2, "amount": 950.0}]
    m = match_line_items(GT["line_items"], wrong_amount)
    assert m.matched == 1 and not m.exact
    renamed = [GT["line_items"][0], {"description": "Office stapler (heavy)", "quantity": 2, "amount": 900.0}]
    m = match_line_items(GT["line_items"], renamed)
    assert m.matched == 1 and m.amount_only_matches == 1


def test_null_and_empty_line_items_equivalent():
    fields, _ = compare_document(NON, dict(NON, line_items=[]))
    assert fields["line_items"].correct


def _docs():
    perfect = evaluate_document("a", "a.pdf", GT, dict(GT), {"status": "success", "usage": {"cost_usd": 0.001}})
    wrong_total = evaluate_document("b", "b.pdf", GT, dict(GT, total=4900.0, needs_review=True), {"status": "success"})
    missed = evaluate_document("c", "c.pdf", GT, dict(GT, vendor="Globex"), {"status": "success"})
    non_invoice_ok = evaluate_document("d", "d.pdf", NON, dict(NON), {"status": "success"})
    failed = evaluate_document("e", "e.pdf", GT, None, {"status": "failed", "error_code": "CORRUPTED_FILE"})
    return [perfect, wrong_total, missed, non_invoice_ok, failed]


def test_non_invoices_do_not_inflate_field_accuracy():
    m = compute_all(_docs())
    vendor = next(r for r in m["field_accuracy"] if r["field"] == "vendor")
    assert vendor["n"] == 4  # only ground-truth invoices
    assert vendor["correct"] == 2 and vendor["failed"] == 1


def test_failure_counts_as_error_never_as_non_invoice():
    m = compute_all(_docs())
    cm = m["classification"]["confusion_matrix"]
    assert cm["invoice"]["failed"] == 1 and cm["invoice"]["non_invoice"] == 0
    assert m["classification"]["fn"] == 1
    assert m["full_document_exact_match_rate"] == 0.4  # a and d


def test_review_effectiveness():
    r = compute_all(_docs())["review"]
    # flagged: b (wrong, caught), e (failed -> flagged). missed: c. correct & unflagged: a, d
    assert (r["incorrect_caught"], r["incorrect_missed"], r["correct_but_flagged"]) == (2, 1, 0)
    assert r["precision"] == 1.0 and r["recall"] == round(2 / 3, 4)
    assert r["missed_documents"] == ["c"]


def test_failure_analysis_categories():
    result = analyse(_docs())
    cats = result["category_counts"]
    assert "Totals mismatch" in cats and "Unreadable document / processing failure" in cats
    assert result["highest_priority"] is not None
    item = next(i for i in result["items"] if i["field"] == "total")
    assert item["expected"] == 4838.0 and item["predicted"] == 4900.0 and item["proposed_fix"]


def test_ground_truth_loader_formats(tmp_path):
    folder = tmp_path / "gt"
    folder.mkdir()
    (folder / "inv1.json").write_text(json.dumps(GT))
    (folder / "wrapped.json").write_text(json.dumps({"file": "scan_7.jpg", "expected": NON}))
    truth = load_ground_truth(folder)
    assert set(truth) == {"inv1", "scan_7"}
    combined = tmp_path / "all.json"
    combined.write_text(json.dumps([{"filename": "x.pdf", **GT}, {"filename": "y.png", **NON}]))
    assert set(load_ground_truth(combined)) == {"x", "y"}
