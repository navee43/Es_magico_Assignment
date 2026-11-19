"""Extract one document and print the invoice JSON to stdout.

    python -m backend.app.cli.extract invoice.pdf
    python -m backend.app.cli.extract invoice.pdf --full        # include review reasons + diagnostics
    python -m backend.app.cli.extract scan.jpg --routing vision_always --no-cache

Exit code: 0 success, 2 processing failure, 1 usage error.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from backend.app.config import get_settings
from backend.app.services.extractor import InvoiceExtractor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract structured invoice data from a PDF/JPG/PNG.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--full", action="store_true", help="also print review reasons and diagnostics")
    parser.add_argument("--routing", choices=["auto", "ocr_first", "vision_always", "text_only"])
    parser.add_argument("--no-cache", action="store_true", help="always call the LLM")
    args = parser.parse_args(argv)
    # Windows consoles/redirects default to cp1252, which can't encode ₹ and similar characters.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    if not args.path.is_file():
        print(f"error: {args.path} is not a file", file=sys.stderr)
        return 1
    settings = get_settings()
    if args.routing:
        settings = dataclasses.replace(settings, routing_mode=args.routing)
    outcome = InvoiceExtractor(settings).extract_file(args.path, use_cache=not args.no_cache)
    payload = outcome.full_json() if args.full else outcome.public_json()
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if not outcome.ok:
        d = outcome.diagnostics
        print(f"processing failed: {d.error_code}: {d.error_message}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
