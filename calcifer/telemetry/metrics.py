"""Metrics: OTel-based counters and histograms.

Tracks:
- Token consumption (input/output/cache by model)
- Tool execution (count, duration, errors by tool)
- LLM requests (count, latency, errors by model)
- Compaction events (count, tokens freed)
- Cost (estimated USD)

Noop when OTel not installed.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _has_otel() -> bool:
    try:
        import opentelemetry
        return True
    except ImportError:
        return False


class MetricsManager:
    """Manages OTel metrics for the agent runner."""

    def __init__(self, service_name: str = "calcifer"):
        self._enabled = _has_otel()
        if not self._enabled:
            self._init_noop()
            return

        from opentelemetry import metrics
        meter = metrics.get_meter(service_name)

        # -- Token counters --
        self.input_tokens = meter.create_counter(
            "calcifer.tokens.input",
            description="Total input tokens consumed",
            unit="tokens",
        )
        self.output_tokens = meter.create_counter(
            "calcifer.tokens.output",
            description="Total output tokens consumed",
            unit="tokens",
        )
        self.cache_read_tokens = meter.create_counter(
            "calcifer.tokens.cache_read",
            description="Tokens served from cache",
            unit="tokens",
        )

        # -- LLM request metrics --
        self.llm_requests = meter.create_counter(
            "calcifer.llm.requests",
            description="Total LLM API requests",
        )
        self.llm_errors = meter.create_counter(
            "calcifer.llm.errors",
            description="LLM API errors",
        )
        self.llm_latency = meter.create_histogram(
            "calcifer.llm.latency_ms",
            description="LLM request latency",
            unit="ms",
        )
        self.llm_ttft = meter.create_histogram(
            "calcifer.llm.ttft_ms",
            description="Time to first token",
            unit="ms",
        )

        # -- Tool metrics --
        self.tool_calls = meter.create_counter(
            "calcifer.tool.calls",
            description="Total tool calls",
        )
        self.tool_errors = meter.create_counter(
            "calcifer.tool.errors",
            description="Tool execution errors",
        )
        self.tool_duration = meter.create_histogram(
            "calcifer.tool.duration_ms",
            description="Tool execution duration",
            unit="ms",
        )

        # -- Compaction metrics --
        self.compactions = meter.create_counter(
            "calcifer.compact.count",
            description="Compaction events",
        )
        self.tokens_freed = meter.create_counter(
            "calcifer.compact.tokens_freed",
            description="Tokens freed by compaction",
            unit="tokens",
        )

        # -- Agent metrics --
        self.agent_runs = meter.create_counter(
            "calcifer.agent.runs",
            description="Total agent.run() invocations",
        )
        self.agent_turns = meter.create_histogram(
            "calcifer.agent.turns",
            description="Turns per agent run",
        )
        self.estimated_cost = meter.create_counter(
            "calcifer.cost.usd",
            description="Estimated cost in USD",
            unit="usd",
        )

    def _init_noop(self) -> None:
        """Initialize noop counters when OTel not available."""
        noop = _NoopInstrument()
        self.input_tokens = noop
        self.output_tokens = noop
        self.cache_read_tokens = noop
        self.llm_requests = noop
        self.llm_errors = noop
        self.llm_latency = noop
        self.llm_ttft = noop
        self.tool_calls = noop
        self.tool_errors = noop
        self.tool_duration = noop
        self.compactions = noop
        self.tokens_freed = noop
        self.agent_runs = noop
        self.agent_turns = noop
        self.estimated_cost = noop

    # -- Convenience methods --

    def record_llm_request(
        self,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        latency_ms: float = 0.0,
        ttft_ms: float = 0.0,
        success: bool = True,
    ) -> None:
        attrs = {"model": model}
        self.llm_requests.add(1, attrs)
        self.input_tokens.add(input_tokens, attrs)
        self.output_tokens.add(output_tokens, attrs)
        if cache_read_tokens:
            self.cache_read_tokens.add(cache_read_tokens, attrs)
        if latency_ms:
            self.llm_latency.record(latency_ms, attrs)
        if ttft_ms:
            self.llm_ttft.record(ttft_ms, attrs)
        if not success:
            self.llm_errors.add(1, attrs)

    def record_tool_call(
        self,
        tool_name: str,
        duration_ms: float = 0.0,
        success: bool = True,
    ) -> None:
        attrs = {"tool": tool_name}
        self.tool_calls.add(1, attrs)
        if duration_ms:
            self.tool_duration.record(duration_ms, attrs)
        if not success:
            self.tool_errors.add(1, attrs)

    def record_compaction(
        self,
        compact_type: str,
        tokens_freed: int = 0,
    ) -> None:
        attrs = {"type": compact_type}
        self.compactions.add(1, attrs)
        if tokens_freed:
            self.tokens_freed.add(tokens_freed, attrs)

    def record_agent_run(
        self,
        turns: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        self.agent_runs.add(1)
        if turns:
            self.agent_turns.record(turns)
        if cost_usd:
            self.estimated_cost.add(cost_usd)


class _NoopInstrument:
    """Noop instrument for when OTel is not available."""

    def add(self, value: Any = 0, attributes: Any = None) -> None:
        pass

    def record(self, value: Any = 0, attributes: Any = None) -> None:
        pass
