# Invoice AI: reads invoices automatically

## What is this?

Businesses receive invoices as PDFs, scanned paper, or phone photos. Someone usually has to type
the important details into a spreadsheet or accounting system by hand: who sent the invoice, its
number, the date, what was bought and the total.

**This tool does that typing automatically.** You give it an invoice file, and it uses AI to read
it and write out the key details in a tidy, standard format. It also **double-checks its own work**
and raises a flag when something doesn't add up, so a person only needs to look at the invoices
that really need attention.

It handles:
- clean digital PDFs (made by accounting software),
- scanned paper invoices,
- photos taken with a phone,
- documents that are **not** invoices (it says "this is not an invoice" instead of making things up),
- damaged or unreadable files (it reports the problem instead of guessing).

---

## What you get back

For every invoice, the tool produces a small structured record like this (the format is called
JSON, a common way for computers to exchange data):

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

| Field | What it means |
|---|---|
| `is_invoice` | Is this document actually an invoice? (`true` / `false`) |
| `vendor` | The company that sent the invoice and should be paid |
| `invoice_number` | The invoice's reference number |
| `invoice_date` | The date on the invoice, always written as year-month-day |
| `currency` | The money used, as a standard 3-letter code (INR = Indian rupee, USD = US dollar…) |
| `line_items` | Each thing that was bought: description, quantity and amount |
| `tax_total` | Total tax on the invoice (for example CGST + SGST together) |
| `total` | The final amount to be paid |
| `needs_review` | `true` means "a person should check this one"; `false` means all checks passed |

If the document is not an invoice, every field except `is_invoice` is left empty (`null`).
If the file can't be read at all, the record says so (`is_invoice` is `null`, `needs_review` is
`true`); it is never wrongly labelled "not an invoice".

---

## How it works (in plain words)

1. **Check the file.** Is it really a PDF or an image? Is it damaged or too large?
2. **Get the text.**
   - If the PDF was made on a computer, the text is copied straight out of it. This is exact.
   - If it's a scan or a photo, a text-recognition program (OCR) reads the picture, and the
     picture itself is also shown to the AI.
3. **Ask the AI** to fill in the fields above, following strict rules: copy what is printed,
   never guess, and leave a field empty if it isn't there.
4. **Double-check the answer:**
   - Do the line items plus tax add up to the total?
   - Are the important fields present?
   - Does the invoice number actually appear in the document? (This catches made-up values.)
5. **If a check fails**, the AI gets one more try, this time looking at the page image.
6. **Flag anything still uncertain** (`needs_review: true`) and say why, for example "totals don't
   add up" or "date missing".

---

## Results at a glance

Tested on 10 invoices where the correct answers were known in advance:

| Result | Value |
|---|---|
| Documents read completely correctly | **9 out of 10** |
| Vendor, invoice number, date, currency, tax, total | **correct on every invoice** |
| Correctly spotted the document that was not an invoice | **yes** |
| The one mistake (a single missed line on one invoice) | **was caught and flagged for a person to check** |
| AI cost per document (all 20 documents) | **about $0.002**, roughly $2 per 1,000 invoices |

Full details, including how accuracy was measured and what went wrong, are in
[`REPORT.md`](REPORT.md).

---

## How to run it yourself (step by step)

You don't need to know how to program. You will copy and paste a few commands.

### Words you'll see
| Word | Meaning |
|---|---|
| **Terminal** | A window where you type commands. On Windows: press Start, type **PowerShell**, press Enter. On a Mac: open **Terminal** from Applications → Utilities. |
| **Repository (repo)** | The project folder, stored on GitHub. |
| **API key** | A private password that lets this tool use an AI service. Never share it or put it on GitHub. |
| **Virtual environment** | A private space for this project's software, so it doesn't interfere with anything else on your computer. |

### What you need first (one-time)
1. **Python 3.11 or newer.** Download from python.org. On Windows, tick **"Add python.exe to PATH"**
   on the first installer screen.
2. **Git.** Download from git-scm.com and install with the default options.
3. **A Groq API key.** Create a free account at console.groq.com, open **API Keys**, click
   **Create API Key**, and copy it somewhere safe.
