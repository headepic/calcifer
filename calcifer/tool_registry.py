"""Tool registry: single source of truth for all built-in tools.

Mirrors Claude Code's tools.ts:
- assembleToolPool: combine built-in + MCP tools
- getTools: get enabled tools
"""

from __future__ import annotations

from .tool import Tool
from .tools.BashTool import BashTool
from .tools.FileEditTool import FileEditTool
from .tools.FileReadTool import FileReadTool
from .tools.FileWriteTool import FileWriteTool
from .tools.GlobTool import GlobTool
from .tools.GrepTool import GrepTool
from .tools.SkillTool import SkillTool
from .tools.ToolSearchTool import ToolSearchTool
from .tools.WebSearchTool import WebSearchTool


def get_all_builtin_tools() -> list[Tool]:
    """Get all built-in tool instances (including SkillTool and ToolSearchTool)."""
    return [
        BashTool(),
        FileReadTool(),
        FileWriteTool(),
        FileEditTool(),
        GlobTool(),
        GrepTool(),
        SkillTool(),
        ToolSearchTool(),
        WebSearchTool(),
    ]


def get_tools() -> list[Tool]:
    """Get enabled built-in tools."""
    tools = get_all_builtin_tools()
    return [t for t in tools if t.is_enabled()]


def assemble_tool_pool(
    builtin_tools: list[Tool],
    mcp_tools: list[Tool],
) -> list[Tool]:
    """Assemble the full tool pool: built-in + MCP, deduped.

    Like Claude Code's assembleToolPool:
    1. Sort each partition for prompt cache stability
    2. Dedup by name (built-in wins on conflict)
    """
    by_name = lambda t: t.name
    sorted_builtin = sorted(builtin_tools, key=by_name)
    sorted_mcp = sorted(mcp_tools, key=by_name)

    # Dedup: built-in wins
    seen: set[str] = set()
    result: list[Tool] = []
    for t in sorted_builtin + sorted_mcp:
        if t.name not in seen:
            result.append(t)
            seen.add(t.name)

    return result
