# One image = React build + FastAPI + Tesseract. Build: docker build -t invoice-ai .

# ---------- Stage 1: build the React frontend ----------
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
# Fall back to a fresh install if the lock file was regenerated on Windows/macOS and lacks the
# Linux-only native packages (a known npm optional-dependency issue).
RUN npm ci --no-audit --no-fund || (rm -rf node_modules package-lock.json && npm install --no-audit --no-fund)
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: Python runtime ----------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STORAGE_DIR=/app/data \
    FRONTEND_DIST=/app/frontend/dist \
    REPORTS_DIR=/app/reports \
    FORWARDED_ALLOW_IPS=127.0.0.1,172.17.0.1

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-osd \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY backend/ backend/
COPY evaluation/ evaluation/
COPY reports/ reports/
COPY --from=frontend /build/dist frontend/dist

# Unprivileged user; /app/data is the persistent volume (SQLite, uploads, results, cache).
RUN useradd --system --uid 10001 --no-create-home --home-dir /app app \
 && mkdir -p /app/data \
 && chown -R app:app /app/data
USER app

EXPOSE 8000
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# One worker: SQLite + in-process limits. Requests still run concurrently in the thread pool.
CMD ["uvicorn", "backend.app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--timeout-keep-alive", "75"]
