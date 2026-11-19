"""Application configuration.

All settings come from environment variables (optionally loaded from a local
``.env`` file). Nothing secret is ever hard-coded; see ``.env.example``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Bump when the pipeline logic changes in a way that should invalidate cached results.
PIPELINE_VERSION = "1.1.0"

# Built-in LLM providers. All speak the OpenAI Chat Completions protocol.
#   openai : api.openai.com
#   xai    : Grok models by xAI at api.x.ai (alias "grok"; keys start with "xai-")
#   groq   : GroqCloud at api.groq.com (keys start with "gsk_") - not the same company as Grok
#   custom : any other OpenAI-compatible endpoint (OpenRouter, Gemini's OpenAI endpoint, a local server ...)
#   mock   : offline regex stub for UI testing only
# "model" is used for text routes, "vision_model" for page-image routes.
PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {"base_url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY",
               "base_env": "OPENAI_BASE_URL", "model": "gpt-4.1-mini", "vision_model": "gpt-4.1-mini"},
    "xai": {"base_url": "https://api.x.ai/v1", "key_env": "XAI_API_KEY", "base_env": "XAI_BASE_URL",
            "model": "grok-4-1-fast-reasoning", "vision_model": "grok-4-1-fast-reasoning"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY", "base_env": "GROQ_BASE_URL",
             "model": "openai/gpt-oss-120b", "vision_model": "qwen/qwen3.8-27b"},
    "custom": {"base_url": "", "key_env": "CUSTOM_LLM_API_KEY", "base_env": "CUSTOM_LLM_BASE_URL",
               "model": "", "vision_model": ""},
}
PROVIDER_ALIASES = {"grok": "xai", "x.ai": "xai", "x-ai": "xai", "groqcloud": "groq",
                    "openai-compatible": "custom", "openrouter": "custom"}


def normalize_provider(name: str | None) -> str:
    value = (name or "").strip().lower()
    return PROVIDER_ALIASES.get(value, value)


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Never overrides real env vars.

    Set INVOICE_AI_SKIP_DOTENV=1 to ignore .env (the test suite does this so a developer's
    real keys and settings never leak into tests).
    """
    if os.environ.get("INVOICE_AI_SKIP_DOTENV") == "1" or not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = _env(name)
    return int(value) if value else default


def _float(name: str, default: float) -> float:
    value = _env(name)
    return float(value) if value else default


def _decimal(name: str, default: str) -> Decimal:
    value = _env(name)
    return Decimal(value if value else default)


def _path(name: str, default: Path) -> Path:
    value = _env(name)
    path = Path(value) if value else default
    return path if path.is_absolute() else (PROJECT_ROOT / path)


