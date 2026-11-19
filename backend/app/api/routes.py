"""REST API (all under /api/v1 except /health)."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError

from backend.app.api.auth import check_password, require_auth
from backend.app.config import PIPELINE_VERSION, Settings
from backend.app.database.db import Database, utcnow
from backend.app.schemas.invoice import InvoiceResult
from backend.app.services import prompts
from backend.app.services.extractor import ExtractionOutcome, InvoiceExtractor
from backend.app.services.file_validation import DocumentError, sanitize_filename, validate_bytes
from backend.app.services.ocr import tesseract_available
from backend.app.services.review import REVIEW_REASON_LABELS
from backend.app.services.validator import check_financials

logger = logging.getLogger(__name__)

public = APIRouter()
api = APIRouter(prefix="/api/v1")
protected = APIRouter(prefix="/api/v1", dependencies=[Depends(require_auth)])

# Errors that mean the file itself is unusable: don't keep the bytes.
REJECTED_UPLOAD_CODES = {"EMPTY_FILE", "FILE_TOO_LARGE", "UNSUPPORTED_FILE_TYPE"}
FILE_ERROR_CODES = REJECTED_UPLOAD_CODES | {"CORRUPTED_FILE", "ENCRYPTED_PDF", "IMAGE_TOO_LARGE", "NO_TEXT_EXTRACTED", "RENDER_FAILED"}


def _error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": {"code": code, "message": message, **extra}})


def _state(request: Request) -> tuple[Settings, Database, InvoiceExtractor]:
    s = request.app.state
    return s.settings, s.db, s.extractor


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

@public.get("/health")
def health(request: Request) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    return {
        "status": "ok",
        "pipeline_version": PIPELINE_VERSION,
        "ocr_available": tesseract_available(),
        "llm_configured": settings.llm_configured,
        "auth_enabled": settings.auth_enabled,
    }


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@api.post("/auth/login")
def login(body: LoginRequest, request: Request):
    settings: Settings = request.app.state.settings
    if not settings.auth_enabled:
        return {"token": "auth-disabled", "expires_at": None}
    if not settings.app_password:
        return _error(503, "AUTH_NOT_CONFIGURED", "APP_PASSWORD is not set on the server.")
    client = request.client.host if request.client else "unknown"
    if not request.app.state.login_limiter.allow(client):
        return _error(429, "TOO_MANY_ATTEMPTS", "Too many login attempts. Try again in a few minutes.")
    if not check_password(settings, body.password):
        return _error(401, "INVALID_PASSWORD", "Incorrect password.")
    token, expires = request.app.state.signer.issue(settings.token_ttl_hours * 3600)
    return {"token": token, "expires_at": expires}


@api.get("/config")
def public_config(request: Request) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    return {
        "auth_enabled": settings.auth_enabled,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model if settings.llm_provider != "mock" else "mock-regex",
        "llm_vision_model": settings.llm_vision_model if settings.llm_provider != "mock" else "mock-regex",
        "routing_mode": settings.routing_mode,
        "max_upload_mb": settings.max_upload_mb,
        "max_pdf_pages": settings.max_pdf_pages,
        "prompt_version": prompts.PROMPT_VERSION,
        "review_reason_labels": REVIEW_REASON_LABELS,
    }


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _summary_fields(outcome: ExtractionOutcome) -> dict[str, Any]:
    d = outcome.diagnostics
    inv = outcome.invoice
    return {
        "status": d.status,
        "is_invoice": None if inv is None else int(inv.is_invoice),
        "needs_review": int(inv.needs_review if inv else True),
        "review_status": "pending" if (inv is None or inv.needs_review) else "not_required",
        "review_reasons": json.dumps(d.review_reasons),
        "error_code": d.error_code,
        "error_message": d.error_message,
        "route": d.route,
        "document_type": d.document_type,
        "vendor": inv.vendor if inv else None,
        "invoice_number": inv.invoice_number if inv else None,
        "invoice_date": inv.invoice_date.isoformat() if inv and inv.invoice_date else None,
        "currency": inv.currency if inv else None,
        "total": float(inv.total) if inv and inv.total is not None else None,
        "financial_status": (d.validation.get("financial") or {}).get("status"),
        "pages": d.source.pages_total or None,
        "model": ", ".join(d.usage.models) or None,
        "input_tokens": d.usage.input_tokens,
        "output_tokens": d.usage.output_tokens,
        "total_tokens": d.usage.total_tokens,
        "cost_usd": d.usage.cost_usd,
        "attempts": d.usage.llm_attempts,
        "cached": int(d.cached),
        "processing_ms": d.processing_ms,
    }


def _process_upload(request: Request, data: bytes, filename: str) -> tuple[int, dict[str, Any]]:
    settings, db, extractor = _state(request)
    doc_id = uuid.uuid4().hex
    display_name = sanitize_filename(filename)
    try:
        info = validate_bytes(data, settings.max_upload_bytes)
    except DocumentError as exc:
        db.create_document(
            doc_id, original_filename=display_name, status="failed", needs_review=0, review_status="not_required",
            error_code=exc.code, error_message=exc.message, size_bytes=len(data), review_reasons="[]",
        )
        return 422, {"detail": {"code": exc.code, "message": exc.message, "document_id": doc_id}}

    stored_name = f"{doc_id}{info.extension}"
    db.upload_path(stored_name).write_bytes(data)
    db.create_document(
        doc_id, original_filename=display_name, stored_name=stored_name, mime_type=info.mime_type,
        size_bytes=info.size_bytes, sha256=info.sha256, status="processing",
    )

    semaphore: threading.BoundedSemaphore = request.app.state.extraction_slots
    if not semaphore.acquire(timeout=settings.extraction_queue_timeout_s):
        db.update_document(doc_id, status="failed", error_code="SERVER_BUSY",
                           error_message="Server busy; please retry.", needs_review=1, review_status="pending",
                           review_reasons=json.dumps(["PROCESSING_FAILED"]))
        return 503, {"detail": {"code": "SERVER_BUSY", "message": "The server is busy. Please retry shortly.",
                                "document_id": doc_id}}
    try:
        outcome = extractor.extract_bytes(data)
    finally:
        semaphore.release()

    db.write_json(doc_id, "model_output.json", outcome.full_json())
    db.update_document(doc_id, **_summary_fields(outcome))
    body = {"document_id": doc_id, "filename": display_name, **outcome.full_json()}
    if outcome.ok:
        return 200, body
    code = outcome.diagnostics.error_code or "EXTRACTION_FAILED"
    status = 422 if code in FILE_ERROR_CODES else 429 if code == "BUDGET_EXCEEDED" else 502
    body["detail"] = {"code": code, "message": outcome.diagnostics.error_message, "document_id": doc_id}
    return status, body


@protected.post("/extract")
async def extract(request: Request, file: UploadFile = File(...)):
    settings: Settings = request.app.state.settings
    limit = settings.max_upload_bytes
    chunks, size = [], 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            return _error(413, "FILE_TOO_LARGE", f"The file exceeds the {settings.max_upload_mb} MB limit.")
        chunks.append(chunk)
    status, body = await run_in_threadpool(_process_upload, request, b"".join(chunks), file.filename or "document")
    return JSONResponse(status_code=status, content=body)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

PUBLIC_DOC_FIELDS = (
    "id", "original_filename", "mime_type", "size_bytes", "pages", "status", "is_invoice", "needs_review",
    "review_status", "review_reasons", "error_code", "error_message", "route", "document_type", "vendor",
    "invoice_number", "invoice_date", "currency", "total", "financial_status", "model", "input_tokens",
    "output_tokens", "total_tokens", "cost_usd", "attempts", "cached", "processing_ms", "created_at",
    "updated_at", "reviewed_at", "review_note",
)


def _public_doc(row: dict[str, Any]) -> dict[str, Any]:
    doc = {k: row.get(k) for k in PUBLIC_DOC_FIELDS}
    doc["has_file"] = bool(row.get("stored_name"))
    return doc


def _get_or_404(db: Database, doc_id: str) -> dict[str, Any]:
    if not doc_id.isalnum() or len(doc_id) > 64:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Document not found."})
    row = db.get(doc_id)
    if not row:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Document not found."})
    return row


@protected.get("/documents")
def list_documents(
    request: Request,
    status: Literal["processing", "success", "failed"] | None = None,
    kind: Literal["invoice", "non_invoice"] | None = None,
    review: Literal["pending", "not_required", "in_review", "approved"] | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    _, db, _ = _state(request)
    rows, total = db.list(status=status, kind=kind, review=review, query=q, limit=limit, offset=offset)
    return {"items": [_public_doc(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@protected.get("/documents/{doc_id}")
def get_document(doc_id: str, request: Request):
    _, db, _ = _state(request)
    return _public_doc(_get_or_404(db, doc_id))


@protected.get("/documents/{doc_id}/result")
def get_result(doc_id: str, request: Request):
    _, db, _ = _state(request)
    row = _get_or_404(db, doc_id)
    stored = db.read_json(doc_id, "model_output.json")
    corrected = db.read_json(doc_id, "corrected.json")
    if stored is None:
        if row["status"] == "processing":
            return _error(409, "STILL_PROCESSING", "The document is still being processed.")
        return {
            "document_id": doc_id, "status": row["status"], "model_output": None, "corrected": None,
            "effective": None, "review_reasons": row["review_reasons"], "review_details": [],
            "diagnostics": {"status": "failed", "error_code": row["error_code"], "error_message": row["error_message"]},
            "review_status": row["review_status"], "review_note": row["review_note"],
        }
    diagnostics = stored["diagnostics"]
    return {
        "document_id": doc_id,
        "status": row["status"],
        "model_output": stored["result"],
        "corrected": corrected["invoice"] if corrected else None,
        "corrected_validation": corrected.get("validation") if corrected else None,
        "effective": corrected["invoice"] if corrected else stored["result"],
        "review_reasons": stored.get("review_reasons", []),
        "review_details": diagnostics.get("review_details", []),
        "diagnostics": diagnostics,
        "review_status": row["review_status"],
        "review_note": row["review_note"],
        "reviewed_at": row["reviewed_at"],
    }


@protected.get("/documents/{doc_id}/file")
def get_file(doc_id: str, request: Request):
    _, db, _ = _state(request)
    row = _get_or_404(db, doc_id)
    if not row.get("stored_name"):
        raise HTTPException(404, detail={"code": "NO_FILE", "message": "The original file was not stored."})
    path = db.upload_path(row["stored_name"])
    if not path.is_file():
        raise HTTPException(404, detail={"code": "NO_FILE", "message": "The original file is missing."})
    return FileResponse(
        path,
        media_type=row["mime_type"] or "application/octet-stream",
        headers={"Content-Disposition": "inline", "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


class CorrectedLineItem(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    quantity: Decimal | None = None
    amount: Decimal | None = None


class CorrectedInvoice(BaseModel):
    is_invoice: bool
    vendor: str | None = Field(default=None, max_length=300)
    invoice_number: str | None = Field(default=None, max_length=100)
    invoice_date: str | None = None
    currency: str | None = Field(default=None, max_length=10)
    line_items: list[CorrectedLineItem] | None = Field(default=None, max_length=500)
    tax_total: Decimal | None = None
    total: Decimal | None = None


class ReviewRequest(BaseModel):
    action: Literal["save", "approve", "reopen"]
    corrected: CorrectedInvoice | None = None
    note: str | None = Field(default=None, max_length=2000)


@protected.patch("/documents/{doc_id}/review")
def review_document(doc_id: str, body: ReviewRequest, request: Request):
    settings, db, _ = _state(request)
    row = _get_or_404(db, doc_id)
    stored = db.read_json(doc_id, "model_output.json")
    if row["status"] == "processing":
        return _error(409, "STILL_PROCESSING", "The document is still being processed.")

    if body.action == "reopen":
        db.update_document(doc_id, review_status="pending", reviewed_at=None)
        return get_result(doc_id, request)

    validation = None
    if body.corrected is not None:
        data = body.corrected.model_dump()
        if not data["is_invoice"]:
            data = {"is_invoice": False}
        if data.get("line_items") == []:
            data["line_items"] = None
        try:
            corrected = InvoiceResult.model_validate({**data, "needs_review": False})
        except ValidationError as exc:
            errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
            return _error(422, "INVALID_CORRECTION", "The corrected values are not valid.", errors=errors)
        extras_raw = ((stored or {}).get("diagnostics") or {}).get("extras") or {}
        extras = {k: Decimal(str(extras_raw[k])) if extras_raw.get(k) is not None else None
                  for k in ("subtotal", "discount_total", "other_charges_total")}
        validation, _ = check_financials(corrected, extras, settings)
        db.write_json(doc_id, "corrected.json", {
            "invoice": corrected.model_dump(mode="json"),
            "validation": validation,
            "note": body.note,
        })
        db.update_document(
            doc_id,
            vendor=corrected.vendor,
            invoice_number=corrected.invoice_number,
            invoice_date=corrected.invoice_date.isoformat() if corrected.invoice_date else None,
            currency=corrected.currency,
            total=float(corrected.total) if corrected.total is not None else None,
            is_invoice=int(corrected.is_invoice),
        )
    elif body.action == "approve" and stored is None:
        return _error(422, "NOTHING_TO_APPROVE",
                      "This document has no model output; provide corrected values to approve it.")

    if body.action == "approve":
        db.update_document(doc_id, review_status="approved", reviewed_at=utcnow(), review_note=body.note)
    else:
        db.update_document(doc_id, review_status="in_review", review_note=body.note)
    result = get_result(doc_id, request)
    if isinstance(result, dict) and validation is not None:
        result["corrected_validation"] = validation
    return result


# ---------------------------------------------------------------------------
# Stats & evaluation
# ---------------------------------------------------------------------------

@protected.get("/stats")
def stats(request: Request):
    settings, db, _ = _state(request)
    recent, _ = db.list(limit=8)
    return {
        **db.stats(),
        "recent_documents": [_public_doc(r) for r in recent],
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
    }


@protected.get("/evaluation")
def evaluation(request: Request):
    settings: Settings = request.app.state.settings
    path = settings.reports_dir / "evaluation.json"
    if not path.is_file():
        return _error(404, "NO_EVALUATION", "No evaluation has been run yet. Run `make eval` to generate reports.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _error(500, "EVALUATION_UNREADABLE", "reports/evaluation.json could not be read.")