4. *(Optional, improves results on scans and photos)* **Tesseract OCR**:
   - Windows: install the "UB-Mannheim" Tesseract installer, then add
     `C:\Program Files\Tesseract-OCR` to your PATH (Start → "environment variables" → Path → Edit → New).
   - Mac: `brew install tesseract`.
   - Check it worked by typing `tesseract --version` in a new terminal.

### Step 1: download the project
Open a terminal and run:
```
git clone https://github.com/navee43/Es_magico_Assignment.git
cd Es_magico_Assignment
```

### Step 2: set up the project's software (one-time, about 2 minutes)

**Windows (PowerShell):**
```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```
If you see "running scripts is disabled", run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`,
type `Y`, and try the second line again.

**Mac / Linux:**
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

When this works, the start of your terminal line shows **`(.venv)`**. Whenever you open a new
terminal later, run the second line again (`.venv\Scripts\Activate.ps1` or `source .venv/bin/activate`)
until you see `(.venv)`.

### Step 3: add your API key
1. Make a settings file from the example: `copy .env.example .env` (Windows) or
   `cp .env.example .env` (Mac/Linux).
2. Open the new `.env` file in any text editor (Notepad is fine).
3. Find these two lines and fill them in, **without quotes**:
   ```
   LLM_PROVIDER=groq
   GROQ_API_KEY=gsk_paste_your_key_here
   ```
4. Save the file. It stays on your computer only and is never uploaded to GitHub.

### Step 4: check everything is installed correctly
```
python -m pytest
```
You should see a line ending in **"passed"**. This test uses no AI and costs nothing.

### Step 5: read an invoice
```
python extract.py path/to/your-invoice.pdf
```
The result appears on screen. To save it to a file instead:
```
python extract.py path/to/your-invoice.pdf > result.json
```
If the file name or folder contains spaces, put the path in quotes: `"C:\My Files\invoice 1.pdf"`.

### Step 6: measure accuracy (needs the test documents)
Put the files in these folders inside the project:
```
data/labelled/       the 10 test invoices with known answers
data/ground_truth/   the known answers, one file per invoice with the same name
                     (e.g. invoice_03.pdf  →  invoice_03.json)
data/unlabelled/     the other 10 invoices
```
Then run:
```
python evaluate.py
```
It reads the 10 test invoices, compares every field with the known answers, and prints a table
showing what it got right and wrong. A copy is saved in the `reports/` folder.

### Step 7: produce the results for all 20 documents
```
python extract_all.py
```
One result file per document is saved in the `outputs/` folder.

### Running it again is free
Each document's result is remembered after the first run, so repeating Step 6 or Step 7 doesn't
use the AI again and costs nothing.

### If something goes wrong
| What you see | What to do |
|---|---|
| `No module named ...` | The project's software isn't active. Run the activate line from Step 2 until you see `(.venv)` |
| `python is not recognized` | Reinstall Python and tick "Add python.exe to PATH" |
| An error mentioning the API key | Check `.env`: the key must be on the `GROQ_API_KEY=` line with no quotes or spaces |
| `No ground truth found` | The answer files' names must match the invoice names exactly (`L01.pdf` → `L01.json`) |

---

## For developers

### Deliverables
| Deliverable | Where |
|---|---|
| Code | This repo. No API keys are committed (`.env` is git-ignored). |
| Extractor | CLI: `python extract.py invoice.pdf > out.json` (exit code `0` success, `2` could not be processed) · API: `POST /api/v1/extract` |
| Eval harness | `python evaluate.py`: runs the 10 labelled documents and prints the results table |
| Outputs | `outputs/<document>.json` for all 20 documents (`python extract_all.py`) |
| Report | [`REPORT.md`](REPORT.md): approach, accuracy, failure analysis, cost per document |

### Configuration
| Variable | Meaning |
|---|---|
| `LLM_PROVIDER` | `groq`, `openai`, `grok` (xAI) or `custom` (any OpenAI-compatible URL) |
| `GROQ_API_KEY` / `OPENAI_API_KEY` / `XAI_API_KEY` | Key for the provider(s) used |
| `LLM_VISION_PROVIDER` | Optional: a different provider for scans/photos |
| `LLM_FALLBACK_PROVIDER` | Optional: provider to retry on if the main one fails |
| `LLM_MODEL` / `LLM_VISION_MODEL` | Optional: override the default models |

Default models: Groq `openai/gpt-oss-120b` (text) and `qwen/qwen3.8-27b` (images); OpenAI
`gpt-4.1-mini`; Grok `grok-4-1-fast-reasoning`. All options are documented in `.env.example`.

### API
```bash
python -m uvicorn backend.app.main:create_app --factory --port 8000
```
Docs at http://localhost:8000/api/docs. Set `APP_PASSWORD` and `SECRET_KEY` in `.env`, then:
```bash
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login -H "content-type: application/json" \
  -d '{"password":"YOUR_APP_PASSWORD"}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s -H "Authorization: Bearer $TOKEN" -F "file=@invoice.pdf" localhost:8000/api/v1/extract
