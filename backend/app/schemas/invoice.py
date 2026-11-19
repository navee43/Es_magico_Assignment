"""Pydantic models for the required invoice JSON and the separate diagnostics."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from backend.app.services.normalization import ISO_CURRENCIES, ISO_DATE_RE


def _money_json(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _quantity_json(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


Money = Annotated[Decimal, PlainSerializer(_money_json, return_type=float | None, when_used="json")]
Quantity = Annotated[
    Decimal, PlainSerializer(_quantity_json, return_type=int | float | None, when_used="json")
]

REQUIRED_FIELDS = (
    "is_invoice",
    "vendor",
    "invoice_number",
    "invoice_date",
    "currency",
    "line_items",
    "tax_total",
    "total",
    "needs_review",
)
INVOICE_FIELDS = REQUIRED_FIELDS[1:-1]  # fields that must be null for non-invoices


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    quantity: Quantity | None = None
    amount: Money | None = None

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        return value or None

    @field_validator("quantity", "amount")
    @classmethod
    def _finite(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("must be a finite number")
        return value


class InvoiceResult(BaseModel):
    """Exactly the JSON shape required by the assignment."""

    model_config = ConfigDict(extra="forbid")

    is_invoice: bool
    vendor: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    currency: str | None = Field(default=None, description="ISO-4217 code")
    line_items: list[LineItem] | None = None
    tax_total: Money | None = None
    total: Money | None = None
    needs_review: bool = False

    @field_validator("invoice_date", mode="before")
    @classmethod
    def _iso_date(cls, value: Any) -> Any:
        if value is None or isinstance(value, date):
            return value
        if not isinstance(value, str) or not ISO_DATE_RE.match(value.strip()):
            raise ValueError("invoice_date must be an ISO date string (YYYY-MM-DD)")
        return value.strip()

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = value.strip().upper()
        if code not in ISO_CURRENCIES:
            raise ValueError(f"'{value}' is not an ISO-4217 currency code")
        return code

    @field_validator("vendor", "invoice_number")
    @classmethod
    def _clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        return value or None

    @field_validator("tax_total", "total")
    @classmethod
    def _finite(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("must be a finite number")
        return value

    @model_validator(mode="after")
    def _non_invoice_nulls(self) -> "InvoiceResult":
        if not self.is_invoice:
            filled = [f for f in INVOICE_FIELDS if getattr(self, f) is not None]
            if filled:
                raise ValueError(
                    "non-invoice documents must have null invoice fields; got values for "
                    + ", ".join(filled)
                )
        return self

    @staticmethod
    def non_invoice(needs_review: bool = False) -> "InvoiceResult":
        return InvoiceResult(is_invoice=False, needs_review=needs_review)


# ---------------------------------------------------------------------------
# Diagnostics: kept separate from the required invoice fields.
# ---------------------------------------------------------------------------

Route = Literal["digital_text", "ocr_text", "vision", "none"]


class ReviewIssue(BaseModel):
    code: str
    message: str
    severity: Literal["review", "info"] = "review"


class LLMAttempt(BaseModel):
    """One logical extraction attempt (may include several HTTP retries)."""

    number: int
    purpose: Literal["initial", "repair", "validation_retry"]
    route: Route
    model: str
    http_calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    outcome: str = "ok"
    error: str | None = None


class UsageSummary(BaseModel):
    models: list[str] = []
    llm_attempts: int = 0
    http_calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    retry_cost_usd: float = 0.0


class SourceInfo(BaseModel):
    kind: Literal["pdf", "image", "unknown"] = "unknown"
    mime_type: str | None = None
    size_bytes: int = 0
    sha256: str | None = None
    pages_total: int = 0
    pages_processed: int = 0
    digital_pages: int = 0
    ocr_pages: int = 0
    scanned_with_text_layer_pages: int = 0
    ocr_mean_confidence: float | None = None
    ocr_word_count: int = 0
    ocr_error: str | None = None  # e.g. TESSERACT_UNAVAILABLE, OCR_TIMEOUT
    text_chars: int = 0
    truncated_pages: bool = False


class Diagnostics(BaseModel):
    status: Literal["success", "failed"]
    error_code: str | None = None
    error_message: str | None = None
    route: Route = "none"
    route_reason: str | None = None
    document_type: str | None = None
    review_reasons: list[str] = []
    review_details: list[ReviewIssue] = []
    validation: dict[str, Any] = {}
    llm_reported_issues: list[str] = []
    extras: dict[str, Any] = {}
    attempts: list[LLMAttempt] = []
    usage: UsageSummary = UsageSummary()
    source: SourceInfo = SourceInfo()
    processing_ms: int = 0
    cached: bool = False
    original_cost_usd: float | None = None
    original_processing_ms: int | None = None
    prompt_version: str | None = None
    pipeline_version: str | None = None
    routing_mode: str | None = None
