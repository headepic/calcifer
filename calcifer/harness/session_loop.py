"""Session Loop: cross-session continuity for long-running tasks.

Pattern from "Effective Harnesses for Long-Running Agents":
- Initializer agent sets up project structure, feature list, init script
- Coding agent picks up where the last session left off
- File-based handoffs (progress log, feature list, git) survive context resets
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ..agent import AgentResult
from ..config import CalciferConfig
from ..tool import Tool
from ..types.message import StreamEvent
from .artifacts import FeatureList, ProgressLog
from .base import HarnessBase

logger = logging.getLogger(__name__)

OnEventFn = Callable[[str, StreamEvent], None] | None

INITIALIZER_PROMPT = """\
You are the initializer agent. Your job is to set up the project structure
for a long-running development task. You must create these artifacts:

1. **`{init_script}`** — A script that starts the development environment/server.
   Make it idempotent (safe to run multiple times). Set execute permission (chmod +x).

2. **`{feature_list}`** — A comprehensive JSON array of features to build.
   Each feature has: category, description, steps (verification steps), passes (false).
   Be thorough — a complete feature list typically has 50-200+ items.
   If your list has fewer than 30 features, you are probably missing categories.
   Order by dependency (foundations first, then features that build on them).

3. **`{progress_file}`** — Initialize with project overview and setup notes.

4. **Initial git commit** — Commit all scaffolding files.

5. **Verify** — Run `{init_script}` to confirm it works.

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

**Session startup sequence (do this first, in order):**
1. Run `pwd` to see your working directory.
2. Read the LAST 3 entries of `{progress_file}` (not the full file — it may be long).
3. Read `{feature_list}` and find the highest-priority feature that hasn't passed yet.
4. Read recent git log: `git log --oneline -20`
5. Run `bash {init_script}` to start the development environment.
6. Run a basic end-to-end test to check for broken functionality.
7. Fix any existing bugs BEFORE starting new feature work.

**During development:**
- Work on ONE feature at a time.
- Test each feature thoroughly before marking it as passing.
- Only set `passes: true` in `{feature_list}` after careful testing.
- NEVER remove or edit feature descriptions — only modify the `passes` field.
- NEVER delete, weaken, or modify existing tests to make them pass. Fix the code instead.

**Testing:**
- Test as a real user would — interact with the running application.{browser_instructions}
- Check the console/logs for errors after each action.
- Test edge cases, not just the happy path.

**When you finish ONE feature:**
- Commit your changes with a descriptive message.
- Update `{progress_file}` with what you did and what to do next.
- Update `{feature_list}` with the feature you verified as passing.
- STOP. Do not begin another feature. The next session will pick up.

**Context anxiety:** Do not rush to finish because you feel you are running low
on context. If you run out of context mid-feature, the next session will pick up
where you left off. Focus on quality over speed. Commit partial progress rather
than shipping broken code.
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


class SessionLoop(HarnessBase):
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
        self._spec = spec
        self._config = session_config or SessionConfig()
        self._on_event = on_event
        self._session_count = 0
        super().__init__(
            agent_config=agent_config,
            tools=tools,
            progress_path=self._config.progress_path,
            max_retries=self._config.max_retries_per_session,
            max_cost_usd=self._config.max_cost_usd,
        )

    def is_initialized(self) -> bool:
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

    async def initialize(self) -> AgentResult:
        prompt = INITIALIZER_PROMPT.format(
            specification=self._spec,
            feature_list=self._config.feature_list_path,
            progress_file=self._config.progress_path,
            init_script=self._config.init_script_path,
        )
        result = await self._run_agent(
            prompt, self._tools, "initializer",
            max_turns=self._config.max_turns_per_session,
            on_event=self._on_event,
        )
        self._session_count += 1
        self._progress.append("init", "Initializer completed. Project structure created.")
        return result

    async def run_session(self) -> AgentResult:
        if not self.is_initialized():
            return await self.initialize()

        self._session_count += 1
        session_id = f"s{self._session_count}-{uuid4().hex[:6]}"

        # Snapshot feature list before session (for corruption recovery)
        FeatureList.snapshot(self._config.feature_list_path)
        pre_count = len(FeatureList.load(self._config.feature_list_path).features)

        browser_instructions = self._detect_browser_tools(self._tools)

        prompt = CODING_PROMPT.format(
            feature_list=self._config.feature_list_path,
            progress_file=self._config.progress_path,
            init_script=self._config.init_script_path,
            browser_instructions=browser_instructions,
        )

        # _run_agent already has retry logic via HarnessBase, no outer retry needed
        result = await self._run_agent(
            prompt, self._tools, f"coding-{session_id}",
            max_turns=self._config.max_turns_per_session,
            on_event=self._on_event,
        )
        # Post-session: validate feature list integrity
        post_fl = FeatureList.load(self._config.feature_list_path)
        if len(post_fl.features) > 0 and len(post_fl.features) < pre_count * 0.5:
            logger.warning(
                "Feature list shrank from %d to %d — possible corruption",
                pre_count, len(post_fl.features),
            )
        return result

    async def run_until_complete(self, max_sessions: int | None = None) -> dict[str, Any]:
        limit = max_sessions or self._config.max_sessions
        while self._session_count < limit and not self.is_complete():
            if self._check_cost():
                break
            try:
                await self.run_session()
            except Exception as e:
                logger.error("Session failed after retries: %s", e)
                continue
            logger.info("Session %d/%d done. %s", self._session_count, limit, self.get_progress())
        return {**self.get_progress(), "complete": self.is_complete()}
