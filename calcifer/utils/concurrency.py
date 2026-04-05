"""Concurrency utilities: QueryGuard, ChildAbortController, ContextModifier queue.

Mirrors Claude Code's:
- QueryGuard.ts: 3-state machine preventing concurrent queries
- abortController.ts: parent→child abort propagation with WeakRef
- toolOrchestration.ts: context modifier queuing pattern
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from enum import Enum
from typing import Any, Callable

from ..types.tools import ToolContext

logger = logging.getLogger(__name__)


# -- Query Guard --

class QueryState(str, Enum):
    IDLE = "idle"
    DISPATCHING = "dispatching"
    RUNNING = "running"


class QueryGuard:
    """Prevents concurrent queries on the same agent.

    Three states: idle → dispatching → running → idle.
    Attempting to dispatch while not idle raises an error.
    Tracks generation IDs to detect stale cleanup from cancelled queries.
    """

    def __init__(self) -> None:
        self._state = QueryState.IDLE
        self._generation = 0

    @property
    def state(self) -> QueryState:
        return self._state

    @property
    def is_idle(self) -> bool:
        return self._state == QueryState.IDLE

    def begin(self) -> int:
        """Begin a query. Returns generation ID. Raises if not idle."""
        if self._state != QueryState.IDLE:
            raise RuntimeError(
                f"Cannot start query: agent is {self._state.value}. "
                "Wait for current query to complete or call abort()."
            )
        self._generation += 1
        self._state = QueryState.DISPATCHING
        return self._generation

    def mark_running(self, generation: int) -> None:
        """Transition from dispatching to running."""
        if generation != self._generation:
            return  # Stale
        self._state = QueryState.RUNNING

    def end(self, generation: int) -> None:
        """End a query. Ignored if generation is stale (cancelled)."""
        if generation != self._generation:
            logger.debug("Ignoring stale query end (gen %d, current %d)", generation, self._generation)
            return
        self._state = QueryState.IDLE

    def force_idle(self) -> None:
        """Force back to idle (e.g., after abort)."""
        self._state = QueryState.IDLE


# -- Child Abort Controller --

class AbortController:
    """Cancellation token with parent→child propagation.

    Like Claude Code's createChildAbortController with WeakRef
    to avoid keeping abandoned children alive.
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._children: list[weakref.ref[AbortController]] = []
        self._callbacks: list[Callable[[], None]] = []

    @property
    def is_aborted(self) -> bool:
        return self._event.is_set()

    def abort(self) -> None:
        """Signal abort. Propagates to all living children."""
        if self._event.is_set():
            return
        self._event.set()

        # Propagate to children
        living: list[weakref.ref[AbortController]] = []
        for ref in self._children:
            child = ref()
            if child is not None:
                child.abort()
                living.append(ref)
        self._children = living  # Prune dead refs

        # Run callbacks
        for cb in self._callbacks:
            try:
                cb()
            except Exception:
                pass

    def on_abort(self, callback: Callable[[], None]) -> None:
        """Register a callback for when abort is signaled."""
        if self._event.is_set():
            callback()
        else:
            self._callbacks.append(callback)

    async def wait(self) -> None:
        """Wait until aborted."""
        await self._event.wait()

    def create_child(self) -> AbortController:
        """Create a child that aborts when this parent aborts.

        Uses WeakRef so abandoned children don't prevent GC.
        """
        child = AbortController()
        self._children.append(weakref.ref(child))

        # If parent already aborted, abort child immediately
        if self._event.is_set():
            child.abort()

        return child

    def check(self) -> None:
        """Raise asyncio.CancelledError if aborted."""
        if self._event.is_set():
            raise asyncio.CancelledError("Operation aborted")


# -- Context Modifier Queue --

class ContextModifierQueue:
    """Queues context modifications from concurrent tool execution.

    Tools running in parallel can't modify context immediately (race condition).
    Instead they return contextModifiers which are queued and applied in order
    after the concurrent batch completes.

    Mirrors Claude Code's queuedContextModifiers pattern in toolOrchestration.ts.
    """

    def __init__(self) -> None:
        self._queue: list[tuple[str, Callable[[ToolContext], ToolContext]]] = []

    def enqueue(self, tool_use_id: str, modifier: Callable[[ToolContext], ToolContext]) -> None:
        """Queue a context modification from a tool."""
        self._queue.append((tool_use_id, modifier))

    def apply_all(self, context: ToolContext) -> ToolContext:
        """Apply all queued modifications in order, then clear."""
        for tool_use_id, modifier in self._queue:
            try:
                context = modifier(context)
            except Exception as e:
                logger.warning("Context modifier from %s failed: %s", tool_use_id, e)
        self._queue.clear()
        return context

    def __len__(self) -> int:
        return len(self._queue)
