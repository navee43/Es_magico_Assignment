# Failure analysis


| Setting | Value |
|---|---|
| Generated | 2026-11-19T19:27:54+00:00 |
| Run tag | baseline |
| Documents evaluated | 10 |
| LLM provider / model | groq / openai/gpt-oss-120b (vision: qwen/qwen3.8-27b) |
| Routing mode | auto |
| Prompt version | v1 (fingerprint `f6ffe9b7209e`) |
| Pipeline version | 1.1.0 |
| Validation retry | True |
| Ground truth | data\ground_truth |

## Errors by cause

| Cause | Errors | Documents affected |
|---|---|---|
| Missing line items | 1 | 1 |

## Highest-priority improvement

**Missing line items** — 1 error(s) across 1 document(s) (ranked by number of labelled documents affected, then by error count).

Proposed fix: Send page images for multi-line tables, raise max output tokens, and use the printed subtotal check to trigger a retry.


## Every observed error


### Missing line items

| Document | Field | Expected | Predicted | Likely reason | Proposed fix |
|---|---|---|---|---|---|
| L07 | line_items | [{"description": "Brand identity package", "quantity": 1, "… | [{"description": "Brand identity package", "quantity": 1, "… | 3 of 4 rows predicted; 3/4 expected rows matched; 3 predicted (route=digital_text) | Send page images for multi-line tables, raise max output tokens, and use the printed subtotal check to trigger a retry. |

## Approach and experiments

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


**Runs actually executed (from `reports/experiments/index.json`):**

| Tag | When | Routing | Prompt | Exact match | Field acc. | Line-item F1 | Avg cost |
|---|---|---|---|---|---|---|---|
| main | 2026-11-19T19:24:02+00:00 | auto | v1 | 90.0% | 98.6% | 99.2% | $0.00217 |
| baseline | 2026-11-19T19:27:54+00:00 | auto | v1 | 90.0% | 98.6% | 99.2% | $0.00217 |

Interpretation of what did or did not work should be written by hand after reading the table above; with ~10 labelled documents, a difference of one document is 10 percentage points.

