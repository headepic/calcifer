"""Session Loop: cross-session continuity for long-running tasks.

Pattern from "Effective Harnesses for Long-Running Agents":
- Initializer agent sets up project structure, feature list, init script
- Coding agent picks up where the last session left off
- File-based handoffs (progress log, feature list, git) survive context resets

Each session is a full context reset — not compaction. The agent reads
structured files to reconstruct context, works on one feature, then
commits and updates the progress log before exiting.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from ..agent import Agent, AgentResult
from ..config import CalciferConfig
from ..tool import Tool
from ..types.message import StreamEvent
from .artifacts import FeatureList, ProgressLog

logger = logging.getLogger(__name__)

# Callback type for observing agent events
OnEventFn = Callable[[StreamEvent], None] | None

INITIALIZER_PROMPT = """\
You are the initializer agent. Your job is to set up the project structure
for a long-running development task. You must create these artifacts:

1. **`{init_script}`** — A script that starts the development environment/server.
   Make it idempotent (safe to run multiple times).

2. **`{feature_list}`** — A comprehensive JSON array of features to build.
   Each feature has: category, description, steps (verification steps), passes (false).
   Be thorough — list every feature needed for the complete specification.
   Order by dependency (foundations first, then features that build on them).

3. **`{progress_file}`** — Initialize with project overview and setup notes.

4. **Initial git commit** — Commit all scaffolding files.

The specification to implement:

{specification}

IMPORTANT:
- Be ambitious on scope — list ALL features, not just the obvious ones.
- Each feature's `steps` should describe how a human would verify it works.
- Do NOT start implementing features. Only set up the structure.
- The feature list format must be a JSON array of objects with fields:
  category, description, steps, passes, priority
"""

CODING_PROMPT = """\
You are a coding agent continuing work on a long-running project.

**Session startup sequence (do this first):**
1. Run `pwd` to see your working directory.
2. Read `{progress_file}` to see what was recently worked on.
3. Read `{feature_list}` and find the highest-priority feature that
   hasn't passed yet.
4. Read recent git log: `git log --oneline -20`
5. Run `{init_script}` to start the development environment.
6. Run a basic end-to-end test to check for broken functionality.
7. Fix any existing bugs BEFORE starting new feature work.

**During development:**
- Work on ONE feature at a time.
- Test each feature thoroughly before marking it as passing.
- Only set `passes: true` in {feature_list} after careful testing.
- NEVER remove or edit feature descriptions — only modify the `passes` field.

**Before ending your session:**
- Commit all changes with a descriptive message.
- Update `{progress_file}` with what you did and what to do next.
- Update `{feature_list}` with any features you verified as passing.

