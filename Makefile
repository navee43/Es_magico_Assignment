# Every target is a single command; on Windows without make, copy the command shown.
PY ?= python
ROUTING ?= vision_always
TAG ?= experiment

.PHONY: help install test eval eval-score eval-baseline eval-experiment eval-synthetic extract-all cost-report \
        backend-dev frontend-dev frontend-build docker-build docker-run

help:            ## list targets
	@grep -E '^[a-z-]+:.*## ' Makefile | sed 's/:.*## /\t/'

install:         ## install Python + frontend dependencies
	$(PY) -m pip install -r requirements-dev.txt
	cd frontend && npm ci

test:            ## run the test suite (mocked LLM, no API spend)
	$(PY) -m pytest

eval:            ## extract labelled docs (cached) + score + write reports/
	$(PY) -m evaluation.run

eval-score:      ## re-score existing outputs/ without calling the LLM
	$(PY) -m evaluation.run --score-only

eval-baseline:   ## snapshot results BEFORE any tuning -> reports/experiments/baseline/
	$(PY) -m evaluation.run --tag baseline

eval-experiment: ## e.g. make eval-experiment ROUTING=ocr_first TAG=ocr_first
	$(PY) -m evaluation.run --tag $(TAG) --routing $(ROUTING)

eval-synthetic:  ## harness demo on generated SYNTHETIC fixtures (not assignment results)
	$(PY) -m evaluation.run --synthetic

extract-all:     ## write one JSON per document for all 20 documents -> outputs/
	$(PY) -m backend.app.cli.extract_all
	$(PY) -m evaluation.cost_report --outputs outputs --out reports/cost_analysis_all.md

cost-report:     ## cost report over everything in outputs/
	$(PY) -m evaluation.cost_report --outputs outputs --out reports/cost_analysis_all.md

backend-dev:     ## API with auto-reload on :8000
	$(PY) -m uvicorn backend.app.main:create_app --factory --reload --port 8000

frontend-dev:    ## Vite dev server on :5173 (proxies /api to :8000)
	cd frontend && npm run dev

frontend-build:  ## production build into frontend/dist (served by FastAPI)
	cd frontend && npm run build

docker-build:    ## build the single production image
	docker build -t invoice-ai .

docker-run:      ## run locally on http://localhost:8000
	docker run -d --name invoice-ai -p 8000:8000 --env-file .env -v invoice-data:/app/data --restart unless-stopped invoice-ai
