"""Write evaluation / failure / cost reports (Markdown, CSV, JSON)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from statistics import mean
from typing import Any

from evaluation.matching import EVAL_FIELDS, __doc__ as MATCHING_RULES
from evaluation.metrics import DocEval


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def usd(value: float | None, digits: int = 5) -> str:
    return "n/a" if value is None else f"${value:.{digits}f}"


def _cell(value: Any, limit: int = 60) -> str:
    if value is None:
        text = "null"
    elif isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    text = text.replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def banner(meta: dict[str, Any]) -> str:
    lines = []
    if meta.get("synthetic"):
        lines.append("> **SYNTHETIC FIXTURES — NOT ASSIGNMENT RESULTS.** These numbers only demonstrate that the "
                     "harness works on generated documents.")
    if meta.get("llm_provider") == "mock":
        lines.append("> **MOCK LLM.** Predictions come from a regex stub (`LLM_PROVIDER=mock`), not a real model.")
    return "\n".join(lines) + ("\n\n" if lines else "")


def meta_table(meta: dict[str, Any]) -> str:
    rows = [
        ("Generated", meta["generated_at"]),
        ("Run tag", meta["tag"]),
        ("Documents evaluated", meta["documents"]),
        ("LLM provider / model", f"{meta['llm_provider']} / {meta['model']} (vision: {meta['vision_model']})"),
        ("Routing mode", meta["routing_mode"]),
        ("Prompt version", f"{meta['prompt_version']} (fingerprint `{meta['prompt_fingerprint']}`)"),
        ("Pipeline version", meta["pipeline_version"]),
        ("Validation retry", meta["validation_retry"]),
        ("Ground truth", meta["ground_truth"]),
    ]
    return "| Setting | Value |\n|---|---|\n" + "\n".join(f"| {k} | {_cell(v, 120)} |" for k, v in rows)


# ---------------------------------------------------------------------------
# evaluation.md / .csv / .json
# ---------------------------------------------------------------------------

def write_evaluation(out: Path, meta: dict, metrics: dict, failures: dict, docs: list[DocEval], baseline: dict | None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "metrics": metrics, "failures": failures}
    (out / "evaluation.json").write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")

    with (out / "evaluation.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["document", "source_file", "field", "scored", "expected", "predicted", "correct", "outcome", "note"])
        for d in docs:
            for name in EVAL_FIELDS:
                fr = d.fields[name]
                writer.writerow([
                    d.name, d.source_file, name, name in d.scored_fields,
                    json.dumps(fr.expected, ensure_ascii=False, default=str),
                    json.dumps(fr.predicted, ensure_ascii=False, default=str),
                    fr.correct, fr.outcome, fr.note or "",
                ])

    m = metrics
    c = m["classification"]
    cm = c["confusion_matrix"]
    li = m["line_items"]
    v = m["validation"]
    r = m["review"]
    p = m["performance"]
    md = [f"# Evaluation report\n", banner(meta), meta_table(meta), "\n## Headline numbers\n",
          "| Metric | Value |\n|---|---|",
          f"| Labelled documents | {m['counts']['labelled_documents']} ({m['counts']['ground_truth_invoices']} invoices, "
          f"{m['counts']['ground_truth_non_invoices']} non-invoice(s)) |",
          f"| Full-document exact-match rate | {pct(m['full_document_exact_match_rate'])} |",
          f"| Field accuracy on invoices (8 fields × invoices) | {pct(m['overall_field_accuracy_on_invoices'])} |",
          f"| Classification F1 (invoice class) | {pct(c['f1'])} |",
          f"| Line-item F1 | {pct(li['f1'])} |",
          f"| Review-flag precision / recall | {pct(r['precision'])} / {pct(r['recall'])} |",
          f"| Processing failures | {m['counts']['processing_failures']} |",
          f"| Avg processing time | {p['avg_processing_ms'] if p['avg_processing_ms'] is not None else 'n/a'} ms |",
          f"| Avg LLM cost per document | {usd(p['avg_cost_usd'])} |",
          "\n## Field-level accuracy\n",
          "Invoice fields are scored only on documents whose ground truth is an invoice, so non-invoices never add "
          "free correct nulls.\n",
          "| Field | Evaluated on | N | Correct | Accuracy | Wrong | Missing | Spurious | Failed |",
          "|---|---|---|---|---|---|---|---|---|"]
    for row in m["field_accuracy"]:
        md.append(f"| {row['field']} | {row['evaluated_on']} | {row['n']} | {row['correct']} | {pct(row['accuracy'])} | "
                  f"{row['wrong']} | {row['missing']} | {row['spurious']} | {row['failed']} |")
    md += [f"\nNon-invoice null compliance (all invoice fields null when the ground truth is a non-invoice): "
           f"{pct(m['non_invoice_null_compliance'])}.\n",
           "\n## Invoice classification\n",
           "| Expected \\ Predicted | invoice | non-invoice | failed |\n|---|---|---|---|",
           f"| **invoice** | {cm['invoice']['invoice']} | {cm['invoice']['non_invoice']} | {cm['invoice']['failed']} |",
           f"| **non-invoice** | {cm['non_invoice']['invoice']} | {cm['non_invoice']['non_invoice']} | {cm['non_invoice']['failed']} |",
           f"\nPrecision {pct(c['precision'])} · Recall {pct(c['recall'])} · F1 {pct(c['f1'])} · Accuracy {pct(c['accuracy'])}. "
           f"{c['note']}\n",
           "\n## Line items\n",
           "| Metric | Value |\n|---|---|",
           f"| Expected / predicted / matched rows | {li['expected_rows']} / {li['predicted_rows']} / {li['matched_rows']} |",
           f"| Row precision / recall / F1 | {pct(li['precision'])} / {pct(li['recall'])} / {pct(li['f1'])} |",
           f"| Invoices with every row correct | {li['documents_all_rows_correct']} / {li['documents']} |",
           f"| Invoices with the correct row count | {li['documents_correct_row_count']} / {li['documents']} |",
           f"| Rows with the right amount but different description/qty | {li['rows_amount_matched_but_description_or_qty_differs']} |",
           "\n## Validation\n",
           "| Metric | Value |\n|---|---|",
           f"| Schema validation success rate | {pct(v['schema_validation_success_rate'])} |",
           f"| Schema valid on first try (no repair call) | {pct(v['schema_valid_first_try_rate'])} |",
           f"| Financial validation pass rate | {pct(v['financial_validation_pass_rate'])} ({v['financial_validation_note']}) |",
           f"| Financial check outcomes | {_cell(v['financial_status_counts'], 200)} |",
           "\n## Human-review flag effectiveness\n",
           f"{r['definition']}\n",
           "| Metric | Value |\n|---|---|",
           f"| Documents flagged | {r['flagged']} of {r['documents']} |",
           f"| Documents that contain errors | {r['documents_with_errors']} |",
           f"| Incorrect results caught (flagged & wrong) | {r['incorrect_caught']} |",
           f"| Incorrect results missed (not flagged & wrong) | {r['incorrect_missed']} |",
           f"| Correct results flagged anyway | {r['correct_but_flagged']} |",
           f"| Review precision (% of flagged docs that contain errors) | {pct(r['precision'])} |",
           f"| Review recall (% of wrong docs that were flagged) | {pct(r['recall'])} |",
           f"| Missed documents | {', '.join(r['missed_documents']) or 'none'} |",
           "\n## Performance and cost\n",
           "| Metric | Value |\n|---|---|",
           f"| Avg / median / max processing time | {p['avg_processing_ms']} / {p['median_processing_ms']} / {p['max_processing_ms']} ms |",
           f"| Avg LLM cost per document | {usd(p['avg_cost_usd'])} |",
           f"| Total LLM cost (first-run equivalent) | {usd(p['total_cost_usd'])} |",
           f"| Avg tokens per document | {p['avg_tokens']} |",
           "\nSee `cost_analysis.md` for the full breakdown.\n",
           "\n## Per-document results\n",
           "| Document | GT | Status | Route | Correct | Exact | Wrong fields | Flagged | Review reasons | Cost |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for row in m["per_document"]:
        md.append(
            f"| {_cell(row['document'], 40)} | {'invoice' if row['expected_is_invoice'] else 'non-invoice'} | "
            f"{row['status'] if row['status'] == 'success' else 'FAILED ' + (row['error_code'] or '')} | {row['route'] or '-'} | "
            f"{row['correct_fields']}/{row['total_fields']} | {'yes' if row['exact_match'] else 'no'} | "
            f"{_cell(', '.join(row['wrong_fields']) or '-', 60)} | {'yes' if row['needs_review'] else 'no'} | "
            f"{_cell(', '.join(row['review_reasons']) or '-', 60)} | {usd(row['cost_usd'])} |")

    if baseline and baseline.get("meta", {}).get("tag") != meta["tag"]:
        md += ["\n## Change vs. saved baseline\n", comparison_table(baseline, payload)]

    md += ["\n## Matching rules\n", "```text", MATCHING_RULES.strip(), "```",
           "\n## Regenerate\n", "```bash\nmake eval            # extract (cached) + score + write all reports\n"
           "make eval-score      # re-score existing outputs without calling the LLM\n```\n"]
    (out / "evaluation.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def headline(payload: dict) -> dict[str, Any]:
    m = payload["metrics"]
    return {
        "exact_match": m["full_document_exact_match_rate"],
        "field_accuracy": m["overall_field_accuracy_on_invoices"],
        "classification_f1": m["classification"]["f1"],
        "line_item_f1": m["line_items"]["f1"],
        "review_precision": m["review"]["precision"],
        "review_recall": m["review"]["recall"],
        "avg_cost_usd": m["performance"]["avg_cost_usd"],
        "avg_processing_ms": m["performance"]["avg_processing_ms"],
        "field": {r["field"]: r["accuracy"] for r in m["field_accuracy"]},
    }


def comparison_table(base: dict, current: dict) -> str:
    b, c = headline(base), headline(current)
    bm, cm = base["meta"], current["meta"]
    lines = [f"Baseline: tag `{bm['tag']}`, routing `{bm['routing_mode']}`, prompt `{bm['prompt_version']}`, "
             f"generated {bm['generated_at']}.\n",
             f"| Metric | Baseline ({bm['tag']}) | This run ({cm['tag']}) | Δ |", "|---|---|---|---|"]

    def row(label: str, bv: Any, cv: Any, money: bool = False) -> None:
        if money:
            delta = "n/a" if bv is None or cv is None else f"{cv - bv:+.5f}"
            lines.append(f"| {label} | {usd(bv)} | {usd(cv)} | {delta} |")
        else:
            delta = "n/a" if bv is None or cv is None else f"{(cv - bv) * 100:+.1f} pp"
            lines.append(f"| {label} | {pct(bv)} | {pct(cv)} | {delta} |")

    row("Exact-match rate", b["exact_match"], c["exact_match"])
    row("Field accuracy (invoices)", b["field_accuracy"], c["field_accuracy"])
    for name in EVAL_FIELDS:
        row(f"· {name}", b["field"].get(name), c["field"].get(name))
    row("Classification F1", b["classification_f1"], c["classification_f1"])
    row("Line-item F1", b["line_item_f1"], c["line_item_f1"])
    row("Review precision", b["review_precision"], c["review_precision"])
    row("Review recall", b["review_recall"], c["review_recall"])
    row("Avg cost / doc", b["avg_cost_usd"], c["avg_cost_usd"], money=True)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# failure_analysis.md
# ---------------------------------------------------------------------------

APPROACH = """\
**Initial (baseline) approach — routing `auto`, prompt `v1`:**

