# Report — AI invoice extraction

> **Before submitting:** replace the few remaining `⟨fill⟩` markers (search for them) using
> `reports/failure_analysis.md`, `reports/cost_analysis_all.md` and `outputs/diagnostics/L07.json`,
> then delete this note.

---

## 1. Approach

### Models used
| Input | Provider / model | Why |
|---|---|---|
| Digital PDFs (embedded text) | Groq `openai/gpt-oss-120b` | Cheap, fast, supports strict JSON-schema output |
| Scans and photos (page image + OCR hint) | Groq `qwen/qwen3.8-27b` | Multimodal model that can read the page image |

Routing mode `auto`, prompt `v1`, temperature 0, one model call per attempt (no ensembles).
Across all 20 documents, 15 were digital PDFs (text route) and 5 were scans/photos (vision route:
L02.jpg, L09.pdf, L10.pdf, U02.jpg, U05.pdf).

### Pipeline
1. **Validate the file** by its content (PDF/JPEG/PNG signatures), size and page limits.
   Corrupted, encrypted or unsupported files fail with a specific error code — never "not an invoice".
2. **Route by document type.**
   - Digital PDF → the embedded text (PyMuPDF) is sent to the text model. The text is exact, so
     images would only add cost and digit-misreading risk.
   - Scan / photo / image → the page image is sent to the vision model, with Tesseract OCR text
     (after grayscale, upscaling, contrast and automatic rotation) as a hint. OCR confidence does not
     say whether a "3" is really an "8", so the image is the source of truth.
3. **Structured output.** The model must return JSON matching a strict schema; Pydantic validates
   it and one repair call is made if it is invalid.
4. **Checks that never change the numbers.**
   - Arithmetic: `sum(line amounts) + tax ≈ total` in Decimal (tolerance 0.05 + 0.01 per line).
     Printed discounts / shipping / round-off can *explain* a gap, but values are never "fixed".
   - Completeness: vendor, invoice number, date, currency, total and line items present.
   - Grounding: for digital PDFs, the invoice number, total and vendor must appear in the text the
     model saw — otherwise the value was invented.
5. **One targeted retry** when a check that a second look could fix fails; text routes escalate to
   the page image. The attempt with fewer problems is kept; disagreement between attempts is flagged.
6. **`needs_review`** is set from the remaining observable problems, each with a reason code
   (e.g. `TOTAL_MISMATCH`, `MISSING_INVOICE_DATE`, `LLM_RESULT_CONFLICT`). No invented confidence scores.

### Prompting approach
One system prompt (`backend/app/services/prompts.py`, version `v1`), written **before** looking at
any ground truth, in three steps:

1. **Classify** — `document_type` plus `is_invoice`, with explicit lists of what is and is not an
   invoice (purchase orders, quotations, proforma invoices, statements, credit notes are not).
2. **Extract** — "copy values printed in the document; never invent, guess or fix; null if absent".
   Field rules target known traps: vendor = seller, not "Bill To"; invoice number ≠ PO / order /
   GSTIN / customer ID; invoice date ≠ due date, resolved with the document's locale (DD/MM vs
   MM/DD); split taxes (CGST + SGST) are added; total = grand total, not amount paid or balance.
3. **Report problems** — a fixed list of issue codes (illegible text, partial document, ambiguous
   total…) so the model can say "I'm not sure" instead of guessing.

The model also returns evidence fields that are **not** part of the required JSON (date as printed,
unit prices, subtotal, discount, other charges). They are only used by the checks above.
Every later prompt change is logged in `reports/prompt_changelog.md`.

### One thing that did not work
**Strict JSON-schema output with Groq's vision model.** The first version always asked for strict
`json_schema` output. On a test invoice image, Groq's vision model (`qwen/qwen3.8-27b`) produced
JSON that broke the schema, and Groq rejected the whole request with HTTP 400 `json_validate_failed`.
The document failed with 0 tokens returned, even though the model had read the page.
**Fix:** when a model fails strict generation, the client now resends once in plain JSON mode with
the schema written into the prompt. The app's own Pydantic validation and repair call then do the
checking. This is per model, so the text model keeps strict schemas.

