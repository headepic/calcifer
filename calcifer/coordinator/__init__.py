"""Multi-agent coordinator: orchestrate worker agents.

Mirrors Claude Code's coordinator/:
- Coordinator mode (orchestrator + workers)
- Worker tool restriction
- Shared scratchpad directory
- Inter-agent messaging (SendMessage)
"""

from .coordinator import Coordinator, WorkerAgent, CoordinatorConfig

__all__ = ["Coordinator", "CoordinatorConfig", "WorkerAgent"]
