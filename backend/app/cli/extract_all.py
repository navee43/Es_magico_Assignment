"""Extract every document in the dataset folders and write one JSON per document.

    python -m backend.app.cli.extract_all                      # data/labelled + data/unlabelled -> outputs/
    python -m backend.app.cli.extract_all --inputs some/dir --out outputs

Writes:
    outputs/<name>.json               the 9 required fields (exact assignment schema)
    outputs/diagnostics/<name>.json   review reasons, validation, routing, tokens, cost
    outputs/run_summary.json          per-run totals
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from backend.app.config import PROJECT_ROOT, get_settings
from backend.app.services.cost_tracker import BudgetGuard
from backend.app.services.extractor import ExtractionOutcome, InvoiceExtractor

IGNORED = {".json", ".md", ".txt", ".csv", ".gitkeep"}


def find_documents(folders: list[Path]) -> list[tuple[str, Path]]:
    """Return (output_name, path) pairs; names are made unique across folders."""
    found: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            # Unsupported formats are included on purpose: they are reported as failures.
            if path.is_file() and not path.name.startswith(".") and path.suffix.lower() not in IGNORED:
                name = path.stem
                if name in seen:
                    name = f"{folder.name}__{path.stem}"
                seen.add(name)
                found.append((name, path))
    return found


def run_batch(
    documents: list[tuple[str, Path]],
    out_dir: Path,
    extractor: InvoiceExtractor,
    use_cache: bool = True,
    progress: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    diag_dir = out_dir / "diagnostics"
    diag_dir.mkdir(exist_ok=True)
    records = []
    for i, (name, path) in enumerate(documents, 1):
        outcome: ExtractionOutcome = extractor.extract_file(path, use_cache=use_cache)
        (out_dir / f"{name}.json").write_text(json.dumps(outcome.public_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        full = outcome.full_json()
        full["source_file"] = path.name
        (diag_dir / f"{name}.json").write_text(json.dumps(full, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        d = outcome.diagnostics
        cost = d.original_cost_usd if d.cached else d.usage.cost_usd
        label = "FAILED " + (d.error_code or "") if not outcome.ok else (
            ("invoice" if outcome.invoice.is_invoice else "non-invoice") + (" [REVIEW]" if outcome.invoice.needs_review else "")
        )
        progress(f"[{i:>2}/{len(documents)}] {path.name:<40} {label:<28} route={d.route:<12} "
                 f"${cost or 0:.5f}{' (cached)' if d.cached else ''} {d.processing_ms} ms")
        records.append({"name": name, "path": str(path), "outcome": outcome})
    return records


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes: list[ExtractionOutcome] = [r["outcome"] for r in records]
    spent = sum(Decimal(str(o.diagnostics.usage.cost_usd)) for o in outcomes)
    return {
        "documents": len(outcomes),
        "succeeded": sum(o.ok for o in outcomes),
        "failed": sum(not o.ok for o in outcomes),
        "invoices": sum(o.ok and o.invoice.is_invoice for o in outcomes),
        "non_invoices": sum(o.ok and not o.invoice.is_invoice for o in outcomes),
        "needs_review": sum(o.public_json()["needs_review"] is True for o in outcomes),
        "cached": sum(o.diagnostics.cached for o in outcomes),
        "spent_this_run_usd": float(spent),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inputs", nargs="*", type=Path,
                        default=[PROJECT_ROOT / "data" / "labelled", PROJECT_ROOT / "data" / "unlabelled"])
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "outputs")
    parser.add_argument("--routing", choices=["auto", "ocr_first", "vision_always", "text_only"])
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--budget", type=float, help="stop calling the LLM after this many USD (default EVAL_BUDGET_USD)")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.routing:
        settings = dataclasses.replace(settings, routing_mode=args.routing)
    documents = find_documents(args.inputs)
    if not documents:
        print("No documents found in: " + ", ".join(str(p) for p in args.inputs), file=sys.stderr)
        return 1
    budget = BudgetGuard(Decimal(str(args.budget)) if args.budget is not None else settings.eval_budget_usd)
    extractor = InvoiceExtractor(settings, budget=budget)
    started = time.time()
    records = run_batch(documents, args.out, extractor, use_cache=not args.no_cache)
    summary = summarize(records) | {"elapsed_s": round(time.time() - started, 1), "routing_mode": settings.routing_mode,
                                    "model": settings.llm_model, "vision_model": settings.llm_vision_model}
    (args.out / "run_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
