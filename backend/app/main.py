"""FastAPI application: JSON API + the built React SPA, served from one process."""

from __future__ import annotations

import logging
import secrets
import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.auth import LoginRateLimiter, TokenSigner
from backend.app.api.routes import api, protected, public
from backend.app.config import PIPELINE_VERSION, Settings, get_settings
from backend.app.database.db import Database
from backend.app.services.extractor import InvoiceExtractor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("invoice_ai")

CSP = (
    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' blob: data:; font-src 'self' data:; worker-src 'self' blob:; connect-src 'self' blob:; "
    "object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def create_app(settings: Settings | None = None, extractor: InvoiceExtractor | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Invoice AI",
        version=PIPELINE_VERSION,
        description="LLM-based invoice extraction with validation, human review, evaluation and cost tracking.",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    secret = settings.secret_key
    if settings.auth_enabled and not secret:
        secret = secrets.token_urlsafe(32)
        logger.warning("SECRET_KEY not set: using a random key (sessions end when the container restarts).")
    if settings.auth_enabled and not settings.app_password:
        logger.error("AUTH_ENABLED=true but APP_PASSWORD is empty: nobody will be able to sign in.")
    if not settings.llm_configured:
        logger.warning("LLM_API_KEY is not set: extraction requests will fail until it is configured.")
    from backend.app.services.ocr import tesseract_available

    if settings.ocr_enabled and not tesseract_available():
        logger.warning("Tesseract OCR not found on PATH: scans/photos will be sent to the vision model without "
                       "an OCR hint. Install Tesseract for better accuracy.")
    if settings.llm_provider == "mock":
        logger.warning("LLM_PROVIDER=mock: results come from a regex stub, NOT a real model.")

    app.state.settings = settings
    app.state.db = Database(settings.storage_dir)
    app.state.extractor = extractor or InvoiceExtractor(settings)
    app.state.signer = TokenSigner(secret or "auth-disabled")
    app.state.login_limiter = LoginRateLimiter()
    app.state.extraction_slots = threading.BoundedSemaphore(max(1, settings.max_concurrent_extractions))

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST", "PATCH"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.middleware("http")
    async def guard_and_headers(request: Request, call_next):
        # Reject oversized uploads before the multipart body is parsed.
        if request.url.path == "/api/v1/extract":
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > settings.max_upload_bytes + 1_048_576:
                return JSONResponse(
                    status_code=413,
                    content={"detail": {"code": "FILE_TOO_LARGE",
                                        "message": f"The file exceeds the {settings.max_upload_mb} MB limit."}},
                )
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if not request.url.path.startswith("/api/docs"):
            response.headers.setdefault("Content-Security-Policy", CSP)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError):
        errors = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": {"code": "INVALID_REQUEST",
                                                                 "message": "Invalid request.", "errors": errors}})

    app.include_router(public)
    app.include_router(api)
    app.include_router(protected)
    _mount_frontend(app, settings.frontend_dist)
    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.is_file():
        logger.info("Frontend build not found at %s; serving the API only.", dist)

        @app.get("/", include_in_schema=False)
        def root():
            return {"message": "Invoice AI API. Frontend not built; see /api/docs."}

        return

    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    dist_root = dist.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/") or full_path == "api":
            return JSONResponse(status_code=404, content={"detail": {"code": "NOT_FOUND", "message": "Not found."}})
        candidate = (dist_root / full_path).resolve()
        if full_path and candidate.is_file() and dist_root in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