{extra_context}
"""


@dataclass
class SessionConfig:
    """Configuration for the session loop."""

    work_dir: str = "."
    feature_list_path: str = "feature_list.json"
    progress_path: str = "claude-progress.txt"
    init_script_path: str = "init.sh"
    max_sessions: int = 50
    max_turns_per_session: int = 100
    max_retries_per_session: int = 2
    max_cost_usd: float | None = None


class SessionLoop:
    """Manages the initializer → coding agent session cycle.

    Usage:
        loop = SessionLoop(config, tools, spec="Build a chat app with...")
        await loop.initialize()
        while not loop.is_complete():
            result = await loop.run_session()
    """

    def __init__(
        self,
        agent_config: CalciferConfig,
        tools: list[Tool],
        spec: str,
        session_config: SessionConfig | None = None,
        on_event: OnEventFn = None,
    ):
        self._agent_config = agent_config
        self._tools = tools
        self._spec = spec
        self._config = session_config or SessionConfig()
        self._on_event = on_event
        self._session_count = 0
        self._total_cost = 0.0
        self._progress = ProgressLog(path=self._config.progress_path)

    def _make_config(self, **overrides: Any) -> CalciferConfig:
        """Copy agent config, overriding specific fields."""
        return dataclasses.replace(self._agent_config, **overrides)

    def is_initialized(self) -> bool:
        from pathlib import Path
        return Path(self._config.feature_list_path).exists()

    def is_complete(self) -> bool:
        if not self.is_initialized():
            return False
        fl = FeatureList.load(self._config.feature_list_path)
        return len(fl.features) > 0 and fl.progress_ratio >= 1.0

    def get_progress(self) -> dict[str, Any]:
        if not self.is_initialized():
            return {"initialized": False, "sessions": self._session_count, "cost_usd": self._total_cost}
        fl = FeatureList.load(self._config.feature_list_path)
        return {
            "initialized": True,
            "sessions": self._session_count,
            "features_total": len(fl.features),
            "features_done": len(fl.done),
            "features_pending": len(fl.pending),
            "progress": f"{fl.progress_ratio:.0%}",
            "next_feature": fl.pending[0].description if fl.pending else None,
            "cost_usd": self._total_cost,
        }

    async def _run_agent(self, prompt: str) -> AgentResult:
        """Run an agent with streaming event forwarding."""
        config = self._make_config(max_turns=self._config.max_turns_per_session)
        async with Agent(config=config, tools=self._tools) as agent:
            if self._agent_config.mcp_servers:
                await agent.connect_mcp_servers()
            try:
                if self._on_event:
                    result = None
                    async for event in agent.run_stream(prompt):
                        self._on_event(event)
                        if event.type == "run_complete" and event.result:
                            result = event.result
                    if result is None:
                        raise RuntimeError("Agent loop ended without result")
                    return result
                else:
                    return await agent.run(prompt)
            finally:
                self._total_cost += agent.cost_tracker.get_cost()

    async def initialize(self) -> AgentResult:
        """Run the initializer agent to set up project structure."""
        prompt = INITIALIZER_PROMPT.format(
            specification=self._spec,
            feature_list=self._config.feature_list_path,
            progress_file=self._config.progress_path,
            init_script=self._config.init_script_path,
        )
        result = await self._run_agent(prompt)
        self._session_count += 1
        self._progress.append("init", "Initializer completed. Project structure created.")
        logger.info("Initializer complete. %s", self.get_progress())
        return result

    async def run_session(self) -> AgentResult:
        """Run one coding session (full context reset) with retry."""
        if not self.is_initialized():
            return await self.initialize()

        self._session_count += 1
        session_id = f"s{self._session_count}-{uuid4().hex[:6]}"

        extra_parts: list[str] = []
        recent_progress = self._progress.read_last(3)
        if recent_progress:
            extra_parts.append(f"Recent progress notes:\n{recent_progress}")

        prompt = CODING_PROMPT.format(
            feature_list=self._config.feature_list_path,
            progress_file=self._config.progress_path,
            init_script=self._config.init_script_path,
            extra_context="\n\n".join(extra_parts) if extra_parts else "",
        )

        last_error: Exception | None = None
        for attempt in range(1, self._config.max_retries_per_session + 1):
            try:
                result = await self._run_agent(prompt)
                return result
            except Exception as e:
                last_error = e
                logger.warning("Session %s attempt %d failed: %s", session_id, attempt, e)
                self._progress.append(
                    session_id,
                    f"Session failed (attempt {attempt}): {e}",
                )
                if attempt < self._config.max_retries_per_session:
                    continue
        raise last_error  # type: ignore[misc]

    async def run_until_complete(self, max_sessions: int | None = None) -> dict[str, Any]:
        """Run sessions until all features pass or limits reached."""
        limit = max_sessions or self._config.max_sessions

        while self._session_count < limit and not self.is_complete():
            # Cost guard
            if self._config.max_cost_usd and self._total_cost >= self._config.max_cost_usd:
                logger.warning("Cost limit reached: $%.2f", self._total_cost)
                break
            try:
                await self.run_session()
            except Exception as e:
                logger.error("Session failed after retries: %s", e)
                # Continue with next session rather than halting entirely
                continue
            logger.info("Session %d/%d done. %s", self._session_count, limit, self.get_progress())

        return {**self.get_progress(), "complete": self.is_complete()}