```
Optional web app (upload, side-by-side viewer, review queue, evaluation dashboard; needs Node 20+):
`cd frontend && npm ci && npm run build`, then start the API and open http://localhost:8000.

### Evaluation options
| Command | Purpose |
|---|---|
| `python evaluate.py --score-only` | Re-score saved outputs without calling the LLM |
| `python evaluate.py --tag baseline` | Save a snapshot before changing the prompt |
| `python evaluate.py --tag ocr_first --routing ocr_first` | Measure an alternative routing strategy |
| `python evaluate.py --ground-truth path/to/labels.json` | Use a single combined ground-truth file |

Reports are written to `reports/evaluation.md`, `evaluation.csv`, `evaluation.json`,
`failure_analysis.md`, `cost_analysis.md` (and `cost_analysis_all.md` for all 20 documents via
`python -m evaluation.cost_report --outputs outputs --out reports/cost_analysis_all.md`).

### Matching rules used by the evaluation
Rules forgive formatting, not meaning:

| Field | Rule |
|---|---|
| Numbers (`tax_total`, `total`, amounts) | Compared as numbers: `"1,200.00"` = `1200` = `"1,200"`; equal within ±0.01 |
| `vendor` | Ignore case, punctuation, extra spaces, `&` vs `and`, and trailing legal words (Pvt, Ltd, Limited, Inc, LLC, Co, Corp, GmbH…): `"Acme Pvt Ltd"` = `"Acme"`, but `"Acme Supplies"` ≠ `"Acme Traders"` |
| `invoice_number` | Ignore spaces and case only: `INV-2041` ≠ `INV2041` |
| `invoice_date` | Compared as calendar dates: `14/03/2026` = `2026-03-14` |
| `currency` | ISO code after mapping unambiguous symbols: `₹` = `INR`, `€` = `EUR` |
| `line_items` | Order ignored; one-to-one matching; a row matches only if description similarity ≥ 0.85, quantity equal and amount within ±0.01. Correct only if every row matches |
| `is_invoice` | Exact; a processing failure always counts as wrong |
| Empty values | `null` vs `null` is correct; `null` vs value = *missing*; value vs `null` = *extra* |

Invoice fields are scored only on documents whose ground truth is an invoice, so non-invoices
cannot add free correct `null`s.

### Repository layout
```text
extract.py, evaluate.py, extract_all.py   entry points for the deliverables
backend/app/services/   file validation, PDF/OCR, routing, LLM client, checks, review flag, cost
backend/app/api/        FastAPI routes (+ simple password auth)
backend/app/cli/        extract, extract_all
backend/app/schemas/    Pydantic models for the output JSON and diagnostics
backend/tests/          pytest suite (mocked LLM, no API cost)
evaluation/             matching rules, metrics, failure analysis, report writer
frontend/               optional React review app
outputs/                JSON results for the 20 documents (+ diagnostics/)
reports/                generated reports + approach.md + prompt_changelog.md
REPORT.md               the written report
```

### Notes
- **No secrets in git:** `.env`, the dataset folders and the virtual environment are git-ignored.
- **Cost control:** results are cached by file content + model + prompt version; evaluation stops
  calling the LLM after `EVAL_BUDGET_USD` (default $2).
- **Docker (optional):** `docker build -t invoice-ai .` then
  `docker run -p 8000:8000 --env-file .env -v invoice-data:/app/data invoice-ai`. This serves the
  API and web app on http://localhost:8000 with Tesseract included.
- **Limitations:** 10 labelled documents give wide error bars (one document = 10 percentage
  points); only the first 10 pages of a PDF are processed; numeric dates rely on the document's
  locale (ambiguous ones are flagged for review).
