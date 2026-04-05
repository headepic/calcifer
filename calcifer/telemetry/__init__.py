"""Telemetry: OpenTelemetry-based tracing, metrics, and logging.

Three-layer telemetry:
1. Tracing — request-level spans (interaction → LLM → tool)
2. Metrics — aggregated counters/histograms (tokens, costs, errors)
3. Events — structured log events

Gracefully degrades to noop when opentelemetry is not installed.
"""

from .spans import (
    TracingManager,
    get_tracer,
    start_interaction_span,
    end_interaction_span,
    start_llm_span,
    end_llm_span,
    start_tool_span,
    end_tool_span,
    start_compact_span,
    end_compact_span,
)
from .metrics import MetricsManager
from .setup import init_telemetry, shutdown_telemetry

__all__ = [
    "MetricsManager",
    "TracingManager",
    "end_compact_span",
    "end_interaction_span",
    "end_llm_span",
    "end_tool_span",
    "get_tracer",
    "init_telemetry",
    "shutdown_telemetry",
    "start_compact_span",
    "start_interaction_span",
    "start_llm_span",
    "start_tool_span",
]
