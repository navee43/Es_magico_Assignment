# Prompt changelog

The labelled set has only ~10 documents, so every prompt change made after looking at
ground truth or failures is logged here, with the evaluation tag that measured it.
`PROMPT_VERSION` lives in `backend/app/services/prompts.py`; bump it with every change
(it is part of the cache key and is printed in every report).

| Version | Date | Written after seeing ground truth? | Change | Measured by |
|---|---|---|---|---|
| v1 | 2026-11-19 | No — written before any assignment document or label was available | Initial prompt: classification rules, seller-vs-buyer vendor rule, invoice-number negatives (PO/GSTIN/customer id), locale-aware dates, tax aggregation, "null if not printed", self-reported issue codes | `make eval-baseline` (run this first) |

## Rules for future changes
1. Run `make eval-baseline` before any change, so the untuned result is preserved.
2. Generalise from failure *categories*; never paste a document's actual values into the prompt.
3. Bump `PROMPT_VERSION`, add a row above, run `make eval`, and keep the comparison table that
   `evaluation.md` prints against the baseline.
