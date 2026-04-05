"""Thinking configuration: adaptive extended thinking support.

Mirrors Claude Code's utils/thinking.ts:
- Adaptive thinking detection by model
- Budget token configuration
- Thinking overhead estimation for token counting
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ThinkingMode(str, Enum):
    DISABLED = "disabled"
    ADAPTIVE = "adaptive"     # Model decides when to think
    ENABLED = "enabled"       # Always think


@dataclass
class ThinkingConfig:
    """Configuration for extended thinking."""

    mode: ThinkingMode = ThinkingMode.DISABLED
    budget_tokens: int = 10_000  # Max tokens for thinking

    def to_api_params(self) -> dict:
        """Convert to API parameters (Anthropic thinking format)."""
        if self.mode == ThinkingMode.DISABLED:
            return {}
        return {
            "thinking": {
                "type": self.mode.value,
                "budget_tokens": self.budget_tokens,
            }
        }


# Models that support adaptive thinking
THINKING_CAPABLE_MODELS = {
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
}

# Minimum thinking budget for token estimation
MIN_THINKING_BUDGET = 1024


def should_enable_thinking(model: str) -> bool:
    """Check if a model supports adaptive thinking by default."""
    return any(m in model for m in THINKING_CAPABLE_MODELS)


def estimate_thinking_overhead(config: ThinkingConfig) -> int:
    """Estimate token overhead from thinking for budget calculations."""
    if config.mode == ThinkingMode.DISABLED:
        return 0
    return MIN_THINKING_BUDGET
