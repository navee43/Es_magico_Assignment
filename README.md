# Invoice AI — LLM invoice extraction with validation, human review and evaluation

Upload an invoice (PDF, scan or phone photo) and get validated JSON back:

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

Built for the Es Magico AI Intern assignment: the mandatory pipeline, **both bonuses** (the
human-review flag with measured effectiveness, and a React review app), an evaluation harness,
failure analysis, real token/cost tracking, and a one-container deployment to a single EC2 instance.

> **Results status.** The evaluation harness, failure analysis and cost reports are generated
> from real runs (`make eval`). They have **not** been run against the assignment dataset in this
> repository yet, so no accuracy numbers appear below. Section 12 explains where they go.

---

## Contents
1. [Overview](#1-overview) · 2. [Architecture](#2-architecture) · 3. [Features](#3-features) ·
4. [Tech stack](#4-tech-stack) · 5. [Why this LLM approach](#5-why-this-llm-approach) ·
6. [Local setup](#6-local-setup) · 7. [Configuration](#7-configuration) ·
8. [Running backend and frontend](#8-running-the-backend-and-frontend) · 9. [Docker](#9-docker) ·
10. [AWS EC2 deployment](#10-aws-ec2-deployment) · 11. [Evaluation](#11-evaluation) ·
12. [Accuracy table](#12-field-level-accuracy) · 13. [Failure analysis](#13-failure-analysis) ·
14. [Cost tracking](#14-cost-tracking) · 15. [Review-flag effectiveness](#15-human-review-effectiveness) ·
16. [API](#16-api) · 17. [Known limitations](#17-known-limitations) · 18. [Future improvements](#18-future-improvements)

---

## 1. Overview

The pipeline handles clean digital PDFs, scanned PDFs, photographed invoices, non-invoice
documents, corrupted or unreadable files, LLM API failures, and missing or conflicting values.
Every result is checked twice (schema + arithmetic). Every document that might be wrong is flagged
with a reason. A processing failure is never reported as a "non-invoice".

## 2. Architecture

```text
Browser ──HTTPS (Caddy, optional)──▶ EC2 (Ubuntu) ─▶ Docker container (one image)
                                                      ├─ React SPA (static build)
                                                      ├─ FastAPI  /api/v1/*
                                                      ├─ Extraction pipeline
                                                      │    validate file (magic bytes, size, pages)
                                                      │    → PyMuPDF text  |  Tesseract OCR (+rotation)
                                                      │    → route: embedded text  or  page image + OCR hint
                                                      │    → LLM (strict JSON schema) ──────────────▶ External LLM API
                                                      │    → Pydantic schema → Decimal arithmetic → grounding
                                                      │    → one targeted retry → needs_review + reasons
                                                      └─ /app/data volume: SQLite metadata, uploads, results, cache
```

```text
backend/app/
  main.py               app factory, security headers, SPA serving
  api/routes.py         REST endpoints;  api/auth.py  password → signed token
  services/
    file_validation.py  signatures, size limits, filename sanitising
    pdf_processor.py    digital text, scanned-page detection, page rendering
    ocr.py              Tesseract + preprocessing + rotation detection
    prompts.py          versioned prompt + JSON schema
    llm_client.py       OpenAI-compatible client: timeouts, retries, real usage
    validator.py        schema normalisation, arithmetic, completeness, grounding
    review.py           needs_review decision from observable signals
    extractor.py        orchestration, retry policy, cost per attempt
    cost_tracker.py     pricing, budget guard, result cache
  database/db.py        SQLite + on-disk JSON results
  cli/                  extract (one file), extract_all (dataset)
evaluation/             run.py, matching.py, metrics.py, failures.py, report.py, synthetic.py
frontend/src/           pages/, components/, hooks/, services/, types/
reports/                generated reports + approach.md + prompt_changelog.md
```

## 3. Features

**Extraction** — routing by document type, strict structured output, ISO dates, ISO-4217
currencies, invoice/non-invoice classification, one JSON repair call, one validation retry that
escalates to the page image, HTTP retries with backoff, caching.

**Validation** — Pydantic schema; `sum(line items) + tax ≈ total` in `Decimal` with a configurable
tolerance; discounts/charges/round-off and tax-inclusive pricing reported as explanations, never
silently applied; required-field checks; a grounding check that catches invented values.

**Bonus 1 — human-review flag** — `needs_review` plus machine-readable `review_reasons`
(e.g. `TOTAL_MISMATCH`, `MISSING_INVOICE_DATE`, `LOW_OCR_QUALITY`, `LLM_RESULT_CONFLICT`,
`UNGROUNDED_INVOICE_NUMBER`). The harness measures its precision and recall.

**Bonus 2 — React app** — dashboard, drag-and-drop upload with progress, side-by-side document
viewer (PDF pages, zoom) and extracted fields with each flag shown next to the field it concerns,
an arithmetic "tape" showing exactly why totals do or don't reconcile, a review queue with an
editor that keeps the model's original output separate from corrections, and an evaluation page
that renders the real report (empty state until one exists — no hard-coded numbers).

**Evaluation** — field accuracy, per-document accuracy, exact-match rate, confusion matrix,
P/R/F1, order-independent line-item matching, schema/arithmetic pass rates, latency, cost,
review-flag effectiveness, failure categories with likely causes and fixes, baseline snapshots
and experiment comparison.

## 4. Tech stack

Python 3.11+, FastAPI, Pydantic v2, PyMuPDF, Tesseract (pytesseract), Pillow, httpx, SQLite,
pytest · React 19 + TypeScript, Vite, Tailwind CSS 4, TanStack Query, Axios, Lucide, react-pdf
(PDF.js) · Docker (single multi-stage image), one Ubuntu EC2 instance, optional Caddy.

## 5. Why this LLM approach

* **Digital PDFs → text only.** Embedded text is exact; sending an image would cost more and
  add a chance to misread digits.
* **Scans and photos → page image + OCR hint, in one call.** OCR confidence doesn't tell you
  whether a "3" is really an "8", and photos scramble table layout. A multimodal model reading the
  image, with OCR text as a hint for small print, is the most robust single call.
* **One model call per attempt.** No ensembles. The text and vision model are configured
  separately, so a cheaper model can serve digital PDFs.
* **Checks drive the only retry.** A retry happens only when a check that a second look could fix
  fails, and it escalates text routes to the image.
* **Measured, not argued.** `ocr_first`, `vision_always` and `text_only` modes exist so the harness
  can compare them against the baseline (`make eval-experiment`).

**Multiple LLM providers.** OpenAI, Groq (GroqCloud), Grok (xAI) or any OpenAI-compatible endpoint can
be chosen separately for text documents (`LLM_PROVIDER`) and for page images (`LLM_VISION_PROVIDER`),
with an optional automatic fallback provider (`LLM_FALLBACK_PROVIDER`) used when the primary one times
out, is rate-limited, errors or rejects the key. Every HTTP call records which provider and model served
it and is priced with that model's rates (reasoning tokens are counted exactly once).

| Provider | `LLM_PROVIDER` | Key variable | Default text / image model |
|---|---|---|---|
| OpenAI | `openai` | `OPENAI_API_KEY` | `gpt-4.1-mini` / `gpt-4.1-mini` |
| Groq (GroqCloud, `gsk_` keys) | `groq` | `GROQ_API_KEY` | `openai/gpt-oss-120b` / `qwen/qwen3.8-27b` |
| Grok (xAI, `xai-` keys) | `grok` | `XAI_API_KEY` | `grok-4-1-fast-reasoning` / same |
| Other OpenAI-compatible | `custom` | `CUSTOM_LLM_API_KEY` + `CUSTOM_LLM_BASE_URL` | set `LLM_MODEL` |

Provider quirks are handled automatically: strict JSON schema falls back to JSON mode (with the schema in
the prompt) if a model rejects it or fails to generate it, and unsupported `temperature` / max-token
parameters are dropped or renamed. Built-in prices exist for the default models; set
`MODEL_PRICING_JSON` for any other model and verify prices on the provider's page. Full rationale:
[`reports/approach.md`](reports/approach.md).

## 6. Local setup

Requirements: Python 3.11+, Node 20+, Tesseract (`sudo apt install tesseract-ocr` ·
`brew install tesseract` · Windows: the UB-Mannheim installer, then add it to `PATH`).

```bash
git clone <your-repo-url> invoice-ai && cd invoice-ai
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cd frontend && npm ci && cd ..
cp .env.example .env                                   # then set LLM_PROVIDER + its key, APP_PASSWORD, SECRET_KEY
make test
```

Put the dataset here (these folders are git-ignored because the data is confidential):

```text
data/labelled/       the 10 labelled documents (pdf/jpg/png)
data/unlabelled/     the 10 unlabelled documents
data/ground_truth/   one JSON per labelled document, named after it: invoice_03.pdf → invoice_03.json
```

A single combined ground-truth JSON (list or `{filename: record}`) also works:
`python -m evaluation.run --ground-truth path/to/labels.json`.

**Windows without `make`:** every target is one command — see the `Makefile`. The common ones:

| make | command |
|---|---|
| `make test` | `python -m pytest` |
| `make eval` | `python -m evaluation.run` |
| `make extract-all` | `python -m backend.app.cli.extract_all` |
| `make eval-baseline` | `python -m evaluation.run --tag baseline` |

## 7. Configuration

All settings are environment variables (see `.env.example` for every option). The important ones:

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` / `LLM_VISION_PROVIDER` | `openai` / same | `openai`, `groq`, `grok`, `custom`, or `mock` (offline UI testing only) |
| `LLM_FALLBACK_PROVIDER` | — | Provider to retry on when the primary fails |
| `OPENAI_API_KEY`, `GROQ_API_KEY`, `XAI_API_KEY` | — | One key per provider you use. Never commit them. |
| `LLM_MODEL` / `LLM_VISION_MODEL` | provider default | Text-route / image-route model |
| `PRICE_INPUT_PER_1M`, `PRICE_CACHED_INPUT_PER_1M`, `PRICE_OUTPUT_PER_1M` | 0.40 / 0.10 / 1.60 | Fallback cost rates (USD) for models without built-in or `MODEL_PRICING_JSON` prices |
| `ROUTING_MODE` | `auto` | `auto`, `ocr_first`, `vision_always`, `text_only` |
| `APP_PASSWORD`, `SECRET_KEY` | — | Web-app password and token-signing key |
| `MAX_UPLOAD_MB`, `MAX_PDF_PAGES` | 15, 10 | Upload / processing limits |
| `EVAL_BUDGET_USD` | 2.00 | Evaluation stops calling the LLM past this |

## 8. Running the backend and frontend

```bash
python -m backend.app.cli.extract invoice.pdf          # one file → JSON on stdout
python -m backend.app.cli.extract scan.jpg --full      # + review reasons, tokens, cost, routing

make backend-dev     # API on http://localhost:8000  (docs at /api/docs)
make frontend-dev    # UI on http://localhost:5173  (proxies /api to :8000)
```

`make frontend-build` creates `frontend/dist`; FastAPI then serves the whole app on :8000.

## 9. Docker

One multi-stage `Dockerfile`: stage 1 builds the React app; stage 2 is `python:3.11-slim` with
Tesseract (eng + osd), the pinned Python dependencies, the backend, and the built SPA. It runs as
an unprivileged user with a health check.

```bash
docker build -t invoice-ai .
docker run -d --name invoice-ai \
  -p 8000:8000 \
  --env-file .env \
  -v invoice-data:/app/data \
  --restart unless-stopped \
  invoice-ai
# open http://localhost:8000
```

`invoice-data` holds SQLite, uploaded files, results and the cache, so replacing the container
keeps your data. Uploaded documents are only reachable through the authenticated API.

> Verification note: in the environment where this was written, Docker Hub was unreachable, so
> the image build could not be run end-to-end there. Each stage was verified separately instead:
> `npm ci && npm run build` from the lockfile, `pip install -r requirements.txt` on Python 3.11
> with the full test suite passing, and the app served from the same file layout as a non-root
> user. Run `docker build` once locally before deploying.

## 10. AWS EC2 deployment

The target is deliberately simple: **one instance, one container, one volume.** No load balancer,
ECS, Kubernetes, Terraform or managed database.

### Step 1 — Launch an instance

* **AMI:** Ubuntu Server 24.04 LTS (x86_64).
* **Type:** `t3.medium` (2 vCPU, 4 GiB) is the recommended starting point. At the time of writing
  it is about $0.042/h in us-east-1 and $0.045/h in ap-south-1 (Mumbai), roughly $30–33/month if
  left running — check the EC2 pricing page for your region. `t3.small` (2 GiB) can run the app,
  but building the image (Node + pip) and OCR on large scans can exhaust memory; `t3.micro` is not
  recommended.
* **Storage:** 20–30 GiB gp3.
* **How to tell if it's big enough:** after deploying, process a few scans while running
  `docker stats` and `free -h`. Memory should stay below ~75 %. On burstable `t3` instances,
  watch *CPUCreditBalance* in CloudWatch: if it trends to zero, OCR will slow down — move to
  `t3.large` or enable *Unlimited* mode knowingly. `df -h` shows disk use; the volume grows with
  uploads (keep 5+ GiB free).

### Step 2 — Security group

| Port | Source | Why |
|---|---|---|
| 22 (SSH) | **My IP** only | Administration |
| 80 (HTTP) | 0.0.0.0/0 | App over HTTP for initial testing, or Caddy's certificate challenge |
| 443 (HTTPS) | 0.0.0.0/0 | Only if you set up a domain + Caddy |

Do **not** open 8000.

### Step 3 — Install Docker

```bash
ssh -i your-key.pem ubuntu@<EC2_PUBLIC_IP>
sudo apt-get update && sudo apt-get install -y docker.io docker-buildx git
sudo systemctl enable --now docker
sudo usermod -aG docker ubuntu && newgrp docker
# optional safety net for builds on small instances: 2 GiB swap
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
```

### Step 4 — Clone the repository

```bash
git clone https://github.com/<you>/invoice-ai.git && cd invoice-ai
```

### Step 5 — Configure `.env`

```bash
cp .env.example .env
nano .env    # set LLM_PROVIDER + its key (e.g. GROQ_API_KEY), APP_PASSWORD (strong), SECRET_KEY:
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
chmod 600 .env
```

To show evaluation results on the live site, copy the generated `reports/` folder (from running
`make eval` on your machine) into the repo before building — it is baked into the image.

### Step 6 — Build

```bash
docker build -t invoice-ai .
```

### Step 7 — Run

```bash
docker run -d \
  --name invoice-ai \
  -p 80:8000 \
  --env-file .env \
  -v invoice-data:/app/data \
  --restart unless-stopped \
  invoice-ai
docker ps && curl -s localhost/health
```

### Step 8 — Test via the public IP

Open `http://<EC2_PUBLIC_IP>` and sign in. **This is plain HTTP**: the password and invoices
travel unencrypted. Use it only for a quick check; use Step 9 for anything public.

### Step 9 — Domain + HTTPS with Caddy (recommended for a public demo)

1. Point an `A` record (e.g. `invoices.example.com`) to the instance's public IP. Use an
   Elastic IP if you want the address to survive stop/start (it is billed like any public IPv4).
2. Re-run the container bound to localhost only:
   ```bash
   docker rm -f invoice-ai
   docker run -d --name invoice-ai -p 127.0.0.1:8000:8000 --env-file .env \
     -v invoice-data:/app/data --restart unless-stopped invoice-ai
   ```
3. Install Caddy on the host (commands from caddyserver.com/docs/install, Debian/Ubuntu section):
   ```bash
   sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
   sudo apt update && sudo apt install -y caddy
   ```
4. `/etc/caddy/Caddyfile`:
   ```caddyfile
   invoices.example.com {
       encode gzip
       request_body {
           max_size 20MB
       }
       reverse_proxy 127.0.0.1:8000 {
           transport http {
               read_timeout 200s
           }
       }
   }
   ```
5. `sudo systemctl reload caddy`. Caddy obtains and renews the certificate automatically
   (ports 80 and 443 must be open). Open `https://invoices.example.com`.

### Step 10 — Operate

```bash
docker logs -f invoice-ai                       # logs (no invoice contents or secrets are logged)
docker restart invoice-ai                       # restart
docker stop invoice-ai && docker start invoice-ai

# update / redeploy (data volume is kept)
git pull && docker build -t invoice-ai . && docker rm -f invoice-ai && \
docker run -d --name invoice-ai -p 80:8000 --env-file .env -v invoice-data:/app/data --restart unless-stopped invoice-ai

# backup (stop briefly so SQLite is consistent)
docker stop invoice-ai
docker run --rm -v invoice-data:/data -v "$PWD":/backup alpine tar czf /backup/invoice-data-$(date +%F).tar.gz -C /data .
docker start invoice-ai
# copy the archive off the instance:  scp -i key.pem ubuntu@<IP>:~/invoice-ai/invoice-data-*.tar.gz .

# restore
docker stop invoice-ai
docker run --rm -v invoice-data:/data -v "$PWD":/backup alpine sh -c "rm -rf /data/* && tar xzf /backup/invoice-data-YYYY-MM-DD.tar.gz -C /data"
docker start invoice-ai
```

(Use `-p 127.0.0.1:8000:8000` instead of `-p 80:8000` if Caddy is in front.)

### Production safety checklist

* `.env` is git-ignored and docker-ignored; keys are read from the environment only.
* Upload size limit (rejected before the body is parsed), magic-byte checks, page and pixel limits,
  sanitised display names; files are stored under random IDs, never under user-provided names.
* Uploaded documents and every data/admin endpoint require the password token; there is no public
  static route to `/app/data`.
* Strict CSP, `X-Frame-Options: DENY`, `nosniff`, no CORS unless `CORS_ORIGINS` is set.
* Login is rate limited; tokens are HMAC-signed and expire.
* Logs contain error codes, not invoice text or keys.
* The app runs as a non-root user.
* **A single EC2 instance is not highly available.** If the instance or its disk fails, the app is
  down until it is replaced, and data since the last backup can be lost. Take backups.
* The public-IP HTTP deployment (Step 8) is **not** secure production HTTPS.

### AWS cost awareness

| Item | What drives it | Notes |
|---|---|---|
| EC2 compute | Hours the instance is *running* | `t3.medium` ≈ $30–33/month if always on; stop it when not demoing |
| EBS gp3 volume | GiB provisioned, even while stopped | 30 GiB costs a few dollars a month; delete it when done |
| Public IPv4 | $0.005 per hour per address while in use | ≈ $3.60/month. An unattached Elastic IP is also billed |
| Data transfer | Outbound to the internet | Small for a demo; inbound is free |
| Snapshots / AMIs | Stored GiB | Delete ones you don't need |
| **LLM API** | Tokens | Billed by the LLM provider, **not** AWS. AWS credits generally won't cover it |

Whether promotional credits cover a given charge depends on your account's credit terms — check
**Billing → Credits** for eligible services and the expiry date.

**Budget alert:** Billing and Cost Management → Budgets → *Create budget* → *Cost budget* →
monthly amount (e.g. $20) → alerts at 50 %, 80 % and 100 % of actual spend to your email.
Optionally set *Credits* to be excluded so the alert reflects real usage.

**Stop vs terminate:** *Stop* ends compute charges but keeps the EBS volume (still billed) and
releases the auto-assigned public IP (an Elastic IP stays and is billed). *Terminate* deletes the
instance and, if "delete on termination" is set, its root volume.

**Clean-up when finished:** terminate the instance → EC2 → Volumes: delete any *available*
volumes → Elastic IPs: release → Snapshots/AMIs: delete → check Cost Explorer the next day.

## 11. Evaluation

```bash
make eval-baseline   # FIRST, before any tuning: snapshot → reports/experiments/baseline/
make eval            # extract labelled docs (cached) + score → reports/evaluation.{md,csv,json},
                     #   failure_analysis.md, cost_analysis.md
make eval-score      # re-score outputs/ without any LLM calls
make eval-experiment ROUTING=ocr_first TAG=ocr_first   # measured alternative vs baseline
make extract-all     # all 20 documents → outputs/<name>.json + outputs/diagnostics/<name>.json
make eval-synthetic  # harness demo on generated, clearly labelled SYNTHETIC fixtures
```

**Matching rules** (conservative — they forgive formatting, not meaning):

| Field | Rule |
|---|---|
| vendor | case, punctuation, whitespace, `&`/`and`; trailing legal-form words ignored (Pvt, Ltd, Limited, Inc, LLC, Co, Corp, GmbH…), so "Acme Pvt Ltd" = "Acme" but "Acme Supplies" ≠ "Acme Traders" |
| invoice_number | whitespace and case only (`INV-2041` ≠ `INV2041`) |
| invoice_date | compared as dates after ISO normalisation |
| currency | ISO code after mapping unambiguous symbols |
| tax_total, total | `Decimal`, ±0.01 |
| line_items | one-to-one maximum matching, any order; a pair matches only if description similarity ≥ 0.85, quantity equal and amount ±0.01 |
| nulls | null = null is correct; otherwise counted as *missing* or *spurious* |

**Integrity:** invoice-field accuracy is computed only over ground-truth invoices, so
non-invoices never add free correct nulls; non-invoices are scored separately (classification +
all-null compliance). Processing failures count as errors and are never treated as correct
non-invoices. Every report records model, routing, prompt version and a prompt fingerprint.
Prompt changes made after seeing labels are logged in
[`reports/prompt_changelog.md`](reports/prompt_changelog.md).

## 12. Field-level accuracy

**Not yet generated for the assignment dataset.** After `make eval`, the table is in
[`reports/evaluation.md`](reports/evaluation.md) (section *Field-level accuracy*) and on the app's
Evaluation page. Paste it here for the submission:

| Field | N | Correct | Accuracy |
|---|---|---|---|
| *run `make eval`* | | | |

## 13. Failure analysis

Generated by `make eval` into [`reports/failure_analysis.md`](reports/failure_analysis.md):
every wrong field with document, expected value, predicted value, likely reason (derived from the
route, OCR statistics, arithmetic status and grounding) and a proposed fix; errors grouped by cause
(OCR, classification, vendor, dates, invoice-number confusion, missing line items, tax, totals,
hallucinations, unreadable documents); the highest-priority improvement ranked by documents
affected; and the initial approach plus any alternative experiments **that were actually run**,
with their measured comparison against the baseline.

## 14. Cost tracking

Every HTTP call's `usage` from the provider (prompt, cached prompt and completion tokens) is
priced per call, including repairs and retries:

`cost = (prompt − cached) × input rate + cached × cached rate + completion × output rate` (per 1M tokens).

Each document stores model, tokens, attempts, cost, latency and whether it was a cache hit (SQLite
and `outputs/diagnostics/`). `reports/cost_analysis.md` reports total spend, average/min/max per
document, average tokens and latency, cost of retries, cost by route and the pricing assumptions.
The result cache means re-running costs nothing, and `EVAL_BUDGET_USD` caps spend per run.
LLM costs are reported separately from AWS hosting costs.

## 15. Human-review effectiveness

A document "contains an error" if any evaluated field differs from ground truth or processing
failed. The harness reports documents flagged, errors caught (flagged & wrong), errors missed,
correct-but-flagged, precision (= % of flagged documents that really had errors) and recall —
in `reports/evaluation.md` and on the Evaluation page. **Not yet run on the assignment data.**

## 16. API

Interactive docs: `/api/docs`. All `/api/v1` routes except `auth/login` and `config` need
`Authorization: Bearer <token>`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness, OCR available, LLM configured (no secrets) |
| POST | `/api/v1/auth/login` | `{"password"}` → `{"token", "expires_at"}` |
| POST | `/api/v1/extract` | multipart `file` → result + review reasons + diagnostics. 200 success · 413 too large · 422 bad file · 502 LLM failure · 503 busy |
| GET | `/api/v1/documents` | List; filters `kind`, `status`, `review=pending`, `q`, `limit`, `offset` |
| GET | `/api/v1/documents/{id}` | Metadata, status, tokens, cost |
| GET | `/api/v1/documents/{id}/result` | `model_output` (original), `corrected`, `effective`, reasons, diagnostics |
| GET | `/api/v1/documents/{id}/file` | Original file (authenticated) |
| PATCH | `/api/v1/documents/{id}/review` | `{"action": "save"\|"approve"\|"reopen", "corrected": {...}, "note"}` — corrections stored separately |
| GET | `/api/v1/stats` | Dashboard counts, cost, tokens, latency percentiles, review reasons |
| GET | `/api/v1/evaluation` | Latest `reports/evaluation.json` (404 until `make eval` has run) |

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"password":"…"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s -H "Authorization: Bearer $TOKEN" -F "file=@invoice.pdf" localhost:8000/api/v1/extract
```

## 17. Known limitations

* Ten labelled documents give wide error bars: one document = 10 percentage points.
* The single-password auth is for a one-user demo, not multi-user access control.
* Extraction is synchronous (by design for this workload); very large scans can take tens of
  seconds. Concurrency is capped by `MAX_CONCURRENT_EXTRACTIONS`.
* Only the first `MAX_PDF_PAGES` pages are processed and the first `MAX_VISION_PAGES` are sent as
  images; documents with more pages than `MAX_PDF_PAGES` are flagged for review.
* The schema has no place for discounts or shipping; they explain arithmetic gaps but are only
  visible in diagnostics.
* Date day/month order for purely numeric dates relies on document locale; ambiguous cases are flagged.
* OCR is English-only by default (`OCR_LANG` can add languages if installed in the image).
* SQLite + one instance: no high availability; back up the volume.

## 18. Future improvements

* Run `ocr_first` vs `auto` vs `vision_always` on the real data and keep the cheapest mode that
  doesn't lose accuracy.
* Per-field evidence (page + bounding box) so reviewers can jump to the value on the page.
* Use reviewer corrections as a growing labelled set (with a held-out split to avoid overfitting).
* Background job queue and multi-user accounts if the workload grows.
* Optional second-opinion model only for flagged documents.