A second, smaller one: the first review rule treated "OCR could not run" (Tesseract not installed)
as "OCR quality is poor", which flagged a correctly extracted invoice for review. Missing OCR is now
recorded as a setup note instead of a review reason.

---

## Outputs (all 20 documents)

`python extract_all.py` wrote one JSON per document to `outputs/` (`L01.json` … `L10.json`,
`U01.json` … `U10.json`), with full diagnostics in `outputs/diagnostics/` and run totals in
`outputs/run_summary.json`.

| | Count | Documents |
|---|---|---|
| Processed successfully | 20 / 20 | no processing failures |
| Classified as invoice | 17 | |
| Classified as non-invoice | 3 | L08, U06, U10 |
| Flagged `needs_review` | 2 | L07 (labelled, missing line item; see §3), U02 (unlabelled photo) |

The unlabelled documents have no ground truth, so they are not scored. U02's flag means a check
failed on that photo, and a person should confirm it in the review queue before it is trusted.
⟨optional — copy U02's `review_reasons` from `outputs/diagnostics/U02.json`⟩

---

## 2. Accuracy

From `python evaluate.py` on the 10 labelled documents (9 invoices, 1 non-invoice).
Invoice fields are scored only on documents whose ground truth is an invoice. A processing failure
counts as wrong.

| Field | Correct / N | Accuracy |
|---|---|---|
| vendor | 9/9 | 100.0% |
| invoice number | 9/9 | 100.0% |
| date | 9/9 | 100.0% |
| currency | 9/9 | 100.0% |
| line items | 8/9 | 88.9% |
| tax | 9/9 | 100.0% |
| total | 9/9 | 100.0% |
| is_invoice (all 10 documents) | 10/10 | 100.0% |

| Summary | Value |
|---|---|
| Documents with every field correct | 9 / 10 (90.0%) |
| Field accuracy on invoices (8 fields × 9 invoices) | 98.6% |
| Invoice classification precision / recall / F1 | 100% / 100% / 100% |
| Line items: row precision / recall / F1 | 100.0% / 98.5% / 99.2% |
| Review flag: errors caught / missed / correct-but-flagged | 1 / 0 / 0 |

**Matching rules** (full table in `README.md`, section 4): numbers compared as numbers
(`"1,200.00"` = `1200`, ±0.01); vendor ignores case, punctuation and trailing legal words
(`"Acme Pvt Ltd"` = `"Acme"`, but `"Acme Supplies"` ≠ `"Acme Traders"`); invoice number ignores only
spaces and case; dates compared as calendar dates; line items matched one-to-one in any order
(description ≥ 85 % similar, equal quantity, amount ±0.01).

With 10 documents, one document changes a field's accuracy by 10 percentage points, so these
numbers show the pipeline works on this set; they are not a precise accuracy estimate.
The review-flag result (1 caught, 0 missed) rests on a single error, so it is encouraging but
not yet strong evidence.

---

## 3. Failure analysis

Source: `reports/failure_analysis.md`, which lists every wrong field with expected value, predicted
value and likely cause.

### Errors grouped by cause
Only **one** field was wrong across the 10 labelled documents:

| Cause | Errors | Documents affected |
|---|---|---|
| Missing line items | 1 | 1 (L07) |

Every other field (vendor, invoice number, date, currency, tax, total, is_invoice) was correct on
every document, including the 3 scanned/photographed ones and the non-invoice (L08).

### What went wrong on L07
- **The error:** row precision is 100% but row recall is 98.5%, so every line item the model returned
  was correct, but **one row printed on the invoice was not extracted**. The vendor, invoice number,
  date, tax and total on L07 were all correct.
- **It was caught.** With one row missing, the line items no longer add up to the printed total, so
  the arithmetic check failed. That triggered the automatic retry with the page image (which is why
  L07 is the most expensive document, $0.00466). The retry still didn't recover the row, so L07 was
  flagged `needs_review` and went to the review queue instead of being passed as correct.
- **Review reasons recorded:** ⟨fill — copy `review_reasons` from `outputs/diagnostics/L07.json`⟩

