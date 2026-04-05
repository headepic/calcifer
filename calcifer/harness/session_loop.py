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

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..agent import Agent, AgentResult
from ..config import CalciferConfig
from ..tool import Tool
from .artifacts import FeatureList, ProgressLog

logger = logging.getLogger(__name__)

# Initializer agent prompt: sets up the project scaffolding
INITIALIZER_PROMPT = """\
You are the initializer agent. Your job is to set up the project structure
for a long-running development task. You must create these artifacts:

1. **`init.sh`** — A script that starts the development environment/server.
   Make it idempotent (safe to run multiple times).

2. **`feature_list.json`** — A comprehensive JSON array of features to build.
   Each feature has: category, description, steps (verification steps), passes (false).
   Be thorough — list every feature needed for the complete specification.
   Order by dependency (foundations first, then features that build on them).

3. **`claude-progress.txt`** — Initialize with project overview and setup notes.

4. **Initial git commit** — Commit all scaffolding files.

The specification to implement:

{specification}

IMPORTANT:
- Be ambitious on scope — list ALL features, not just the obvious ones.
- Each feature's `steps` should describe how a human would verify it works.
- Do NOT start implementing features. Only set up the structure.
- The feature_list.json format must be a JSON array of objects with fields:
  category, description, steps, passes, priority
"""

# Coding agent prompt: picks up where last session left off
CODING_PROMPT = """\
You are a coding agent continuing work on a long-running project.

**Session startup sequence (do this first):**
1. Run `pwd` to see your working directory.
2. Read `claude-progress.txt` to see what was recently worked on.
3. Read `feature_list.json` and find the highest-priority feature that
   hasn't passed yet.
4. Read recent git log: `git log --oneline -20`
5. Run `init.sh` to start the development environment.
6. Run a basic end-to-end test to check for broken functionality.
7. Fix any existing bugs BEFORE starting new feature work.

**During development:**
- Work on ONE feature at a time.
- Test each feature thoroughly before marking it as passing.
- Use browser automation tools if available to verify UI features.
- Only set `passes: true` in feature_list.json after careful testing.
- NEVER remove or edit feature descriptions — only modify the `passes` field.

**Before ending your session:**
- Commit all changes with a descriptive message.
- Update `claude-progress.txt` with what you did and what to do next.
- Update `feature_list.json` with any features you verified as passing.

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


class SessionLoop:
    """Manages the initializer → coding agent session cycle.

    Usage:
        loop = SessionLoop(config, tools, spec="Build a chat app with...")
        # Run the initializer (first time only)
        await loop.initialize()
        # Run coding sessions until all features pass
        while not loop.is_complete():
            result = await loop.run_session()
    """

    def __init__(
        self,
        agent_config: CalciferConfig,
        tools: list[Tool],
        spec: str,
        session_config: SessionConfig | None = None,
    ):
        self._agent_config = agent_config
        self._tools = tools
        self._spec = spec
        self._config = session_config or SessionConfig()
        self._session_count = 0
        self._progress = ProgressLog(path=self._config.progress_path)

    def is_initialized(self) -> bool:
        """Check if the initializer has run."""
        return Path(self._config.feature_list_path).exists()

    def is_complete(self) -> bool:
        """Check if all features are passing."""
        if not self.is_initialized():
            return False
        fl = FeatureList.load(self._config.feature_list_path)
        return fl.progress_ratio >= 1.0

    def get_progress(self) -> dict[str, Any]:
        """Get current progress summary."""
        if not self.is_initialized():
            return {"initialized": False, "sessions": self._session_count}
        fl = FeatureList.load(self._config.feature_list_path)
        return {
            "initialized": True,
            "sessions": self._session_count,
            "features_total": len(fl.features),
            "features_done": len(fl.done),
            "features_pending": len(fl.pending),
            "progress": f"{fl.progress_ratio:.0%}",
            "next_feature": fl.pending[0].description if fl.pending else None,
        }

    async def initialize(self) -> AgentResult:
        """Run the initializer agent to set up project structure."""
        prompt = INITIALIZER_PROMPT.format(specification=self._spec)

        async with Agent(
            config=self._agent_config,
            tools=self._tools,
        ) as agent:
            result = await agent.run(prompt)

        self._session_count += 1
        self._progress.append(
            session_id=f"init-{self._session_count}",
            content="Initializer agent completed. Project structure created.",
        )
        logger.info("Initializer complete. Features: %s", self.get_progress())
        return result

    async def run_session(self) -> AgentResult:
        """Run one coding session (full context reset)."""
        if not self.is_initialized():
            return await self.initialize()

        self._session_count += 1
        session_id = f"s{self._session_count}-{uuid4().hex[:6]}"

        # Build context from files (not from prior agent memory)
        extra_parts: list[str] = []
        recent_progress = self._progress.read_last(3)
        if recent_progress:
            extra_parts.append(f"Recent progress notes:\n{recent_progress}")

        prompt = CODING_PROMPT.format(
            extra_context="\n\n".join(extra_parts) if extra_parts else "",
        )

        # Fresh agent — full context reset
        async with Agent(
            config=CalciferConfig(
                api_key=self._agent_config.api_key,
                base_url=self._agent_config.base_url,
                model=self._agent_config.model,
                max_tokens=self._agent_config.max_tokens,
                temperature=self._agent_config.temperature,
                max_turns=self._config.max_turns_per_session,
                system_prompt=self._agent_config.system_prompt,
            ),
            tools=self._tools,
        ) as agent:
            result = await agent.run(prompt)

        logger.info(
            "Session %s complete. Progress: %s",
            session_id, self.get_progress(),
        )
        return result

    async def run_until_complete(self, max_sessions: int | None = None) -> dict[str, Any]:
        """Run sessions until all features pass or max_sessions reached."""
        limit = max_sessions or self._config.max_sessions
        results: list[AgentResult] = []

        while self._session_count < limit and not self.is_complete():
            result = await self.run_session()
            results.append(result)
            logger.info("Session %d/%d done. %s", self._session_count, limit, self.get_progress())

        return {
            **self.get_progress(),
            "sessions_run": len(results),
            "complete": self.is_complete(),
        }
