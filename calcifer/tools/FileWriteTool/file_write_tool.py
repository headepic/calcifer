"""File write tool: create or overwrite files.

Mirrors Claude Code's FileWriteTool:
- Distinguish create vs update
- Track file in read state after write (for subsequent edits)
- Report line count and operation type
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ...tool import Tool
from ...types.tools import ToolContext, ToolResult


class FileWriteInput(BaseModel):
    file_path: str = Field(description="Path to the file to write")
    content: str = Field(description="Content to write to the file")


class FileWriteTool(Tool):
    name = "file_write"
    description = "Write content to a file. Creates the file if it doesn't exist, overwrites if it does."
    parameters = FileWriteInput
    is_concurrency_safe = False
    is_read_only = False
    max_result_size = 10_000

    def get_path(self, args: dict[str, Any]) -> str | None:
        return args.get("file_path")

    def to_auto_classifier_input(self, args: dict[str, Any]) -> str:
        return args.get("file_path", "")

    def get_activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args and args.get("file_path"):
            return f"Writing {Path(args['file_path']).name}"
        return "Writing file"

    async def call(self, args: BaseModel, context: ToolContext, **kwargs) -> ToolResult:
        assert isinstance(args, FileWriteInput)
        path = Path(args.file_path)

        if not path.is_absolute():
            path = Path(context.cwd) / path

        is_new = not path.exists()

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args.content)

            # Track in read state so subsequent edits don't require re-read
            context.read_file_state[str(path)] = path.stat().st_mtime

            line_count = args.content.count("\n") + (1 if args.content else 0)
            op = "Created" if is_new else "Updated"
            return ToolResult(
                content=f"{op} {args.file_path} ({line_count} lines, {len(args.content)} chars)"
            )
        except Exception as e:
            return ToolResult(content=f"Error writing file: {e}", is_error=True)
