"""File read tool: read file contents with offset/limit and encoding detection.

Mirrors Claude Code's FileReadTool:
- Numbered line output (cat -n style)
- Offset/limit for partial reads
- Binary file detection
- File state tracking (for read-before-edit enforcement)
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from ...tool import Tool
from ...types.tools import ToolContext, ToolProgress, ToolResult

MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10MB


class FileReadInput(BaseModel):
    file_path: str = Field(description="Path to the file to read")
    offset: int = Field(default=0, description="Line number to start reading from (0-based)")
    limit: int = Field(default=2000, description="Maximum number of lines to read")


class FileReadTool(Tool):
    name = "file_read"
    description = "Read the contents of a file. Returns numbered lines."
    parameters = FileReadInput
    is_concurrency_safe = True
    is_read_only = True
    is_compactable = True
    max_result_size = 100_000  # Never persist (self-bounded)

    def get_path(self, args: dict[str, Any]) -> str | None:
        return args.get("file_path")

    def get_activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args and args.get("file_path"):
            return f"Reading {Path(args['file_path']).name}"
        return "Reading file"

    def is_search_or_read(self, args: dict[str, Any]) -> dict[str, bool]:
        return {"is_search": False, "is_read": True, "is_list": False}

    async def call(
        self, args: BaseModel, context: ToolContext,
        on_progress: Callable[[ToolProgress], None] | None = None,
    ) -> ToolResult:
        assert isinstance(args, FileReadInput)
        path = Path(args.file_path)
        if not path.is_absolute():
            path = Path(context.cwd) / path

        if not path.exists():
            return ToolResult(content=f"File not found: {args.file_path}", is_error=True)
        if not path.is_file():
            return ToolResult(content=f"Not a file: {args.file_path}", is_error=True)

        # Size check
        file_size = path.stat().st_size
        if file_size > MAX_SIZE_BYTES:
            return ToolResult(
                content=f"File too large ({file_size} bytes, max {MAX_SIZE_BYTES}). Use offset/limit.",
                is_error=True,
            )

        # Binary detection
        mime_type, _ = mimetypes.guess_type(str(path))
        if mime_type and not mime_type.startswith("text/") and mime_type not in (
            "application/json", "application/xml", "application/javascript",
            "application/x-yaml", "application/toml",
        ):
            return ToolResult(content=f"Binary file ({mime_type}): {args.file_path}")

        try:
            text = path.read_text(errors="replace")
        except Exception as e:
            return ToolResult(content=f"Error reading file: {e}", is_error=True)

        # Track read state for edit validation
        context.read_file_state[str(path)] = path.stat().st_mtime

        lines = text.splitlines()
        total = len(lines)
        selected = lines[args.offset : args.offset + args.limit]

        numbered = [f"{i + args.offset + 1}\t{line}" for i, line in enumerate(selected)]
        content = "\n".join(numbered)

        if args.offset + args.limit < total:
            content += f"\n\n... ({total - args.offset - args.limit} more lines)"

        return ToolResult(content=content)
