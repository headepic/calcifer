"""Query profiling: timing checkpoints for performance diagnostics.

Mirrors Claude Code's queryProfiler.ts — marks timing points
throughout the agent loop for diagnostics.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Checkpoint:
    name: str
    timestamp: float
    elapsed_ms: float  # Since profiler start


class QueryProfiler:
    """Records timing checkpoints during a query loop iteration."""

    def __init__(self) -> None:
        self._start = time.monotonic()
        self._checkpoints: list[Checkpoint] = []

    def checkpoint(self, name: str) -> None:
        now = time.monotonic()
        elapsed = (now - self._start) * 1000
        self._checkpoints.append(Checkpoint(
            name=name, timestamp=now, elapsed_ms=elapsed,
        ))
        logger.debug("Query checkpoint: %s @ %.1fms", name, elapsed)

    def reset(self) -> None:
        self._start = time.monotonic()
        self._checkpoints.clear()

    def summary(self) -> dict[str, float]:
        """Return checkpoint name → elapsed_ms from start."""
        return {cp.name: cp.elapsed_ms for cp in self._checkpoints}

    def total_ms(self) -> float:
        return (time.monotonic() - self._start) * 1000
