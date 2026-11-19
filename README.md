# Invoice AI — LLM invoice extraction with validation and evaluation

Give it an invoice (PDF, scan or phone photo) and it returns validated JSON:

```json
{
  "is_invoice": true,
  "vendor": "Acme Supplies Pvt Ltd",
  "invoice_number": "INV-2041",
  "invoice_date": "2026-03-14",
  "currency": "INR",
  "line_items": [{ "description": "A4 paper, 500 sheets", "quantity": 10, "amount": 3200.0 }],
  "tax_total": 576.0,
  "total": 3776.0,
  "needs_review": false
}
```

Non-invoices return `"is_invoice": false` with every other field `null`. A file that cannot be
processed (corrupted, unsupported, LLM failure) returns `"is_invoice": null` and
`"needs_review": true` — a failure is never reported as a non-invoice.

| Deliverable | Where |
|---|---|
| 1. Code | This repo. Setup below works from a fresh clone. No API keys are committed (`.env` is git-ignored). |
| 2. Extractor | CLI: `python extract.py invoice.pdf > out.json` · API: `POST /api/v1/extract` |
| 3. Eval harness | `python evaluate.py` — runs the 10 labelled documents and prints the results table |
| 4. Outputs | `outputs/<document>.json` for all 20 documents (`python extract_all.py`) |
| Report | [`REPORT.md`](REPORT.md) — approach, accuracy table, failure analysis, cost per document |

---

## 1. Setup from a fresh clone

Requirements: **Python 3.11+** and a **Groq API key** (console.groq.com/keys). Tesseract OCR is
optional but recommended for scans and photos (see the note below).

**Windows (PowerShell)**
```powershell
git clone https://github.com/<you>/invoice-ai.git
cd invoice-ai
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env
```

**macOS / Linux**
```bash
git clone https://github.com/<you>/invoice-ai.git
cd invoice-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Then open `.env` and set at least:
```
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_your_key
```
(Write values without quotes. `APP_PASSWORD` and `SECRET_KEY` are only needed for the API / web app.)

Check that everything works (no API calls, no cost):
```bash
python -m pytest
```

> **Tesseract (optional).** Without it, scans and photos are still read by the vision model, but
> without an OCR text hint. Install: Windows — UB-Mannheim installer, then add
> `C:\Program Files\Tesseract-OCR` to PATH and reopen the terminal · macOS — `brew install tesseract` ·
> Ubuntu — `sudo apt install tesseract-ocr`. Check with `tesseract --version`.

> **Windows tip.** Every new terminal must show `(.venv)` at the start of the prompt before you run
> project commands. If PowerShell says scripts are disabled, run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

## 2. Configuration

| Variable | Meaning |
|---|---|
| `LLM_PROVIDER` | `groq`, `openai`, `grok` (xAI) or `custom` (any OpenAI-compatible URL) |
| `GROQ_API_KEY` / `OPENAI_API_KEY` / `XAI_API_KEY` | Key for the provider(s) you use |
| `LLM_VISION_PROVIDER` | Optional: a different provider for scans/photos (e.g. `openai`) |
| `LLM_FALLBACK_PROVIDER` | Optional: provider to retry on if the main one fails |
| `LLM_MODEL` / `LLM_VISION_MODEL` | Optional: override the default models |

Default models: Groq — `openai/gpt-oss-120b` (text) and `qwen/qwen3.8-27b` (images); OpenAI —
`gpt-4.1-mini`; Grok — `grok-4-1-fast-reasoning`. Every other option is documented in `.env.example`.

## 3. Extractor

**CLI** — prints the JSON to stdout, so it can be redirected to a file:
```bash
python extract.py invoice.pdf > out.json
python extract.py scan.jpg --full          # + review reasons, routing, tokens and cost
```
Exit code `0` = success, `2` = the document could not be processed (the JSON is still printed).

**API** (FastAPI):
```bash
python -m uvicorn backend.app.main:create_app --factory --port 8000
```
Interactive docs at http://localhost:8000/api/docs. Set `APP_PASSWORD` and `SECRET_KEY` in `.env`
first; then:
```bash
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login -H "content-type: application/json" \
  -d '{"password":"YOUR_APP_PASSWORD"}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s -H "Authorization: Bearer $TOKEN" -F "file=@invoice.pdf" localhost:8000/api/v1/extract
