"""Shared base class for harness workflows.

Extracts common patterns: agent creation, execution with retry/streaming/cost,
MCP connection, progress logging.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Callable

from ..agent import Agent, AgentResult
from ..config import CalciferConfig
from ..tool import Tool
from ..types.message import StreamEvent
from .artifacts import ProgressLog

logger = logging.getLogger(__name__)


class HarnessBase:
    """Base class with shared agent orchestration logic."""

    def __init__(
        self,
        agent_config: CalciferConfig,
        tools: list[Tool],
        progress_path: str,
        max_retries: int = 2,
        max_cost_usd: float | None = None,
    ):
        self._agent_config = agent_config
        self._tools = tools
        self._max_retries = max_retries
        self._max_cost_usd = max_cost_usd
        self._total_cost = 0.0
        self._progress = ProgressLog(path=progress_path)

    def _make_config(self, **overrides: Any) -> CalciferConfig:
        return dataclasses.replace(self._agent_config, **overrides)

    def _check_cost(self) -> bool:
        if self._max_cost_usd is not None and self._total_cost >= self._max_cost_usd:
            logger.warning("Cost limit reached: $%.2f", self._total_cost)
            return True
        return False

    def _detect_browser_tools(self, tools: list[Tool]) -> str:
        """Detect if browser automation tools are available and return prompt instructions."""
        browser_names = {"browse", "puppeteer", "playwright", "browser"}
        has_browser = any(
            any(b in t.name.lower() for b in browser_names)
            for t in tools
        )
        if has_browser:
            return (
                "\n\nYou have browser automation tools available. Use them to test "
                "the application as a real user would: navigate pages, click buttons, "
                "fill forms, take screenshots, and check the console for errors."
            )
        return ""

    async def _run_agent(
        self,
        prompt: str,
        tools: list[Tool],
        phase: str,
        max_turns: int = 100,
        on_event: Callable[..., None] | None = None,
    ) -> AgentResult:
        """Run an agent with retry, cost tracking, MCP, and optional event streaming."""
        config = self._make_config(max_turns=max_turns)

        for attempt in range(1, self._max_retries + 1):
            try:
                async with Agent(config=config, tools=tools) as agent:
                    if self._agent_config.mcp_servers:
                        await agent.connect_mcp_servers()
                    try:
                        if on_event:
                            result = None
                            async for event in agent.run_stream(prompt):
                                on_event(phase, event)
                                if event.type == "run_complete" and event.result:
                                    result = event.result
                            if result is None:
                                raise RuntimeError("Agent ended without result")
                        else:
                            result = await agent.run(prompt)
                        return result
                    finally:
                        self._total_cost += agent.cost_tracker.get_cost()
            except Exception as e:
                logger.warning("%s attempt %d failed: %s", phase, attempt, e)
                if attempt >= self._max_retries:
                    raise

        raise RuntimeError("Unreachable")
