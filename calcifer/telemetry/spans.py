"""Span management: interaction, LLM request, tool execution, compact.

Mirrors Claude Code's sessionTracing.ts span hierarchy:
  Interaction Span (per agent.run())
  ├── LLM Request Span (per API call)
  │   └── attrs: model, tokens, ttft_ms, cache_stats
  ├── Tool Span (per tool call)
  │   ├── Tool Execution Span (actual call)
  │   └── attrs: tool_name, duration_ms, success
  └── Compact Span (if compaction triggered)

Uses contextvars for async context propagation (= Node's AsyncLocalStorage).
Noop when OTel is not installed.
"""

from __future__ import annotations

import contextvars
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Context vars for span propagation
_interaction_span: contextvars.ContextVar[Any] = contextvars.ContextVar("interaction_span", default=None)
_current_span: contextvars.ContextVar[Any] = contextvars.ContextVar("current_span", default=None)


def _has_otel() -> bool:
    try:
        import opentelemetry
        return True
    except ImportError:
        return False


def get_tracer(name: str = "calcifer") -> Any:
    """Get an OTel tracer, or a noop tracer if OTel not installed."""
    if not _has_otel():
        return _NoopTracer()
    from opentelemetry import trace
    return trace.get_tracer(name)


class TracingManager:
    """High-level tracing API for the agent runner."""

    def __init__(self, service_name: str = "calcifer"):
        self._tracer = get_tracer(service_name)
        self._enabled = _has_otel()

    @property
    def enabled(self) -> bool:
        return self._enabled


# -- Interaction Span (root per agent.run()) --

def start_interaction_span(
    prompt: str = "",
    *,
    session_id: str = "",
    chain_id: str = "",
    model: str = "",
) -> Any:
    """Start root interaction span for an agent.run() call."""
    tracer = get_tracer()
    attrs = {
        "calcifer.session_id": session_id,
        "calcifer.chain_id": chain_id,
        "calcifer.model": model,
    }
    if prompt:
        # Truncate prompt for safety (don't log full user content by default)
        attrs["calcifer.prompt_preview"] = prompt[:200]

    span = tracer.start_span("agent.interaction", attributes=attrs)
    _interaction_span.set(span)
    _current_span.set(span)
    return span


def end_interaction_span(
    *,
    turn_count: int = 0,
    total_tokens: int = 0,
    cost_usd: float = 0.0,
    success: bool = True,
    error: str = "",
) -> None:
    """End the root interaction span."""
    span = _interaction_span.get()
    if span is None:
        return
    _set_attrs(span, {
        "calcifer.turn_count": turn_count,
        "calcifer.total_tokens": total_tokens,
        "calcifer.cost_usd": cost_usd,
        "calcifer.success": success,
    })
    if error:
        _set_attrs(span, {"calcifer.error": error[:500]})
    _end_span(span)
    _interaction_span.set(None)


# -- LLM Request Span --

def start_llm_span(
    model: str = "",
    *,
    query_source: str = "",
    attempt: int = 1,
) -> Any:
    """Start a span for an LLM API call."""
    tracer = get_tracer()
    attrs = {
        "calcifer.llm.model": model,
        "calcifer.llm.query_source": query_source,
        "calcifer.llm.attempt": attempt,
    }
    span = tracer.start_span("llm.request", attributes=attrs)
    _current_span.set(span)
    return span


def end_llm_span(
    span: Any = None,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    ttft_ms: float = 0.0,
    success: bool = True,
    status_code: int = 0,
    error: str = "",
    has_tool_calls: bool = False,
) -> None:
    """End an LLM request span with token and timing metadata."""
    if span is None:
        span = _current_span.get()
    if span is None:
        return
    _set_attrs(span, {
        "calcifer.llm.input_tokens": input_tokens,
        "calcifer.llm.output_tokens": output_tokens,
        "calcifer.llm.cache_read_tokens": cache_read_tokens,
        "calcifer.llm.cache_creation_tokens": cache_creation_tokens,
        "calcifer.llm.ttft_ms": ttft_ms,
        "calcifer.llm.success": success,
        "calcifer.llm.status_code": status_code,
        "calcifer.llm.has_tool_calls": has_tool_calls,
    })
    if error:
        _set_attrs(span, {"calcifer.llm.error": error[:500]})
    _end_span(span)


# -- Tool Span --

def start_tool_span(
    tool_name: str,
    *,
    tool_use_id: str = "",
    is_concurrent: bool = False,
    tool_input: str = "",
) -> Any:
    """Start a span for a tool execution."""
    tracer = get_tracer()
    attrs = {
        "calcifer.tool.name": tool_name,
        "calcifer.tool.use_id": tool_use_id,
        "calcifer.tool.is_concurrent": is_concurrent,
    }
    # Only log tool input when explicitly enabled (privacy)
    if tool_input:
        attrs["calcifer.tool.input_preview"] = tool_input[:500]
    span = tracer.start_span(f"tool.{tool_name}", attributes=attrs)
    _current_span.set(span)
    return span


def end_tool_span(
    span: Any = None,
    *,
    success: bool = True,
    error: str = "",
    duration_ms: float = 0.0,
    result_tokens: int = 0,
    result_preview: str = "",
) -> None:
    """End a tool execution span."""
    if span is None:
        span = _current_span.get()
    if span is None:
        return
    _set_attrs(span, {
        "calcifer.tool.success": success,
        "calcifer.tool.duration_ms": duration_ms,
        "calcifer.tool.result_tokens": result_tokens,
    })
    if error:
        _set_attrs(span, {"calcifer.tool.error": error[:500]})
    if result_preview:
        _set_attrs(span, {"calcifer.tool.result_preview": result_preview[:200]})
    _end_span(span)


# -- Compact Span --

def start_compact_span(
    compact_type: str = "autocompact",
) -> Any:
    """Start a span for context compaction."""
    tracer = get_tracer()
    span = tracer.start_span(
        f"compact.{compact_type}",
        attributes={"calcifer.compact.type": compact_type},
    )
    _current_span.set(span)
    return span


def end_compact_span(
    span: Any = None,
    *,
    pre_tokens: int = 0,
    post_tokens: int = 0,
    layers_applied: str = "",
) -> None:
    """End a compaction span."""
    if span is None:
        span = _current_span.get()
    if span is None:
        return
    _set_attrs(span, {
        "calcifer.compact.pre_tokens": pre_tokens,
        "calcifer.compact.post_tokens": post_tokens,
        "calcifer.compact.tokens_freed": pre_tokens - post_tokens,
    })
    if layers_applied:
        _set_attrs(span, {"calcifer.compact.layers": layers_applied})
    _end_span(span)


# -- Helpers --

def _set_attrs(span: Any, attrs: dict[str, Any]) -> None:
    """Set attributes on a span (noop-safe)."""
    if hasattr(span, "set_attribute"):
        for k, v in attrs.items():
            if v is not None:
                try:
                    span.set_attribute(k, v)
                except Exception:
                    pass


def _end_span(span: Any) -> None:
    """End a span (noop-safe)."""
    if hasattr(span, "end"):
        try:
            span.end()
        except Exception:
            pass


# -- Noop fallback --

class _NoopSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def end(self) -> None:
        pass


class _NoopTracer:
    def start_span(self, name: str, **kwargs: Any) -> _NoopSpan:
        return _NoopSpan()
