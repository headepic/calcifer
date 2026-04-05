"""ToolSearchTool: on-demand tool discovery for the agent.

Mirrors Claude Code's ToolSearchTool:
- Tools with should_defer=True are not included in the initial tools array
- LLM calls ToolSearch to find tools by keyword
- Matched tools are returned as descriptions → available for next turn
- Two-stage search: fast path (exact/prefix) + keyword scoring
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from pydantic import BaseModel, Field

from ...tool import Tool
from ...types.tools import ToolContext, ToolResult

logger = logging.getLogger(__name__)


class ToolSearchInput(BaseModel):
    query: str = Field(description="Search query — tool name, keyword, or capability description")
    max_results: int = Field(default=5, description="Maximum number of results to return")


class ToolSearchTool(Tool):
    """Search for available tools by keyword.

    Not all tools are loaded initially. Use this to discover tools for
    specific tasks. Returns tool names and descriptions.
    """

    name = "tool_search"
    description = (
        "Search for available tools by name or capability keyword. "
        "Returns matching tool names and descriptions. "
        "Use when you need a tool that isn't in your current set."
    )
    parameters = ToolSearchInput
    is_concurrency_safe = True
    is_read_only = True
    always_load = True  # Must always be available

    def __init__(self, all_tools: list[Tool] | None = None):
        self._all_tools: list[Tool] = all_tools or []

    def set_tools(self, tools: list[Tool]) -> None:
        self._all_tools = tools

    def get_deferred_tools(self) -> list[Tool]:
        """Get tools that should be deferred (not in initial prompt)."""
        return [t for t in self._all_tools if _is_deferred(t)]

    def get_inline_tools(self) -> list[Tool]:
        """Get tools that should be in the initial prompt."""
        return [t for t in self._all_tools if not _is_deferred(t)]

    async def call(
        self,
        args: BaseModel,
        context: ToolContext,
        on_progress: Callable | None = None,
    ) -> ToolResult:
        assert isinstance(args, ToolSearchInput)
        query = args.query.strip()
        max_results = min(args.max_results, 20)

        if not query:
            return ToolResult(content="Please provide a search query.", is_error=True)

        deferred = self.get_deferred_tools()
        if not deferred:
            return ToolResult(content="No additional tools available to search.")

        results = _search_tools(query, deferred, max_results)

        if not results:
            return ToolResult(
                content=f"No tools found matching '{query}'. "
                f"Available deferred tools: {', '.join(t.name for t in deferred[:10])}"
            )

        lines = []
        for tool, score in results:
            lines.append(f"**{tool.name}** (score: {score})")
            lines.append(f"  {tool.description}")
            if tool.search_hint:
                lines.append(f"  _Hint: {tool.search_hint}_")
            lines.append("")

        lines.append(
            f"Found {len(results)} tool(s). "
            "These tools are now available for you to use in subsequent turns."
        )

        return ToolResult(content="\n".join(lines))


def _is_deferred(tool: Tool) -> bool:
    """Determine if a tool should be deferred (not in initial prompt).

    Deferral rules (matching Claude Code):
    1. alwaysLoad=True → never defer
    2. is_mcp=True → always defer (unless alwaysLoad)
    3. shouldDefer=True → defer
    4. Otherwise → inline
    """
    if getattr(tool, "always_load", False):
        return False
    if getattr(tool, "is_mcp", False):
        return True
    if getattr(tool, "should_defer", False):
        return True
    return False


def _search_tools(query: str, tools: list[Tool], max_results: int) -> list[tuple[Tool, int]]:
    """Two-stage search: fast path + keyword scoring.

    Returns list of (tool, score) sorted by score descending.
    """
    query_lower = query.lower()

    # Fast path 1: Exact name match
    for t in tools:
        if t.name.lower() == query_lower:
            return [(t, 100)]

    # Fast path 2: MCP prefix match (e.g., "mcp__slack")
    if query_lower.startswith("mcp__"):
        matches = [t for t in tools if t.name.lower().startswith(query_lower)]
        if matches:
            return [(t, 90) for t in matches[:max_results]]

    # Keyword scoring
    query_terms = query_lower.split()
    scored: list[tuple[Tool, int]] = []

    for tool in tools:
        score = _score_tool(tool, query_terms)
        if score > 0:
            scored.append((tool, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:max_results]


def _score_tool(tool: Tool, query_terms: list[str]) -> int:
    """Score a tool against query terms.

    Scoring (matching Claude Code):
    - Exact name part match: 10 (12 for MCP)
    - Partial name part match: 5 (6 for MCP)
    - searchHint match: 4
    - Description match: 2
    """
    score = 0
    is_mcp = getattr(tool, "is_mcp", False)
    name_parts = tool.name.lower().replace("__", " ").replace("_", " ").split()
    hint = (tool.search_hint or "").lower()
    desc = (tool.description or "").lower()

    for term in query_terms:
        # Name matching
        if term in name_parts:
            score += 12 if is_mcp else 10
        elif any(term in part for part in name_parts):
            score += 6 if is_mcp else 5
        # Search hint matching
        elif hint and term in hint:
            score += 4
        # Description matching
        elif term in desc:
            score += 2

    return score
