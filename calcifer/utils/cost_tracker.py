"""Cost tracker: per-model token consumption and cost estimation.

Mirrors Claude Code's cost-tracker.ts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..types.message import Usage

logger = logging.getLogger(__name__)

# Default pricing (per 1M tokens) — override via set_model_pricing
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
}


@dataclass
class ModelUsage:
    """Usage for a specific model."""
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    api_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class CostTracker:
    """Tracks token consumption and costs across models."""

    _by_model: dict[str, ModelUsage] = field(default_factory=dict)
    _custom_pricing: dict[str, dict[str, float]] = field(default_factory=dict)

    def record(self, model: str, usage: Usage) -> None:
        """Record usage from an API call."""
        if model not in self._by_model:
            self._by_model[model] = ModelUsage(model=model)

        mu = self._by_model[model]
        mu.input_tokens += usage.prompt_tokens
        mu.output_tokens += usage.completion_tokens
        mu.cache_read_tokens += usage.cache_read_input_tokens
        mu.cache_creation_tokens += usage.cache_creation_input_tokens
        mu.api_calls += 1

    def set_model_pricing(self, model: str, input_per_m: float, output_per_m: float) -> None:
        """Set custom pricing for a model."""
        self._custom_pricing[model] = {"input": input_per_m, "output": output_per_m}

    def _get_pricing(self, model: str) -> dict[str, float]:
        if model in self._custom_pricing:
            return self._custom_pricing[model]
        # Fuzzy match
        for key, pricing in DEFAULT_PRICING.items():
            if key in model:
                return pricing
        return {"input": 0.0, "output": 0.0}

    def get_cost(self, model: str | None = None) -> float:
        """Get estimated cost in USD. If model=None, returns total."""
        total = 0.0
        for m, mu in self._by_model.items():
            if model and m != model:
                continue
            pricing = self._get_pricing(m)
            cost = (
                mu.input_tokens * pricing["input"] / 1_000_000
                + mu.output_tokens * pricing["output"] / 1_000_000
            )
            total += cost
        return total

    def get_total_usage(self) -> Usage:
        """Get aggregate usage across all models."""
        total = Usage()
        for mu in self._by_model.values():
            total.prompt_tokens += mu.input_tokens
            total.completion_tokens += mu.output_tokens
            total.cache_read_input_tokens += mu.cache_read_tokens
            total.cache_creation_input_tokens += mu.cache_creation_tokens
            total.total_tokens += mu.total_tokens
        return total

    def summary(self) -> dict[str, Any]:
        """Return a summary of costs by model."""
        return {
            model: {
                "input_tokens": mu.input_tokens,
                "output_tokens": mu.output_tokens,
                "api_calls": mu.api_calls,
                "cost_usd": round(self.get_cost(model), 4),
            }
            for model, mu in self._by_model.items()
        }
