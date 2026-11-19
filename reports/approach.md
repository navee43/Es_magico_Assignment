# Approach and design decisions

This file explains *why* the pipeline looks the way it does. Measured results live in the
generated reports (`evaluation.md`, `failure_analysis.md`, `cost_analysis.md`).

## 1. One LLM call per document, chosen by what the document actually is

| Document | What the model receives | Why |
|---|---|---|
| Digital PDF (every page has a usable text layer) | Embedded text from PyMuPDF only (`digital_text`) | The text is exact. Images would add cost and a chance to misread digits. |
| Scanned PDF, photo, image, mixed PDF, or a PDF whose text layer sits on a full-page scan | Page image(s) **plus** Tesseract OCR text as a hint (`vision`) | OCR confidence is a weak signal for digit-level correctness, and photos break table layout. The image is the source of truth; OCR helps with small print. |
| Corrupted / encrypted / unsupported / empty | Nothing — the request fails with a specific error code | A failure is never reported as a "non-invoice". |

Alternative routing modes exist so they can be *measured* rather than argued about:

* `ocr_first` — scanned documents with good OCR (mean confidence ≥ 80, ≥ 30 words) are sent as
  OCR text only; images are used only when OCR looks unreliable. Cheapest option.
* `vision_always` — always send page images (with any text as a hint).
* `text_only` — never send images.

Run `make eval-experiment ROUTING=ocr_first TAG=ocr_first` to compare a mode against the saved
baseline. Results only appear in `failure_analysis.md` once that run has actually happened.

Only one model is called per attempt. The text and vision model names are configurable
separately (`LLM_MODEL`, `LLM_VISION_MODEL`), so a cheaper model can handle the digital PDFs.

## 2. The model returns more than the schema needs — the extras are evidence, not output

The LLM is asked for the nine required fields **plus** `document_type`, `invoice_date_as_printed`,
`unit_price` per line, `subtotal`, `discount_total`, `other_charges_total` and a list of
self-reported `issues`. These never appear in the required JSON. They are used to:

* explain arithmetic gaps (discounts, shipping, round-off) instead of silently "fixing" numbers;
* detect day/month confusion (ISO date vs. the date as printed);
* flag borderline document types (receipt, proforma, credit note);
* let the model say "I couldn't read this" instead of guessing.

Structured output uses a strict JSON schema (`response_format: json_schema`), falling back to
JSON mode automatically if a provider rejects it. Temperature is 0.

## 3. Validation never edits numbers

1. **Schema** — Pydantic validates types, ISO dates, ISO-4217 currency codes and the
   "non-invoice ⇒ all fields null" rule. One repair call is allowed if the JSON is invalid.
2. **Arithmetic** — `sum(line amounts) + tax ≈ total` in `Decimal`, tolerance
   `0.05 + 0.01 × lines`. If it fails, printed discounts/charges or tax-inclusive pricing may
   *explain* it (status `pass_with_adjustments`), otherwise it is `TOTAL_MISMATCH`.
3. **Completeness** — vendor, invoice number, date, currency, total, line items.
4. **Grounding (hallucination guard)** — for embedded-text documents the model saw only that
   text, so an invoice number, total or vendor that is not in it must have been invented →
   review. For OCR/vision documents a miss is only recorded (OCR text is noisy).

## 4. One targeted retry

If a check that a second look could fix fails (mismatch, missing key field, ungrounded value,
invalid date), the document is extracted once more with the specific problems listed — and text
routes escalate to the page image (e.g. a vendor that only appears in a logo). The prompt tells
the model **not** to change numbers just to make them add up. The attempt with fewer remaining
problems is kept; if the two attempts disagree and problems remain, `LLM_RESULT_CONFLICT` is
added. HTTP timeouts / 429 / 5xx are retried separately (2 retries with backoff).

## 5. needs_review from observable signals only

No invented confidence percentages. A document is flagged when any of these is true: a required
field is missing, totals don't reconcile, a value isn't found in exact source text, the date is
invalid/ambiguous/inconsistent, OCR confidence is very low, the document type is borderline, the
model reported illegible/partial/multiple invoices, two attempts disagree, only some pages were
processed, or processing failed. Each reason has a code and a human message; the evaluation
harness measures the flag's precision and recall against the labelled set.

## 6. Cost

Every HTTP call's `usage` (prompt, cached prompt and completion tokens) is priced with
configurable per-1M-token rates, including repairs and retries. Results are cached by
file hash + model + routing + prompt version, so re-running evaluation costs nothing; cached
documents report $0 spent now and keep their original cost for reporting. Evaluation and
`extract-all` stop calling the model when `EVAL_BUDGET_USD` is reached.

## 7. Deployment

One Docker image (React build + FastAPI + Tesseract), one EC2 instance, SQLite + files on a
named volume, optional Caddy on the host for HTTPS. One uvicorn worker; extraction runs in the
thread pool with at most `MAX_CONCURRENT_EXTRACTIONS` documents at a time, so a slow document
never blocks other requests.
