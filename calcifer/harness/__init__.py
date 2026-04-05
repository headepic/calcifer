"""Harness workflows for long-running agent tasks.

Two patterns from Anthropic's engineering research:

1. **Session Loop** (initializer → coding agent cycle):
   Cross-session continuity via structured files (progress log, feature list,
   init script, git history). Each session picks up where the last left off.

2. **Pipeline** (planner → generator → evaluator):
   Quality through separation. Planner expands scope, generator builds,
   evaluator tests and grades against criteria.

Both use context resets with file-based handoffs (not compaction) for
multi-hour tasks spanning multiple context windows.
"""

from .session_loop import SessionLoop, SessionConfig
from .pipeline import Pipeline, PipelineConfig, PipelineResult

__all__ = [
    "SessionLoop",
    "SessionConfig",
    "Pipeline",
    "PipelineConfig",
    "PipelineResult",
]