@dataclass(frozen=True)
class Settings:
    # --- LLM ---------------------------------------------------------------
    llm_provider: str  # provider for text routes: openai | xai | custom | mock
    llm_base_url: str  # endpoint of llm_provider
    llm_api_key: str = field(repr=False)  # key of llm_provider
    llm_model: str
    llm_vision_model: str
    llm_vision_provider: str  # provider for page-image routes ("" = same as llm_provider)
    llm_fallback_provider: str  # used when the primary provider fails ("" = no fallback)
    llm_fallback_model: str
    llm_fallback_vision_model: str
    provider_api_keys: dict[str, str] = field(repr=False)
    provider_base_urls: dict[str, str]
    llm_timeout_s: float
    llm_max_api_retries: int
    llm_response_format: str  # json_schema | json_object | none
    llm_temperature: float | None
    llm_max_output_tokens: int
    llm_max_tokens_param: str  # max_tokens | max_completion_tokens
    llm_image_detail: str  # auto | low | high

    # --- Pricing (USD per 1M tokens) for cost tracking ----------------------
    price_input_per_1m: Decimal
    price_cached_input_per_1m: Decimal
    price_output_per_1m: Decimal
    model_pricing_overrides: dict[str, dict[str, str]]

    # --- Routing / document processing ------------------------------------
    routing_mode: str  # auto | ocr_first | vision_always | text_only
    min_text_chars_per_page: int
    ocr_enabled: bool
    ocr_lang: str
    ocr_dpi: int
    ocr_timeout_s: int
    ocr_route_min_confidence: float
    ocr_route_min_words: int
    ocr_review_min_confidence: float
    ocr_review_min_words: int
    max_vision_pages: int
    vision_max_side_px: int
    max_text_chars: int
    date_day_first: bool

    # --- Validation / review ----------------------------------------------
    math_abs_tolerance: Decimal
    math_per_line_tolerance: Decimal
    validation_retry: bool
    review_on_low_ocr: bool

    # --- Files ---------------------------------------------------------------
    storage_dir: Path
    max_upload_mb: int
    max_pdf_pages: int
    max_image_pixels: int

    # --- Server -------------------------------------------------------------
    auth_enabled: bool
    app_password: str = field(repr=False)
    secret_key: str = field(repr=False)
    token_ttl_hours: int
    cors_origins: tuple[str, ...]
    max_concurrent_extractions: int
    extraction_queue_timeout_s: float
    reports_dir: Path
    frontend_dist: Path

    # --- Cache / budget -----------------------------------------------------
    cache_enabled: bool
    cache_dir: Path
    eval_budget_usd: Decimal

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def vision_provider(self) -> str:
        return self.llm_vision_provider or self.llm_provider

    def endpoint(self, provider: str) -> tuple[str, str]:
        """(base_url, api_key) for a provider. The primary provider uses llm_base_url / llm_api_key."""
        provider = normalize_provider(provider)
        if provider == self.llm_provider:
            return self.llm_base_url, self.llm_api_key
        return self.provider_base_urls.get(provider, ""), self.provider_api_keys.get(provider, "")

    def provider_ready(self, provider: str) -> bool:
        base_url, key = self.endpoint(provider)
        return bool(base_url and key)

    @property
    def llm_configured(self) -> bool:
        if self.llm_provider == "mock":
            return True
        primary = self.provider_ready(self.llm_provider) and self.provider_ready(self.vision_provider)
        return primary or bool(self.llm_fallback_provider and self.provider_ready(self.llm_fallback_provider))

    @staticmethod
    def from_env() -> "Settings":
        _load_dotenv(PROJECT_ROOT / ".env")
        storage = _path("STORAGE_DIR", PROJECT_ROOT / "data" / "runtime")
        provider = normalize_provider(_env("LLM_PROVIDER", "openai")) or "openai"
        preset = PROVIDERS.get(provider, PROVIDERS["custom"])
        keys = {name: _env(p["key_env"]) for name, p in PROVIDERS.items()}
        bases = {name: (_env(p["base_env"]) or p["base_url"]).rstrip("/") for name, p in PROVIDERS.items()}
        model = _env("LLM_MODEL") or preset["model"] or "gpt-4.1-mini"
        vision_provider = normalize_provider(_env("LLM_VISION_PROVIDER"))
        if vision_provider == provider:
            vision_provider = ""
        vision_preset = PROVIDERS.get(vision_provider or provider, {})
        if vision_provider or not _env("LLM_MODEL"):
            default_vision = vision_preset.get("vision_model") or vision_preset.get("model") or model
        else:
            default_vision = model  # an explicit LLM_MODEL on the same provider is reused for images
        vision_model = _env("LLM_VISION_MODEL") or default_vision
        fallback = normalize_provider(_env("LLM_FALLBACK_PROVIDER"))
        fallback_preset = PROVIDERS.get(fallback, {}) if fallback else {}
        fallback_model = _env("LLM_FALLBACK_MODEL") or fallback_preset.get("model", "")
        fallback_vision_model = (_env("LLM_FALLBACK_VISION_MODEL") or fallback_preset.get("vision_model", "")
                                 or fallback_model)
        temperature = _env("LLM_TEMPERATURE", "0")
        pricing_json = _env("MODEL_PRICING_JSON")
        cors = tuple(o.strip() for o in _env("CORS_ORIGINS").split(",") if o.strip())
        return Settings(
            llm_provider=provider,
            llm_base_url=(_env("LLM_BASE_URL") or bases.get(provider, "")).rstrip("/"),
            llm_api_key=_env("LLM_API_KEY") or keys.get(provider, ""),
            llm_model=model,
            llm_vision_model=vision_model,
            llm_vision_provider=vision_provider,
            llm_fallback_provider=fallback,
            llm_fallback_model=fallback_model,
            llm_fallback_vision_model=fallback_vision_model,
            provider_api_keys=keys,
            provider_base_urls=bases,
            llm_timeout_s=_float("LLM_TIMEOUT_S", 90.0),
            llm_max_api_retries=_int("LLM_MAX_API_RETRIES", 2),
            llm_response_format=_env("LLM_RESPONSE_FORMAT", "json_schema").lower(),
            llm_temperature=float(temperature) if temperature else None,
            llm_max_output_tokens=_int("LLM_MAX_OUTPUT_TOKENS", 4096),
            llm_max_tokens_param=_env("LLM_MAX_TOKENS_PARAM", "max_tokens"),
            llm_image_detail=_env("LLM_IMAGE_DETAIL", "high").lower(),
            price_input_per_1m=_decimal("PRICE_INPUT_PER_1M", "0.40"),
            price_cached_input_per_1m=_decimal("PRICE_CACHED_INPUT_PER_1M", "0.10"),
            price_output_per_1m=_decimal("PRICE_OUTPUT_PER_1M", "1.60"),
            model_pricing_overrides=json.loads(pricing_json) if pricing_json else {},
            routing_mode=_env("ROUTING_MODE", "auto").lower(),
            min_text_chars_per_page=_int("MIN_TEXT_CHARS_PER_PAGE", 40),
            ocr_enabled=_bool("OCR_ENABLED", True),
            ocr_lang=_env("OCR_LANG", "eng"),
            ocr_dpi=_int("OCR_DPI", 300),
            ocr_timeout_s=_int("OCR_TIMEOUT_S", 60),
            ocr_route_min_confidence=_float("OCR_ROUTE_MIN_CONFIDENCE", 80.0),
            ocr_route_min_words=_int("OCR_ROUTE_MIN_WORDS", 30),
            ocr_review_min_confidence=_float("OCR_REVIEW_MIN_CONFIDENCE", 50.0),
            ocr_review_min_words=_int("OCR_REVIEW_MIN_WORDS", 10),
            max_vision_pages=_int("MAX_VISION_PAGES", 3),
            vision_max_side_px=_int("VISION_MAX_SIDE_PX", 2000),
            max_text_chars=_int("MAX_TEXT_CHARS", 30000),
            date_day_first=_bool("DATE_DAY_FIRST", True),
            math_abs_tolerance=_decimal("MATH_ABS_TOLERANCE", "0.05"),
            math_per_line_tolerance=_decimal("MATH_PER_LINE_TOLERANCE", "0.01"),
            validation_retry=_bool("VALIDATION_RETRY", True),
            review_on_low_ocr=_bool("REVIEW_ON_LOW_OCR", True),
            storage_dir=storage,
            max_upload_mb=_int("MAX_UPLOAD_MB", 15),
            max_pdf_pages=_int("MAX_PDF_PAGES", 10),
            max_image_pixels=_int("MAX_IMAGE_PIXELS", 60_000_000),
            auth_enabled=_bool("AUTH_ENABLED", True),
            app_password=_env("APP_PASSWORD"),
            secret_key=_env("SECRET_KEY"),
            token_ttl_hours=_int("TOKEN_TTL_HOURS", 12),
            cors_origins=cors,
            max_concurrent_extractions=_int("MAX_CONCURRENT_EXTRACTIONS", 2),
            extraction_queue_timeout_s=_float("EXTRACTION_QUEUE_TIMEOUT_S", 60.0),
            reports_dir=_path("REPORTS_DIR", PROJECT_ROOT / "reports"),
            frontend_dist=_path("FRONTEND_DIST", PROJECT_ROOT / "frontend" / "dist"),
            cache_enabled=_bool("CACHE_ENABLED", True),
            cache_dir=_path("CACHE_DIR", storage / "cache"),
            eval_budget_usd=_decimal("EVAL_BUDGET_USD", "2.00"),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
