"""Multi-agent coordinator: orchestrate worker agents.

Mirrors Claude Code's coordinator/coordinatorMode.ts:
- Coordinator acts as orchestrator, delegates work to workers via AgentTool
- Workers get restricted tool set (bash, read, edit, MCP — no spawning sub-agents)
- Shared scratchpad directory for cross-worker file sharing
- Inter-agent messaging via SendMessage pattern
- Task notification format for progress visibility
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import CalciferConfig
from ..tool import Tool
from ..types.message import Message, Usage
from ..types.tools import ToolContext

logger = logging.getLogger(__name__)

# Default tools workers are allowed to use
WORKER_ALLOWED_TOOLS = {
    "bash", "file_read", "file_write", "file_edit", "glob", "grep",
}


@dataclass
class CoordinatorConfig:
    """Configuration for the coordinator."""

    max_workers: int = 5
    worker_max_turns: int = 30
    scratchpad_dir: str | None = None  # Shared directory; auto-created if None
    worker_allowed_tools: set[str] = field(default_factory=lambda: set(WORKER_ALLOWED_TOOLS))


@dataclass
class WorkerResult:
    """Result from a worker agent."""

    worker_id: str
    name: str
    status: str  # "completed", "failed", "killed"
    result_text: str
    usage: Usage
    turn_count: int


class WorkerAgent:
    """A worker agent spawned by the coordinator.

    Workers have a restricted tool set and run in isolation.
    Each worker gets its own file state cache (read-before-edit tracking)
    to prevent cross-contamination with other workers.
    """

    def __init__(
        self,
        worker_id: str,
        name: str,
        config: CalciferConfig,
        tools: list[Tool],
        scratchpad_dir: str,
    ):
        self.worker_id = worker_id
        self.name = name
        self._config = config
        self._tools = tools
        self._scratchpad = scratchpad_dir
        self._agent: Any | None = None  # Lazy init for abort access

    def abort(self) -> None:
        """Signal this worker to stop."""
        if self._agent:
            self._agent.abort()

    async def run(self, prompt: str) -> WorkerResult:
        """Run the worker on a task."""
        from ..agent import Agent

        system_prompt = (
            f"{self._config.system_prompt}\n\n"
            f"You are worker '{self.name}'. "
            f"Shared scratchpad directory: {self._scratchpad}\n"
            f"Write intermediate results to the scratchpad for other workers to read."
        )

        worker_config = CalciferConfig(
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            model=self._config.model,
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
            max_turns=30,
            system_prompt=system_prompt,
        )

        try:
            async with Agent(config=worker_config, tools=self._tools) as agent:
                self._agent = agent
                result = await agent.run(prompt)
                return WorkerResult(
                    worker_id=self.worker_id,
                    name=self.name,
                    status="completed",
                    result_text=result.final_text,
                    usage=result.usage,
                    turn_count=result.turn_count,
                )
        except Exception as e:
            return WorkerResult(
                worker_id=self.worker_id,
                name=self.name,
                status="failed",
                result_text=f"Worker failed: {e}",
                usage=Usage(),
                turn_count=0,
            )
        finally:
            self._agent = None


class Coordinator:
    """Orchestrates multiple worker agents.

    Usage:
        coord = Coordinator(config, tools, coord_config)
        results = await coord.run_workers([
            ("research", "Find all API endpoints"),
            ("implement", "Add the new endpoint"),
        ])
    """

    def __init__(
        self,
        config: CalciferConfig,
        all_tools: list[Tool],
        coord_config: CoordinatorConfig | None = None,
    ):
        self._config = config
        self._all_tools = all_tools
        self._coord_config = coord_config or CoordinatorConfig()

        # Set up scratchpad
        if self._coord_config.scratchpad_dir:
            self._scratchpad = self._coord_config.scratchpad_dir
        else:
            self._scratchpad = str(
                Path(tempfile.mkdtemp(prefix="calcifer-scratchpad-"))
            )
        Path(self._scratchpad).mkdir(parents=True, exist_ok=True)

        # Filter tools for workers
        self._worker_tools = [
            t for t in all_tools
            if t.name in self._coord_config.worker_allowed_tools or t.is_mcp
        ]

        self._workers: dict[str, WorkerAgent] = {}
        self._worker_counter = 0
        self._abort_event = asyncio.Event()

    def _create_worker(self, name: str) -> WorkerAgent:
        """Create a new worker agent."""
        self._worker_counter += 1
        worker_id = f"w_{self._worker_counter}"
        worker = WorkerAgent(
            worker_id=worker_id,
            name=name,
            config=self._config,
            tools=self._worker_tools,
            scratchpad_dir=self._scratchpad,
        )
        self._workers[worker_id] = worker
        return worker

    async def run_worker(self, name: str, prompt: str) -> WorkerResult:
        """Run a single worker on a task."""
        worker = self._create_worker(name)
        result = await worker.run(prompt)
        logger.info(
            "Worker '%s' %s in %d turns",
            name, result.status, result.turn_count,
        )
        return result

    async def run_workers(
        self,
        tasks: list[tuple[str, str]],  # [(name, prompt), ...]
        parallel: bool = True,
    ) -> list[WorkerResult]:
        """Run multiple workers, optionally in parallel.

        Args:
            tasks: List of (worker_name, prompt) tuples.
            parallel: If True, run all workers concurrently.
        """
        if parallel:
            coros = [self.run_worker(name, prompt) for name, prompt in tasks]
            # Respect max_workers
            sem = asyncio.Semaphore(self._coord_config.max_workers)

            async def _limited(coro: Any) -> WorkerResult:
                async with sem:
                    return await coro

            results = await asyncio.gather(*[_limited(c) for c in coros])
            return list(results)
        else:
            results: list[WorkerResult] = []
            for name, prompt in tasks:
                result = await self.run_worker(name, prompt)
                results.append(result)
            return results

    def format_results_for_coordinator(
        self, results: list[WorkerResult]
    ) -> str:
        """Format worker results as task-notification XML for the coordinator."""
        parts: list[str] = []
        for r in results:
            parts.append(
                f"<task-notification>\n"
                f"  <task-id>{r.worker_id}</task-id>\n"
                f"  <worker-name>{r.name}</worker-name>\n"
                f"  <status>{r.status}</status>\n"
                f"  <result>{r.result_text[:2000]}</result>\n"
                f"  <turns>{r.turn_count}</turns>\n"
                f"</task-notification>"
            )
        return "\n\n".join(parts)

    def abort(self) -> None:
        """Abort all running workers."""
        self._abort_event.set()
        for worker in self._workers.values():
            worker.abort()

    @property
    def scratchpad_dir(self) -> str:
        return self._scratchpad