```

**Web app (optional)** — a React review UI (upload, side-by-side document viewer, review queue,
evaluation dashboard). Needs Node 20+: `cd frontend && npm ci && npm run build`, then start the API
above and open http://localhost:8000.

### How extraction works (short)
1. The file is validated by its content (PDF/JPEG/PNG signatures, size and page limits).
2. **Digital PDFs** → the embedded text is sent to the text model. **Scans/photos** → the page
   image is sent to the vision model, with Tesseract OCR text as a hint.
3. The model must return JSON matching a strict schema; Pydantic validates it (one repair call if not).
4. Checks: `sum(line items) + tax ≈ total` (Decimal arithmetic), required fields present, and
   key values actually present in the source text (catches invented values).
5. If a check fails, one targeted retry is made (text routes escalate to the page image).
6. `needs_review` is set from the problems that remain, each with a reason code.

Details and design reasons: [`REPORT.md`](REPORT.md) and [`reports/approach.md`](reports/approach.md).

## 4. Eval harness — one command

Put the data here (these folders are git-ignored because the data is confidential):
```text
data/labelled/       the 10 labelled documents (pdf / jpg / png)
data/unlabelled/     the 10 unlabelled documents
data/ground_truth/   one JSON per labelled document, same name: invoice_03.pdf -> invoice_03.json
```
(A single combined ground-truth file also works: `python evaluate.py --ground-truth path/to/labels.json`.
JSON files placed next to the documents in `data/labelled/` are found automatically.)

Run:
```bash
python evaluate.py
```

It extracts each labelled document (results are cached, so re-runs cost nothing), compares every
field with the ground truth, and prints:

```text
FIELD-LEVEL ACCURACY  (10 labelled docs: ...)
field           scored on     correct  accuracy  wrong  missing  extra  failed
is_invoice      all docs         .../10       ...
vendor          invoices          ...
...
PER DOCUMENT
document          truth    fields  exact  flagged  wrong fields
...
SUMMARY
Full-document exact match, field accuracy, classification P/R/F1, line-item P/R/F1,
review-flag effectiveness, average time and cost per document
```

The same results are written to `reports/evaluation.md`, `evaluation.csv`, `evaluation.json`,
`failure_analysis.md` and `cost_analysis.md`. Other options:

| Command | Purpose |
|---|---|
| `python evaluate.py --score-only` | Re-score saved outputs without calling the LLM |
| `python evaluate.py --tag baseline` | Save a snapshot before changing the prompt (later runs show the difference) |
| `python evaluate.py --tag ocr_first --routing ocr_first` | Measure an alternative routing strategy |

### Matching rules

Rules forgive formatting, not meaning:

| Field | Rule |
|---|---|
| Numbers (`tax_total`, `total`, amounts) | Parsed as numbers: `"1,200.00"` = `1200` = `"1,200"`; equal within ±0.01 |
| `vendor` | Ignore case, punctuation, extra spaces, `&` vs `and`, and trailing legal-form words (Pvt, Ltd, Limited, Inc, LLC, Co, Corp, GmbH…): `"Acme Pvt Ltd"` = `"Acme"`, but `"Acme Supplies"` ≠ `"Acme Traders"` |
| `invoice_number` | Ignore spaces and case only: `INV-2041` ≠ `INV2041` (a hyphen can be meaningful) |
| `invoice_date` | Both sides converted to a calendar date: `14/03/2026` = `2026-03-14` |
| `currency` | ISO code after mapping unambiguous symbols: `₹` = `INR`, `€` = `EUR` |
| `line_items` | Order ignored; one-to-one matching. A row matches only if description similarity ≥ 0.85, quantity is equal and amount is within ±0.01. The field is correct only if every row matches |
| `is_invoice` | Exact; a processing failure always counts as wrong |
| Empty values | `null` vs `null` is correct; `null` vs value = *missing*; value vs `null` = *extra* |

Invoice fields are scored only on documents whose ground truth is an invoice, so non-invoices
cannot add free correct `null`s.

## 5. Outputs for all 20 documents

```bash
python extract_all.py
```

Writes, for every document in `data/labelled/` and `data/unlabelled/`:
- `outputs/<document>.json` — the 9 required fields, exactly as specified;
- `outputs/diagnostics/<document>.json` — review reasons, validation results, model, tokens, cost;
- `outputs/run_summary.json` — totals for the run.

Then commit the `outputs/` folder (it contains no keys or ground truth).

## 6. Repository layout

```text
extract.py, evaluate.py, extract_all.py   one-line entry points for the deliverables
backend/app/
  services/          file validation, PDF/OCR, routing, LLM client, validation, review flag, cost
  api/               FastAPI routes (+ simple password auth)
  cli/               extract, extract_all
  schemas/           Pydantic models for the required JSON and diagnostics
backend/tests/       pytest suite (mocked LLM, no API cost)
evaluation/          matching rules, metrics, failure analysis, report writer
frontend/            optional React review app
outputs/             JSON results for the 20 documents
reports/             generated evaluation, failure and cost reports + approach.md
REPORT.md            the written report
```

## 7. Notes

- **No secrets in git:** `.env`, the dataset folders and the virtual environment are git-ignored.
  Check with `git status` before every push.
- **Cost control:** results are cached by file content + model + prompt version, and evaluation
  stops calling the LLM after `EVAL_BUDGET_USD` (default $2).
- **Docker (optional):** `docker build -t invoice-ai .` then
  `docker run -p 8000:8000 --env-file .env -v invoice-data:/app/data invoice-ai` serves the API and
  web app on http://localhost:8000 with Tesseract included.
- **Limitations:** ten labelled documents give wide error bars (one document = 10 percentage
  points); only the first 10 pages of a PDF are processed; numeric dates rely on the document's
  locale (ambiguous ones are flagged).
