"""Cost report over every document in an outputs folder (e.g. all 20 after `make extract-all`).

    python -m evaluation.cost_report --outputs outputs --out reports/cost_analysis_all.md
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from backend.app.config import PIPELINE_VERSION, PROJECT_ROOT, get_settings
from backend.app.services import prompts
from backend.app.services.cost_tracker import get_pricing
from evaluation.report import cost_summary, write_cost_analysis


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs", type=Path, default=PROJECT_ROOT / "outputs")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "reports" / "cost_analysis_all.md")
    args = parser.parse_args(argv)
    diags = [json.loads(p.read_text(encoding="utf-8"))["diagnostics"] for p in sorted((args.outputs / "diagnostics").glob("*.json"))]
    if not diags:
        print(f"No diagnostics found in {args.outputs / 'diagnostics'}; run `make extract-all` first.")
        return 1
    settings = get_settings()
    models = sorted({a["model"] for d in diags for a in d.get("attempts", []) if a.get("model")}) or [settings.llm_model]
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tag": "all-outputs",
        "documents": len(diags), "llm_provider": settings.llm_provider, "model": settings.llm_model,
        "vision_model": settings.llm_vision_model, "routing_mode": settings.routing_mode,
        "prompt_version": prompts.PROMPT_VERSION, "prompt_fingerprint": prompts.prompt_fingerprint(),
        "pipeline_version": PIPELINE_VERSION, "validation_retry": settings.validation_retry,
        "ground_truth": "n/a (cost only)",
    }
    write_cost_analysis(args.out.parent, meta, cost_summary(diags),
                        {m: get_pricing(m, settings).as_dict() for m in models}, float(settings.eval_budget_usd),
                        filename=args.out.name)
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
