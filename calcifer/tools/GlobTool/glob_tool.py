"""Glob tool: file pattern matching."""

from __future__ import annotations

import glob as globlib
from pathlib import Path

from pydantic import BaseModel, Field

from ...tool import Tool
from ...types.tools import ToolContext, ToolResult


class GlobInput(BaseModel):
    pattern: str = Field(description='Glob pattern to match (e.g. "**/*.py")')
    path: str = Field(default=".", description="Directory to search in")


class GlobTool(Tool):
    name = "glob"
    description = "Find files matching a glob pattern. Returns matching file paths."
    parameters = GlobInput
    is_concurrency_safe = True
    is_read_only = True
    is_compactable = True
    max_result_size = 100_000

    async def call(self, args: BaseModel, context: ToolContext, **kwargs) -> ToolResult:
        assert isinstance(args, GlobInput)
        search_dir = Path(args.path)

        if not search_dir.is_absolute():
            search_dir = Path(context.cwd) / search_dir

        try:
            matches = sorted(
                globlib.glob(
                    str(search_dir / args.pattern), recursive=True
                )
            )
        except Exception as e:
            return ToolResult(content=f"Glob error: {e}", is_error=True)

        if not matches:
            return ToolResult(content="No files found matching the pattern.")

        # Limit results
        max_results = 250
        truncated = len(matches) > max_results
        matches = matches[:max_results]

        content = "\n".join(matches)
        if truncated:
            content += f"\n\n... (truncated, showing {max_results} of {len(matches)} results)"

        return ToolResult(content=content)
