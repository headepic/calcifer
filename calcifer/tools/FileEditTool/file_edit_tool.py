"""File edit tool: string replacement with atomicity and staleness checks.

Mirrors Claude Code's FileEditTool:
- Exact string match → replace (with uniqueness check)
- Quote normalization (curly quotes ↔ straight quotes fallback)
- Staleness detection (read-before-edit enforcement)
- Atomic write (no async gap between check and write)
- replace_all mode
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from ...tool import Tool
from ...types.tools import ToolContext, ToolProgress, ToolResult, ValidationResult

# Quote normalization map (curly → straight)
QUOTE_NORMALIZE = {
    "\u2018": "'", "\u2019": "'",  # Single curly
    "\u201c": '"', "\u201d": '"',  # Double curly
}


def _normalize_quotes(text: str) -> str:
    for curly, straight in QUOTE_NORMALIZE.items():
        text = text.replace(curly, straight)
    return text


# Whitespace normalization variants for fuzzy matching
def _normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace to single space."""
    import re
    return re.sub(r"[ \t]+", " ", text)


def _normalize_line_endings(text: str) -> str:
    """Normalize all line endings to \n."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _find_fuzzy_match(content: str, old_string: str) -> str | None:
    """Try multiple normalization strategies to find old_string in content.

    Mirrors Claude Code's findActualString — attempts various encoding
    and whitespace normalizations to match the intended string.

    Returns the actual string found in content (for exact replacement),
    or None if no match found.
    """
    strategies = [
        # 1. Line ending normalization
        (_normalize_line_endings, _normalize_line_endings),
        # 2. Quote normalization
        (_normalize_quotes, _normalize_quotes),
        # 3. Whitespace normalization (tabs/spaces)
        (_normalize_whitespace, _normalize_whitespace),
        # 4. Combined: quotes + whitespace
        (lambda t: _normalize_whitespace(_normalize_quotes(t)),
         lambda t: _normalize_whitespace(_normalize_quotes(t))),
        # 5. Combined: line endings + quotes + whitespace
        (lambda t: _normalize_whitespace(_normalize_quotes(_normalize_line_endings(t))),
         lambda t: _normalize_whitespace(_normalize_quotes(_normalize_line_endings(t)))),
    ]

    for normalize_content, normalize_search in strategies:
        nc = normalize_content(content)
        ns = normalize_search(old_string)
        if ns in nc:
            # Found via normalization — extract the actual substring from original content
            idx = nc.find(ns)
            if idx >= 0:
                # Map back to original content by counting chars
                # This works because normalization doesn't change char count for
                # quote normalization, but may change it for whitespace.
                # For safety, do a direct extraction attempt:
                actual = content[idx:idx + len(old_string)]
                if _normalize_whitespace(_normalize_quotes(_normalize_line_endings(actual))) == ns:
                    return actual
                # Fallback: search in original with progressively wider window
                for delta in range(0, 20):
                    candidate = content[idx:idx + len(old_string) + delta]
                    if normalize_content(candidate) == ns:
                        return candidate
                    if idx + len(old_string) + delta >= len(content):
                        break
    return None


class FileEditInput(BaseModel):
    file_path: str = Field(description="Path to the file to edit")
    old_string: str = Field(description="The exact string to find and replace")
    new_string: str = Field(description="The replacement string")
    replace_all: bool = Field(default=False, description="Replace all occurrences")


class FileEditTool(Tool):
    name = "file_edit"
    description = "Edit a file by replacing an exact string match. The old_string must be unique unless replace_all is true."
    parameters = FileEditInput
    is_concurrency_safe = False
    is_read_only = False
    max_result_size = 100_000

    def get_path(self, args: dict[str, Any]) -> str | None:
        return args.get("file_path")

    def to_auto_classifier_input(self, args: dict[str, Any]) -> str:
        path = args.get("file_path", "")
        old = args.get("old_string", "")[:100]
        new = args.get("new_string", "")[:100]
        return f"{path}: {old} → {new}"

    def get_activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args and args.get("file_path"):
            return f"Editing {Path(args['file_path']).name}"
        return "Editing file"

    async def check_input(self, args: dict[str, Any], context: ToolContext) -> ValidationResult:
        file_path = args.get("file_path", "")
        if not file_path:
            return ValidationResult(valid=False, message="file_path is required")

        old_string = args.get("old_string", "")
        new_string = args.get("new_string", "")
        if old_string == new_string:
            return ValidationResult(valid=False, message="old_string and new_string are identical")

        # Read-before-edit check
        path = Path(file_path)
        if not path.is_absolute():
            path = Path(context.cwd) / path

        if path.exists() and str(path) not in context.read_file_state:
            return ValidationResult(
                valid=False,
                message="File has not been read yet. Read it first before editing.",
            )

        return ValidationResult(valid=True)

    async def call(
        self, args: BaseModel, context: ToolContext,
        on_progress: Callable[[ToolProgress], None] | None = None,
    ) -> ToolResult:
        assert isinstance(args, FileEditInput)
        path = Path(args.file_path)
        if not path.is_absolute():
            path = Path(context.cwd) / path

        if not path.exists():
            return ToolResult(content=f"File not found: {args.file_path}", is_error=True)

        # Staleness check — verify file hasn't changed since last read
        cached_mtime = context.read_file_state.get(str(path))
        if cached_mtime is not None:
            actual_mtime = path.stat().st_mtime
            if actual_mtime > cached_mtime:
                return ToolResult(
                    content="File has been modified since last read. Re-read before editing.",
                    is_error=True,
                )

        try:
            content = path.read_text(errors="replace")
        except Exception as e:
            return ToolResult(content=f"Error reading file: {e}", is_error=True)

        # Try exact match first
        count = content.count(args.old_string)
        effective_old = args.old_string

        # Fallback: fuzzy matching with multiple normalization strategies
        if count == 0:
            actual = _find_fuzzy_match(content, args.old_string)
            if actual:
                effective_old = actual
                count = content.count(actual)
            else:
                return ToolResult(content=f"old_string not found in {args.file_path}", is_error=True)

        if count > 1 and not args.replace_all:
            return ToolResult(
                content=f"old_string found {count} times. Use replace_all=true or provide more context.",
                is_error=True,
            )

        # Atomic: no async between check and write
        if args.replace_all:
            new_content = content.replace(effective_old, args.new_string)
            replaced = count
        else:
            new_content = content.replace(effective_old, args.new_string, 1)
            replaced = 1

        try:
            path.write_text(new_content)
            # Update read state with new mtime
            context.read_file_state[str(path)] = path.stat().st_mtime
        except Exception as e:
            return ToolResult(content=f"Error writing file: {e}", is_error=True)

        return ToolResult(content=f"Replaced {replaced} occurrence(s) in {args.file_path}")
