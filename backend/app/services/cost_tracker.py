"""LLM cost tracking, budget guard and on-disk result cache."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from backend.app.config import Settings

PER_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class ModelPricing:
    """USD per 1M tokens."""

    input_per_1m: Decimal
    cached_input_per_1m: Decimal
    output_per_1m: Decimal
    source: str = "generic"  # override | built-in | generic (PRICE_* env values)

    def as_dict(self) -> dict[str, float | str]:
        return {
            "input_per_1m_usd": float(self.input_per_1m),
            "cached_input_per_1m_usd": float(self.cached_input_per_1m),
            "output_per_1m_usd": float(self.output_per_1m),
            "source": self.source,
        }


# Published list prices (USD per 1M tokens: input, cached input, output) for the default models.
# Prices change: verify on the provider's pricing page, and override with MODEL_PRICING_JSON.
#   gpt-4.1-mini            -> platform.openai.com/docs/pricing
#   grok-4-1-fast-reasoning -> docs.x.ai/developers/models/grok-4-1-fast-reasoning (reasoning tokens bill as output)
#   Groq models             -> console.groq.com/docs/models (qwen3.8-27b is a preview model; no cached rate
#                              published, so cached tokens are priced at the full input rate)
BUILT_IN_PRICING: dict[str, tuple[str, str, str]] = {
    "gpt-4.1-mini": ("0.40", "0.10", "1.60"),
    "grok-4-1-fast-reasoning": ("0.20", "0.05", "0.50"),
    "openai/gpt-oss-120b": ("0.15", "0.075", "0.60"),
    "openai/gpt-oss-20b": ("0.075", "0.0375", "0.30"),
    "qwen/qwen3.8-27b": ("0.80", "0.80", "4.00"),
}


def model_id(model: str) -> str:
    """Strip a "provider:" label ("xai:grok-4-1-fast-reasoning" -> "grok-4-1-fast-reasoning")."""
    return model.split(":", 1)[1] if ":" in model else model


def get_pricing(model: str, settings: Settings) -> ModelPricing:
    """MODEL_PRICING_JSON override > built-in table > generic PRICE_* values."""
    name = model_id(model)
    override = settings.model_pricing_overrides.get(name) or settings.model_pricing_overrides.get(model)
    if override:
        return ModelPricing(
            Decimal(str(override["input"])),
            Decimal(str(override.get("cached_input", override["input"]))),
            Decimal(str(override["output"])),
            "override",
        )
    if name in BUILT_IN_PRICING:
        i, c, o = BUILT_IN_PRICING[name]
        return ModelPricing(Decimal(i), Decimal(c), Decimal(o), "built-in")
    return ModelPricing(
        settings.price_input_per_1m,
        settings.price_cached_input_per_1m,
        settings.price_output_per_1m,
        "generic",
    )


def compute_cost(
    input_tokens: int, output_tokens: int, pricing: ModelPricing, cached_input_tokens: int = 0
) -> Decimal:
    """Cost = uncached input × input rate + cached input × cached rate + output × output rate.

    ``input_tokens`` is the provider's prompt token count, which already *includes*
    cached tokens (OpenAI semantics), and image tokens. ``output_tokens`` includes
    any reasoning tokens.
    """
    cached = max(0, min(cached_input_tokens, input_tokens))
    uncached = input_tokens - cached
    cost = (
        Decimal(uncached) * pricing.input_per_1m
        + Decimal(cached) * pricing.cached_input_per_1m
        + Decimal(output_tokens) * pricing.output_per_1m
    ) / PER_MILLION
    return cost.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


class BudgetExceeded(Exception):
    pass


class BudgetGuard:
    """Stops new LLM calls once a spending limit is reached (used by evaluation runs)."""

    def __init__(self, limit_usd: Decimal | None):
        self.limit = limit_usd
        self.spent = Decimal(0)
        self._lock = threading.Lock()

    def check(self) -> None:
        if self.limit is not None and self.spent >= self.limit:
            raise BudgetExceeded(f"Evaluation budget of ${self.limit} reached (spent ${self.spent:.4f}).")

    def add(self, cost: Decimal) -> None:
        with self._lock:
            self.spent += cost


class ResultCache:
    """Caches successful extraction outcomes by document hash + pipeline configuration."""

    def __init__(self, directory: Path, enabled: bool = True):
        self.directory = directory
        self.enabled = enabled
        if enabled:
            directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def make_key(sha256: str, settings: Settings, prompt_version: str, pipeline_version: str) -> str:
        parts = [
            sha256,
            settings.llm_provider,
            settings.vision_provider,
            settings.llm_model,
            settings.llm_vision_model,
            settings.routing_mode,
            prompt_version,
            pipeline_version,
            str(settings.validation_retry),
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, key: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
        tmp.replace(self._path(key))
