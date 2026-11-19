"""Evaluation harness entry point.

    make eval                         # = python -m evaluation.run
    python -m evaluation.run --score-only                  # re-score outputs/ without LLM calls
    python -m evaluation.run --tag baseline                # snapshot to reports/experiments/baseline/
    python -m evaluation.run --tag vision_always --routing vision_always
    python -m evaluation.run --synthetic                   # harness demo on generated fixtures

Ground truth: one JSON per document in data/ground_truth/<document-stem>.json (or a
single JSON file mapping file names to records). Documents: data/labelled/.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.app.cli.extract_all import find_documents, run_batch
from backend.app.config import PIPELINE_VERSION, PROJECT_ROOT, get_settings
from backend.app.services import prompts
from backend.app.services.cost_tracker import BudgetGuard, get_pricing
from backend.app.services.extractor import InvoiceExtractor
from evaluation.failures import analyse
from evaluation.metrics import evaluate_document, compute_all
from evaluation.report import (
    cost_summary,
    headline,
    write_cost_analysis,
    write_evaluation,
    write_failure_analysis,
)

FILE_KEYS = ("file", "filename", "file_name", "document", "source_file", "name")


def _unwrap(record: dict[str, Any]) -> dict[str, Any]:
    for key in ("expected", "ground_truth", "label", "output"):
        if isinstance(record.get(key), dict):
            return record[key]
    return record


def load_ground_truth(path: Path) -> dict[str, dict[str, Any]]:
    """Return {document_stem: expected_record}."""
    truth: dict[str, dict[str, Any]] = {}
    if path.is_dir():
        for file in sorted(path.glob("*.json")):
            data = json.loads(file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "is_invoice" in _unwrap(data):
                name = next((data[k] for k in FILE_KEYS if isinstance(data.get(k), str)), file.stem)
                truth[Path(name).stem] = _unwrap(data)
            else:
                truth.update(_from_collection(data))
    elif path.is_file():
        truth.update(_from_collection(json.loads(path.read_text(encoding="utf-8"))))
    return truth


def _from_collection(data: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict) and "is_invoice" in _unwrap(value):
                out[Path(key).stem] = _unwrap(value)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                name = next((item[k] for k in FILE_KEYS if isinstance(item.get(k), str)), None)
                if name and "is_invoice" in _unwrap(item):
                    out[Path(name).stem] = _unwrap(item)
    return out


def _load_prediction(out_dir: Path, name: str) -> tuple[dict | None, dict | None]:
    pred_path = out_dir / f"{name}.json"
    diag_path = out_dir / "diagnostics" / f"{name}.json"
    pred = json.loads(pred_path.read_text(encoding="utf-8")) if pred_path.is_file() else None
    diag = json.loads(diag_path.read_text(encoding="utf-8")).get("diagnostics") if diag_path.is_file() else None
    return pred, diag


def _update_index(index_path: Path, entry: dict[str, Any]) -> list[dict[str, Any]]:
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else []
    index = [e for e in index if not (e["tag"] == entry["tag"] and e.get("synthetic") == entry.get("synthetic"))]
    index.append(entry)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--docs", type=Path, default=PROJECT_ROOT / "data" / "labelled")
    parser.add_argument("--ground-truth", type=Path, default=PROJECT_ROOT / "data" / "ground_truth")
    parser.add_argument("--outputs", type=Path, help="predictions folder (default outputs/ or outputs/experiments/<tag>)")
    parser.add_argument("--reports", type=Path, help="reports folder (default reports/ or reports/experiments/<tag>)")
    parser.add_argument("--tag", default="main", help="name of this run; anything but 'main' is stored as an experiment")
    parser.add_argument("--routing", choices=["auto", "ocr_first", "vision_always", "text_only"])
    parser.add_argument("--score-only", action="store_true", help="score existing predictions; no LLM calls")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--budget", type=float, help="USD limit for this run (default EVAL_BUDGET_USD)")
    parser.add_argument("--synthetic", action="store_true", help="generate and evaluate SYNTHETIC fixtures")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.routing:
        settings = dataclasses.replace(settings, routing_mode=args.routing)

    reports_root = PROJECT_ROOT / "reports"
    outputs_root = PROJECT_ROOT / "outputs"
    if args.synthetic:
        from evaluation.synthetic import generate

        fixtures = PROJECT_ROOT / "evaluation" / "fixtures" / "synthetic"
        generate(fixtures)
        args.docs, args.ground_truth = fixtures / "documents", fixtures / "ground_truth"
        reports_root, outputs_root = reports_root / "synthetic", outputs_root / "synthetic"
    is_main = args.tag == "main"
    reports_dir = args.reports or (reports_root if is_main else reports_root / "experiments" / args.tag)
    outputs_dir = args.outputs or (outputs_root if is_main else outputs_root / "experiments" / args.tag)

    gt_path = args.ground_truth
    truth = load_ground_truth(gt_path) if gt_path.exists() else {}
    if not truth:
        truth = load_ground_truth(args.docs)  # JSON labels stored next to the documents
    if not truth:
        print(f"No ground truth found in {gt_path} or {args.docs}. Put one <document>.json per labelled document "
              f"in data/ground_truth/ (see README).", file=sys.stderr)
        return 1
    documents = [(n, p) for n, p in find_documents([args.docs]) if n in truth]
    missing = sorted(set(truth) - {n for n, _ in documents})
    if missing:
        print(f"warning: ground truth without a document: {', '.join(missing)}", file=sys.stderr)
    if not documents:
        print(f"No labelled documents in {args.docs} match the ground-truth names.", file=sys.stderr)
        return 1

    budget = Decimal(str(args.budget)) if args.budget is not None else settings.eval_budget_usd
    if args.score_only:
        loaded = [(n, p, *_load_prediction(outputs_dir, n)) for n, p in documents]
    else:
        print(f"Extracting {len(documents)} labelled documents (routing={settings.routing_mode}, "
              f"model={settings.llm_model}, budget=${budget}) ...")
        extractor = InvoiceExtractor(settings, budget=BudgetGuard(budget))
        records = run_batch(documents, outputs_dir, extractor, use_cache=not args.no_cache)
        loaded = []
        for r in records:
            o = r["outcome"]
            loaded.append((r["name"], Path(r["path"]), o.public_json(), o.diagnostics.model_dump(mode="json")))

    docs = [evaluate_document(name, path.name, truth[name], pred, diag) for name, path, pred, diag in loaded]
    metrics = compute_all(docs)
    failures = analyse(docs)
    diagnostics = [d for *_, d in loaded if d]
    models = sorted({m for d in diagnostics for m in (d.get("usage") or {}).get("models", [])} |
                    {a.get("model") for d in diagnostics for a in d.get("attempts", []) if a.get("model")})
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tag": args.tag,
        "synthetic": args.synthetic,
        "documents": len(docs),
        "llm_provider": settings.llm_provider,
        "model": settings.llm_model if settings.llm_provider != "mock" else "mock-regex",
        "vision_model": settings.llm_vision_model if settings.llm_provider != "mock" else "mock-regex",
        "models_seen": models,
        "routing_mode": settings.routing_mode,
        "prompt_version": prompts.PROMPT_VERSION,
        "prompt_fingerprint": prompts.prompt_fingerprint(),
        "pipeline_version": PIPELINE_VERSION,
        "validation_retry": settings.validation_retry,
        "ground_truth": str(gt_path.relative_to(PROJECT_ROOT) if gt_path.is_relative_to(PROJECT_ROOT) else gt_path),
        "score_only": args.score_only,
    }

    baseline_path = reports_root / "experiments" / "baseline" / "evaluation.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.is_file() else None
    write_evaluation(reports_dir, meta, metrics, failures, docs, baseline)
    current = json.loads((reports_dir / "evaluation.json").read_text(encoding="utf-8"))
    index = _update_index(reports_root / "experiments" / "index.json", {
        "tag": args.tag, "generated_at": meta["generated_at"], "routing_mode": meta["routing_mode"],
        "prompt_version": meta["prompt_version"], "model": meta["model"], "synthetic": args.synthetic,
        "headline": headline(current),
    })
    write_failure_analysis(reports_dir, meta, failures, index, baseline, current)
    pricing = {m: get_pricing(m, settings).as_dict() for m in (models or [settings.llm_model])}
    write_cost_analysis(reports_dir, meta, cost_summary(diagnostics), pricing, float(budget))

    print_results(metrics, meta)
    print(f"\nFull reports: {reports_dir / 'evaluation.md'}, failure_analysis.md, cost_analysis.md, evaluation.csv")
    return 0


def _pct(value: float | None) -> str:
    return "  n/a" if value is None else f"{value * 100:5.1f}%"


def print_results(m: dict[str, Any], meta: dict[str, Any]) -> None:
    """Plain-text results table for the terminal (same numbers as reports/evaluation.md)."""
    c = m["counts"]
    line = "-" * 78
    if meta.get("synthetic") or meta.get("llm_provider") == "mock":
        print("\n*** SYNTHETIC / MOCK RUN - NOT ASSIGNMENT RESULTS ***")
    print(f"\n{line}\nFIELD-LEVEL ACCURACY  ({c['labelled_documents']} labelled docs: "
          f"{c['ground_truth_invoices']} invoices, {c['ground_truth_non_invoices']} non-invoices)\n{line}")
    print(f"{'field':<16}{'scored on':<12}{'correct':>9}{'accuracy':>10}{'wrong':>7}{'missing':>9}{'extra':>7}{'failed':>8}")
    for r in m["field_accuracy"]:
        scope = "all docs" if r["field"] == "is_invoice" else "invoices"
        print(f"{r['field']:<16}{scope:<12}{str(r['correct']) + '/' + str(r['n']):>9}{_pct(r['accuracy']):>10}"
              f"{r['wrong']:>7}{r['missing']:>9}{r['spurious']:>7}{r['failed']:>8}")
    print(f"{line}\nPER DOCUMENT\n{line}")
    print(f"{'document':<28}{'truth':<9}{'fields':>8}  {'exact':<6}{'flagged':<9}wrong fields")
    for d in m["per_document"]:
        fields = "FAILED" if d["status"] == "failed" else f"{d['correct_fields']}/{d['total_fields']}"
        wrong = ", ".join(d["wrong_fields"]) or "-"
        if d["status"] == "failed":
            wrong = d.get("error_code") or "processing failed"
        print(f"{d['document'][:27]:<28}{'invoice' if d['expected_is_invoice'] else 'non-inv':<9}{fields:>8}  "
              f"{'yes' if d['exact_match'] else 'no':<6}{'yes' if d['needs_review'] else 'no':<9}{wrong}")
    cl, li, rv, pf = m["classification"], m["line_items"], m["review"], m["performance"]
    print(f"{line}\nSUMMARY\n{line}")
    print(f"Full-document exact match : {_pct(m['full_document_exact_match_rate'])}")
    print(f"Field accuracy (invoices) : {_pct(m['overall_field_accuracy_on_invoices'])}")
    print(f"Invoice classification    : precision {_pct(cl['precision'])}  recall {_pct(cl['recall'])}  F1 {_pct(cl['f1'])}")
    print(f"Line items (row matching) : precision {_pct(li['precision'])}  recall {_pct(li['recall'])}  F1 {_pct(li['f1'])}")
    print(f"Review flag               : caught {rv['incorrect_caught']}, missed {rv['incorrect_missed']}, "
          f"flagged-but-correct {rv['correct_but_flagged']}  (precision {_pct(rv['precision'])}, recall {_pct(rv['recall'])})")
    cost = pf["avg_cost_usd"]
    print(f"Avg time / cost per doc   : {pf['avg_processing_ms']} ms / " + ("n/a" if cost is None else f"${cost:.5f}"))
    print(line)


if __name__ == "__main__":
    sys.exit(main())
