"""End-to-end extraction pipeline.

    validate file -> digital text / OCR -> route -> LLM -> schema validation
    -> financial + grounding checks -> (one targeted retry) -> review decision

Every LLM call (including retries and repairs) is recorded with real token usage
and cost in the diagnostics.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.app.config import PIPELINE_VERSION, Settings, get_settings
from backend.app.schemas.invoice import (
    REQUIRED_FIELDS,
    Diagnostics,
    InvoiceResult,
    LLMAttempt,
    ReviewIssue,
    SourceInfo,
    UsageSummary,
)
from backend.app.services import prompts
from backend.app.services.cost_tracker import (
    BudgetExceeded,
    BudgetGuard,
    ResultCache,
    compute_cost,
    get_pricing,
)
from backend.app.services.file_validation import DocumentError, validate_bytes
from backend.app.services.llm_client import LLMClient, LLMError, build_llm_client
from backend.app.services.pdf_processor import DocumentContent, load_document
from backend.app.services.review import (
    compare_attempts,
    document_level_checks,
    issue_score,
    review_reasons,
    to_review_issues,
)
from backend.app.services.validator import (
    Check,
    NormalizedOutput,
    OutputSchemaError,
    check_financials,
    check_grounding,
    check_line_arithmetic,
    check_required_fields,
    normalize_llm_output,
)

logger = logging.getLogger(__name__)

# Route reliability used to break ties between two attempts with equal problems.
ROUTE_PREFERENCE = {"digital_text": 0, "vision": 1, "ocr_text": 2, "none": 3}
INVALID_OUTPUT_CODES = {"LLM_EMPTY_RESPONSE", "LLM_MALFORMED_RESPONSE"}


@dataclass
class ExtractionOutcome:
    invoice: InvoiceResult | None
    diagnostics: Diagnostics

    @property
    def ok(self) -> bool:
        return self.diagnostics.status == "success" and self.invoice is not None

    def public_json(self) -> dict[str, Any]:
        """The 9 required fields. Failures are NOT reported as non-invoices: is_invoice is null."""
        if self.invoice is not None:
            return self.invoice.model_dump(mode="json")
        payload: dict[str, Any] = {name: None for name in REQUIRED_FIELDS}
        payload["needs_review"] = True
        return payload

    def full_json(self) -> dict[str, Any]:
        return {
            "result": self.public_json(),
            "review_reasons": self.diagnostics.review_reasons,
            "diagnostics": self.diagnostics.model_dump(mode="json"),
        }


@dataclass
class Candidate:
    normalized: NormalizedOutput
    route: str
    checks: list[Check] = field(default_factory=list)
    financial: dict[str, Any] = field(default_factory=dict)
    grounding: dict[str, Any] = field(default_factory=dict)
    repaired: bool = False

    @property
    def invoice(self) -> InvoiceResult:
        return self.normalized.invoice


class _Failure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise json.JSONDecodeError("top-level JSON value is not an object", text, 0)
    return value


class InvoiceExtractor:
    def __init__(
        self,
        settings: Settings | None = None,
        llm_client: LLMClient | None = None,
        cache: ResultCache | None = None,
        budget: BudgetGuard | None = None,
    ):
        self.settings = settings or get_settings()
        self._llm = llm_client
        self.cache = cache if cache is not None else ResultCache(self.settings.cache_dir, self.settings.cache_enabled)
        self.budget = budget

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            if not self.settings.llm_configured:
                raise LLMError("LLM_NOT_CONFIGURED", "No LLM API key configured (set LLM_API_KEY).")
            self._llm = build_llm_client(self.settings)
        return self._llm

    # ------------------------------------------------------------------ public
    def extract_file(self, path: Path, use_cache: bool = True) -> ExtractionOutcome:
        path = Path(path)
        try:
            size = path.stat().st_size
        except OSError:
            return self._failed_outcome("FILE_NOT_FOUND", f"Cannot read {path.name}.", time.perf_counter())
        if size > self.settings.max_upload_bytes:
            return self._failed_outcome(
                "FILE_TOO_LARGE", f"File exceeds the {self.settings.max_upload_mb} MB limit.", time.perf_counter()
            )
        return self.extract_bytes(path.read_bytes(), use_cache=use_cache)

    def extract_bytes(self, data: bytes, use_cache: bool = True) -> ExtractionOutcome:
        started = time.perf_counter()
        diag = self._new_diagnostics()
        try:
            info = validate_bytes(data, self.settings.max_upload_bytes)
            diag.source = SourceInfo(kind=info.kind, mime_type=info.mime_type, size_bytes=info.size_bytes, sha256=info.sha256)
            cache_key = ResultCache.make_key(info.sha256, self.settings, prompts.PROMPT_VERSION, PIPELINE_VERSION)
            if use_cache:
                cached = self.cache.get(cache_key)
                if cached:
                    return self._from_cache(cached, started)

            content = load_document(data, info, self.settings)
            self._fill_source(diag.source, content)
            outcome = self._run_pipeline(content, diag, started)
            if outcome.ok:
                self.cache.put(cache_key, outcome.full_json())
            return outcome
        except DocumentError as exc:
            return self._fail(diag, exc.code, exc.message, started)
        except LLMError as exc:
            return self._fail(diag, exc.code, exc.message, started)
        except _Failure as exc:
            return self._fail(diag, exc.code, exc.message, started)
        except BudgetExceeded as exc:
            return self._fail(diag, "BUDGET_EXCEEDED", str(exc), started)
        except Exception:  # never leak document content into logs
            logger.exception("Unexpected extraction error")
            return self._fail(diag, "INTERNAL_ERROR", "An unexpected error occurred while processing the document.", started)

    # ---------------------------------------------------------------- pipeline
    def _run_pipeline(self, content: DocumentContent, diag: Diagnostics, started: float) -> ExtractionOutcome:
        route, route_reason = self.plan_route(content)
        diag.route, diag.route_reason = route, route_reason

        first = self._extract_candidate(content, route, "initial", None, diag)
        candidates = [first]
        retry_checks = [c for c in first.checks if c.retryable and c.severity == "review"]
        if self.settings.validation_retry and first.invoice.is_invoice and retry_checks:
            retry_route = "vision" if self.settings.routing_mode != "text_only" else route
            feedback = (
                "A previous extraction of this document failed automatic checks:\n"
                + "\n".join(f"- {c.code}: {c.message}" for c in retry_checks)
                + "\nRe-read the document carefully and extract it again. Correct only values that were misread "
                "or missed. Do NOT change numbers just to make the arithmetic balance: if the printed figures "
                "genuinely do not add up, return them exactly as printed. Use null for anything you cannot read."
            )
            try:
                candidates.append(self._extract_candidate(content, retry_route, "validation_retry", feedback, diag))
            except (_Failure, LLMError) as exc:
                code = getattr(exc, "code", "RETRY_FAILED")
                first.checks.append(Check("RETRY_FAILED", f"validation retry failed ({code}); kept first result", "info"))

        final = min(candidates, key=lambda c: (issue_score(c.checks), ROUTE_PREFERENCE.get(c.route, 9)))
        extra_checks: list[Check] = []
        if len(candidates) == 2:
            diffs = compare_attempts(candidates[0].invoice, candidates[1].invoice)
            unresolved = issue_score(final.checks)[0] > 0
            if "is_invoice" in diffs or (diffs and unresolved):
                extra_checks.append(
                    Check("LLM_RESULT_CONFLICT", f"the two extraction attempts disagree on: {', '.join(diffs)}", "review")
                )
            elif diffs:
                extra_checks.append(
                    Check("RETRY_CHANGED_VALUES", f"retry corrected: {', '.join(diffs)} (now passes checks)", "info")
                )
            diag.validation["retry_selected"] = final is candidates[1]

        issues: list[ReviewIssue] = to_review_issues(final.checks + extra_checks)
        reasons = review_reasons(issues)
        invoice = final.invoice.model_copy(update={"needs_review": bool(reasons)})

        extras = final.normalized.extras
        diag.status = "success"
        diag.route = final.route  # type: ignore[assignment]
        diag.document_type = extras.get("document_type")
        diag.review_details = issues
        diag.review_reasons = reasons
        diag.llm_reported_issues = extras.get("issues", [])
        diag.extras = {
            "subtotal": _f(extras.get("subtotal")),
            "discount_total": _f(extras.get("discount_total")),
            "other_charges_total": _f(extras.get("other_charges_total")),
            "invoice_date_as_printed": extras.get("invoice_date_as_printed"),
            "line_item_unit_prices": [_f(u) for u in extras.get("unit_prices", [])],
        }
        diag.validation.update(
            {
                "schema_valid": True,
                "schema_valid_first_try": not first.repaired,
                "financial": final.financial,
                "grounding": final.grounding,
                "checks": [{"code": c.code, "severity": c.severity, "message": c.message} for c in final.checks + extra_checks],
            }
        )
        self._finalise_usage(diag)
        diag.processing_ms = _ms(started)
        return ExtractionOutcome(invoice, diag)

    def plan_route(self, content: DocumentContent) -> tuple[str, str]:
        s = self.settings
        mode = s.routing_mode
        if mode == "vision_always":
            return "vision", "ROUTING_MODE=vision_always"
        if mode == "text_only":
            if not content.has_text:
                raise DocumentError("NO_TEXT_EXTRACTED", "No text could be extracted and vision is disabled (text_only).")
            return ("digital_text" if content.all_digital else "ocr_text"), "ROUTING_MODE=text_only"
        if content.all_digital:
            return "digital_text", "every page has a usable embedded text layer (exact text, no OCR or images needed)"
        conf, words = content.ocr_mean_confidence, content.ocr_word_count
        if mode == "ocr_first" and content.scanned_layer_pages == 0:
            if conf is not None and conf >= s.ocr_route_min_confidence and words >= s.ocr_route_min_words:
                return "ocr_text", f"OCR quality acceptable (mean confidence {conf}, {words} words)"
            return "vision", f"OCR unreliable (mean confidence {conf}, {words} words): page images + OCR hint"
        return "vision", "scanned/photographed content: page images sent with OCR text as a hint"

    # ------------------------------------------------------------- LLM steps
    def _extract_candidate(
        self, content: DocumentContent, route: str, purpose: str, feedback: str | None, diag: Diagnostics
    ) -> Candidate:
        text = content.text(self.settings.max_text_chars)
        images: list[bytes] = []
        if route == "vision":
            images = content.vision_images(self.settings.max_vision_pages, self.settings.vision_max_side_px)
            if not images:
                raise _Failure("RENDER_FAILED", "Could not render page images for the vision model.")
        model = self.settings.llm_vision_model if route == "vision" else self.settings.llm_model
        user = prompts.build_user_message(route, text, len(images), content.pages_total, feedback)

        content_str = self._call(route, model, user, images, purpose, diag)
        repaired = False
        try:
            normalized = self._parse(content_str)
        except (json.JSONDecodeError, OutputSchemaError) as exc:
            errors = exc.errors if isinstance(exc, OutputSchemaError) else [f"invalid JSON: {exc.msg}"]
            repair = (
                (feedback + "\n\n" if feedback else "")
                + "Your previous answer could not be used because it was not valid for the schema:\n"
                + "\n".join(f"- {e}" for e in errors[:10])
                + "\nReturn the complete, corrected JSON object only."
            )
            user = prompts.build_user_message(route, text, len(images), content.pages_total, repair)
            content_str = self._call(route, model, user, images, "repair", diag)
            try:
                normalized = self._parse(content_str)
            except (json.JSONDecodeError, OutputSchemaError) as exc2:
                msg = "; ".join(exc2.errors[:3]) if isinstance(exc2, OutputSchemaError) else str(exc2)
                raise _Failure("LLM_INVALID_OUTPUT", f"The model returned invalid output twice ({msg}).")
            repaired = True

        candidate = Candidate(normalized, route, list(normalized.checks), repaired=repaired)
        invoice = normalized.invoice
        candidate.checks += check_required_fields(invoice)
        candidate.financial, fin_checks = check_financials(invoice, normalized.extras, self.settings)
        candidate.checks += fin_checks
        candidate.checks += check_line_arithmetic(invoice, normalized.extras)
        grounding_text = text if route != "vision" else content.text(self.settings.max_text_chars)
        candidate.grounding, ground_checks = check_grounding(invoice, grounding_text, route)
        candidate.checks += ground_checks
        candidate.checks += document_level_checks(invoice, normalized.extras, content, route, self.settings)
        return candidate

    def _parse(self, content_str: str) -> NormalizedOutput:
        return normalize_llm_output(parse_json_content(content_str), self.settings)

    def _call(self, route: str, model: str, user: str, images: list[bytes], purpose: str, diag: Diagnostics) -> str:
        if self.budget:
            self.budget.check()
        number = len(diag.attempts) + 1
        provider = self.settings.vision_provider if route == "vision" else self.settings.llm_provider
        label = model if provider == "mock" else f"{provider}:{model}"
        attempt = LLMAttempt(number=number, purpose=purpose, route=route, model=label)  # type: ignore[arg-type]
        diag.attempts.append(attempt)
        try:
            response = self.llm.complete(prompts.SYSTEM_PROMPT, user, images, prompts.EXTRACTION_SCHEMA, model)
        except LLMError as exc:
            self._record_calls(attempt, exc.calls, model)
            attempt.outcome, attempt.error = "error", exc.code
            if exc.code in INVALID_OUTPUT_CODES:
                return ""  # treated as invalid JSON -> repair path
            raise
        self._record_calls(attempt, response.calls, model)
        served = next((c for c in reversed(response.calls) if c.ok), None)
        if response.provider:
            attempt.model = f"{response.provider}:{(served.model if served and served.model else model)}"
        else:
            attempt.model = response.model or model
        if response.fallback_used:
            attempt.outcome = "fallback"
        if response.finish_reason == "length":
            attempt.outcome = "truncated"
        return response.content

    def _record_calls(self, attempt: LLMAttempt, calls: list, requested_model: str) -> None:
        """Sum usage and price each HTTP call with the rates of the model that served it."""
        attempt.http_calls = len(calls)
        attempt.input_tokens = sum(c.input_tokens for c in calls)
        attempt.cached_input_tokens = sum(c.cached_input_tokens for c in calls)
        attempt.output_tokens = sum(c.output_tokens for c in calls)
        attempt.latency_ms = sum(c.latency_ms for c in calls)
        cost = Decimal(0)
        for call in calls:
            pricing = get_pricing(getattr(call, "model", None) or requested_model, self.settings)
            cost += compute_cost(call.input_tokens, call.output_tokens, pricing, call.cached_input_tokens)
        attempt.cost_usd = float(cost)
        if self.budget:
            self.budget.add(cost)

    # ---------------------------------------------------------------- helpers
    def _new_diagnostics(self) -> Diagnostics:
        return Diagnostics(
            status="failed",
            prompt_version=prompts.PROMPT_VERSION,
            pipeline_version=PIPELINE_VERSION,
            routing_mode=self.settings.routing_mode,
        )

    @staticmethod
    def _fill_source(source: SourceInfo, content: DocumentContent) -> None:
        source.pages_total = content.pages_total
        source.pages_processed = len(content.pages)
        source.digital_pages = content.digital_pages
        source.ocr_pages = content.ocr_pages
        source.scanned_with_text_layer_pages = content.scanned_layer_pages
        source.ocr_mean_confidence = content.ocr_mean_confidence
        source.ocr_word_count = content.ocr_word_count
        source.ocr_error = content.ocr_error
        source.text_chars = sum(len(p.text) for p in content.pages)
        source.truncated_pages = content.truncated

    @staticmethod
    def _finalise_usage(diag: Diagnostics) -> None:
        attempts = diag.attempts
        usage = UsageSummary(
            models=sorted({a.model for a in attempts}),
            llm_attempts=len(attempts),
            http_calls=sum(a.http_calls for a in attempts),
            input_tokens=sum(a.input_tokens for a in attempts),
            cached_input_tokens=sum(a.cached_input_tokens for a in attempts),
            output_tokens=sum(a.output_tokens for a in attempts),
            cost_usd=round(sum(a.cost_usd for a in attempts), 8),
            retry_cost_usd=round(sum(a.cost_usd for a in attempts if a.purpose != "initial"), 8),
        )
        usage.total_tokens = usage.input_tokens + usage.output_tokens
        diag.usage = usage

    def _fail(self, diag: Diagnostics, code: str, message: str, started: float) -> ExtractionOutcome:
        diag.status = "failed"
        diag.error_code = code
        diag.error_message = message
        diag.review_reasons = ["PROCESSING_FAILED"]
        diag.review_details = [ReviewIssue(code="PROCESSING_FAILED", message=f"{code}: {message}")]
        self._finalise_usage(diag)
        diag.processing_ms = _ms(started)
        return ExtractionOutcome(None, diag)

    def _failed_outcome(self, code: str, message: str, started: float) -> ExtractionOutcome:
        return self._fail(self._new_diagnostics(), code, message, started)

    def _from_cache(self, cached: dict[str, Any], started: float) -> ExtractionOutcome:
        diag = Diagnostics.model_validate(cached["diagnostics"])
        diag.original_cost_usd = diag.usage.cost_usd if diag.original_cost_usd is None else diag.original_cost_usd
        if diag.original_processing_ms is None:
            diag.original_processing_ms = diag.processing_ms
        diag.cached = True
        diag.usage = UsageSummary(models=diag.usage.models)  # nothing was spent on this run
        diag.processing_ms = _ms(started)
        invoice = InvoiceResult.model_validate(cached["result"])
        return ExtractionOutcome(invoice, diag)


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