1. Validate the upload by magic bytes, size and page limits.
2. Digital PDFs (every page has a usable text layer) → embedded text from PyMuPDF → one text-only LLM call.
   This is exact text, so it is the cheapest and most reliable input.
3. Scanned PDFs, photos and mixed PDFs → Tesseract OCR (with grayscale/upscale/contrast/denoise and
   automatic rotation) → one multimodal call with the page image(s) as the source of truth and the
   OCR text as a hint.
4. The model returns strict JSON (json_schema); Pydantic validates it; a single repair call is made
   if the JSON is invalid.
5. Financial check (sum of line amounts + tax ≈ total, Decimal arithmetic), required-field check and
   a grounding check (are the invoice number / total / vendor present in the source text?).
6. If a retryable check fails, one validation retry is made, escalating text routes to the vision
   route; the attempt with fewer problems is kept and disagreements are flagged.
7. needs_review is set from the remaining observable problems.
"""


def write_failure_analysis(out: Path, meta: dict, failures: dict, experiments: list[dict], baseline: dict | None,
                           current: dict) -> None:
    md = ["# Failure analysis\n", banner(meta), meta_table(meta), "\n## Errors by cause\n"]
    counts = failures["category_counts"]
    if not counts:
        md.append("No field errors were observed on the labelled documents in this run.\n")
    else:
        md += ["| Cause | Errors | Documents affected |", "|---|---|---|"]
        md += [f"| {cat} | {v['errors']} | {v['documents']} |" for cat, v in counts.items()]
        hp = failures["highest_priority"]
        md += ["\n## Highest-priority improvement\n",
               f"**{hp['category']}** — {hp['errors']} error(s) across {hp['documents_affected']} document(s) "
               f"({hp['rationale']}).\n", f"Proposed fix: {hp['recommendation']}\n"]
        md.append("\n## Every observed error\n")
        by_cat: dict[str, list[dict]] = {}
        for item in failures["items"]:
            by_cat.setdefault(item["category"], []).append(item)
        for cat in counts:
            md += [f"\n### {cat}\n", "| Document | Field | Expected | Predicted | Likely reason | Proposed fix |",
                   "|---|---|---|---|---|---|"]
            for it in by_cat[cat]:
                md.append(f"| {_cell(it['document'], 40)} | {it['field']} | {_cell(it['expected'])} | "
                          f"{_cell(it['predicted'])} | {_cell(it['likely_reason'], 140)} | {_cell(it['proposed_fix'], 160)} |")
    md += ["\n## Approach and experiments\n", APPROACH]
    executed = [e for e in experiments if bool(e.get("synthetic")) == bool(meta.get("synthetic"))]
    if len({e["tag"] for e in executed}) <= 1:
        md.append("\n**Alternatives tried:** none executed yet with this harness. Run, for example, "
                  "`make eval-experiment ROUTING=vision_always TAG=vision_always` and this section will be "
                  "regenerated with the measured comparison. No result is reported here unless it was run.\n")
    else:
        md += ["\n**Runs actually executed (from `reports/experiments/index.json`):**\n",
               "| Tag | When | Routing | Prompt | Exact match | Field acc. | Line-item F1 | Avg cost |",
               "|---|---|---|---|---|---|---|---|"]
        for e in executed:
            h = e["headline"]
            md.append(f"| {e['tag']} | {e['generated_at']} | {e['routing_mode']} | {e['prompt_version']} | "
                      f"{pct(h['exact_match'])} | {pct(h['field_accuracy'])} | {pct(h['line_item_f1'])} | {usd(h['avg_cost_usd'])} |")
        if baseline and baseline["meta"]["tag"] != meta["tag"]:
            md += ["\n**This run compared with the baseline:**\n", comparison_table(baseline, current)]
        md.append("\nInterpretation of what did or did not work should be written by hand after reading the table "
                  "above; with ~10 labelled documents, a difference of one document is 10 percentage points.\n")
    (out / "failure_analysis.md").write_text("\n".join(md) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# cost_analysis.md
# ---------------------------------------------------------------------------

def cost_summary(diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    per_doc = []
    retry_cost = 0.0
    retries = 0
    calls = 0
    for d in diagnostics:
        attempts = d.get("attempts") or []
        cost = sum(a.get("cost_usd", 0.0) for a in attempts)
        per_doc.append({
            "cost": cost,
            "input": sum(a.get("input_tokens", 0) for a in attempts),
            "cached_input": sum(a.get("cached_input_tokens", 0) for a in attempts),
            "output": sum(a.get("output_tokens", 0) for a in attempts),
            "llm_ms": sum(a.get("latency_ms", 0) for a in attempts),
            "total_ms": d.get("original_processing_ms") if d.get("cached") and d.get("original_processing_ms") else d.get("processing_ms"),
            "attempts": len(attempts),
            "cached": bool(d.get("cached")),
            "route": d.get("route"),
            "spent_this_run": float((d.get("usage") or {}).get("cost_usd") or 0.0),
        })
        calls += sum(a.get("http_calls", 0) for a in attempts)
        for a in attempts:
            if a.get("purpose") != "initial":
                retries += 1
                retry_cost += a.get("cost_usd", 0.0)
    billed = [p for p in per_doc if p["attempts"]]
    route_costs: dict[str, list[float]] = {}
    for p in billed:
        route_costs.setdefault(p["route"] or "none", []).append(p["cost"])

    def avg(key: str) -> float | None:
        return mean(p[key] for p in billed) if billed else None

    return {
        "documents": len(per_doc),
        "documents_with_llm_calls": len(billed),
        "cached_documents": sum(p["cached"] for p in per_doc),
        "total_cost_usd": sum(p["cost"] for p in per_doc),
        "spent_this_run_usd": sum(p["spent_this_run"] for p in per_doc),
        "avg_cost_usd": avg("cost"),
        "max_cost_usd": max((p["cost"] for p in billed), default=None),
        "min_cost_usd": min((p["cost"] for p in billed), default=None),
        "avg_input_tokens": avg("input"),
        "avg_cached_input_tokens": avg("cached_input"),
        "avg_output_tokens": avg("output"),
        "avg_llm_latency_ms": avg("llm_ms"),
        "avg_processing_ms": mean([p["total_ms"] for p in per_doc if p["total_ms"] is not None]) if per_doc else None,
        "retry_attempts": retries,
        "retry_cost_usd": retry_cost,
        "http_calls": calls,
        "avg_cost_by_route": {k: mean(v) for k, v in route_costs.items()},
        "docs_by_route": {k: len(v) for k, v in route_costs.items()},
    }


def write_cost_analysis(out: Path, meta: dict, summary: dict[str, Any], pricing: dict[str, Any], budget: float | None,
                        filename: str = "cost_analysis.md") -> None:
    s = summary
    fmt_tokens = lambda v: "n/a" if v is None else f"{v:,.0f}"  # noqa: E731
    per_1000 = None if s["avg_cost_usd"] is None else s["avg_cost_usd"] * 1000
    md = ["# LLM cost analysis\n", banner(meta), meta_table(meta),
          "\n## Pricing assumptions\n",
          "| Model | Input / 1M | Cached input / 1M | Output / 1M |", "|---|---|---|---|"]
    for model, p in pricing.items():
        md.append(f"| {model} | ${p['input_per_1m_usd']} | ${p['cached_input_per_1m_usd']} | ${p['output_per_1m_usd']} |")
    md += ["\nCost per call = (prompt tokens − cached tokens) × input rate + cached tokens × cached rate + "
           "completion tokens × output rate, using the token counts returned by the API (`usage`). Image tokens are "
           "included in prompt tokens by the provider. Rates are configuration (`PRICE_*` / `MODEL_PRICING_JSON`) and "
           "must be checked against the provider's current price list.\n",
           "\n## Totals\n", "| Metric | Value |", "|---|---|",
          f"| Documents | {s['documents']} ({s['documents_with_llm_calls']} needed LLM calls) |",
          f"| Total API spend to extract these documents | {usd(s['total_cost_usd'])} |",
          f"| Spent during this run (cache hits cost $0) | {usd(s['spent_this_run_usd'])} ({s['cached_documents']} cached) |",
          f"| Average cost per document | {usd(s['avg_cost_usd'])} |",
          f"| Minimum / maximum cost per document | {usd(s['min_cost_usd'])} / {usd(s['max_cost_usd'])} |",
          f"| Average input tokens (of which cached) | {fmt_tokens(s['avg_input_tokens'])} ({fmt_tokens(s['avg_cached_input_tokens'])}) |",
          f"| Average output tokens | {fmt_tokens(s['avg_output_tokens'])} |",
          f"| Average LLM latency per document | {fmt_tokens(s['avg_llm_latency_ms'])} ms |",
          f"| Average end-to-end processing time | {fmt_tokens(s['avg_processing_ms'])} ms |",
          f"| Retry / repair attempts | {s['retry_attempts']} costing {usd(s['retry_cost_usd'])} |",
          f"| HTTP calls (incl. transport retries) | {s['http_calls']} |",
          f"| Projected cost per 1,000 similar documents | {usd(per_1000, 2)} |",
          f"| Evaluation budget guard | {usd(budget, 2) if budget is not None else 'off'} |",
          "\n## Cost by route\n", "| Route | Documents | Avg cost |", "|---|---|---|"]
    for route, value in s["avg_cost_by_route"].items():
        md.append(f"| {route} | {s['docs_by_route'][route]} | {usd(value)} |")
    md += ["\n## LLM cost vs. hosting cost\n",
           "These figures are **LLM provider charges only**. EC2/EBS/public-IPv4/data-transfer charges are billed by "
           "AWS separately (see README → *AWS cost awareness*). AWS promotional credits generally do not pay "
           "third-party LLM API bills.\n"]
    out.mkdir(parents=True, exist_ok=True)
    (out / filename).write_text("\n".join(md) + "\n", encoding="utf-8")