| Document | Field | Expected | Predicted | Cause (after checking the document) |
|---|---|---|---|---|
| L07 | line_items | ⟨fill — the missing row from `reports/failure_analysis.md`⟩ | not extracted | ⟨fill — what is special about that row in the PDF, e.g. wrapped description, row split across a page, row below a sub-total⟩ |

### What I would fix first
**Line-item completeness on longer tables.** It is the only error type seen, and the fix is
measurable:
1. **Make the retry say how much is missing.** The retry feedback already says the totals don't add
   up. Adding "your rows sum to X but the printed subtotal is Y, so rows worth Y − X are missing"
   points the model at exactly what to look for.
2. **Count rows.** Ask the model for the number of item rows (or the last serial number) printed in
   the table, and treat "fewer rows extracted than counted" as a retry trigger.
3. **Check it with the harness.** Run `python evaluate.py --score-only` against the saved baseline
   (`reports/experiments/baseline/`) to confirm line-item recall improves and nothing else regresses.

---

## 4. Cost per document

### Result
Per-document costs below are from the `extract_all.py` run (exact values: `reports/cost_analysis_all.md`).

| Metric | All 20 documents | 10 labelled | 10 unlabelled |
|---|---|---|---|
| **Average cost per document** | **$0.00192** | $0.00217 | $0.00168 |
| Total | $0.0384 | $0.0217 | $0.0168 |

| By input type | Documents | Average cost |
|---|---|---|
| Digital PDF → text model (`gpt-oss-120b`) | 15 | $0.00105 |
| Scan / photo → vision model (`qwen3.8-27b`) | 5 | $0.00454 (≈ 4× a digital PDF) |

| Other metrics | Value |
|---|---|
| Average tokens in / out per document | ⟨fill⟩ / ⟨fill⟩ (from `reports/cost_analysis_all.md`) |
| Cheapest document | $0.00041 (L08, non-invoice) |
| Most expensive documents | $0.00741 (U02, photo, flagged) and $0.00466 (L07, flagged) |
| Spent on retries and repairs | ⟨fill — "Retry / repair attempts" row of `reports/cost_analysis_all.md`⟩ |
| Average processing time | 13.1 s per labelled document (first run, before caching) |
| Projected cost for 1,000 similar invoices | about $1.92 in LLM charges |

The two flagged documents are the two most expensive. That fits the design: a failed check
triggers one extra model call with the page image, which adds a vision call to the cost (L07 cost
about 4× an average digital PDF). The `attempts` list in `outputs/diagnostics/L07.json` and
`U02.json` shows every call made and its cost.

### How it was calculated
- **Tokens are the provider's own counts.** Every API response includes `usage`: prompt tokens
  (including cached and image tokens) and completion tokens. They are recorded for **every** HTTP
  call, including repair calls, validation retries and transport retries.
- **Each call is priced with the rates of the model that served it:**
  ```
  cost = (prompt_tokens − cached_tokens) × input_rate
       + cached_tokens × cached_input_rate
       + completion_tokens × output_rate          (rates in USD per 1M tokens)
  ```
  Reasoning tokens are billed as output. They are counted once, whether the provider reports them
  inside or separately from `completion_tokens`.
- **Rates used** (USD per 1M tokens — verify on the provider's pricing page; overridable with
  `MODEL_PRICING_JSON`):

  | Model | Input | Cached input | Output |
  |---|---|---|---|
  | `openai/gpt-oss-120b` (Groq) | 0.15 | 0.075 | 0.60 |
  | `qwen/qwen3.8-27b` (Groq, preview) | 0.80 | 0.80* | 4.00 |
  | `gpt-4.1-mini` (OpenAI) | 0.40 | 0.10 | 1.60 |

  \* No cached rate is published for this model, so cached tokens are priced at the full input rate.
- **Worked example** (a sample invoice image during development, not an assignment document):
  2,815 tokens in and 358 out on `qwen/qwen3.8-27b` →
  2,815 × 0.80 / 1M + 358 × 4.00 / 1M = 0.00225 + 0.00143 = **$0.0037**.
- **Caching:** results are cached by file content + model + prompt version. A re-run costs $0, and
  reports still show each document's original cost.
- **Scope:** these are LLM charges only. Hosting costs are separate.
