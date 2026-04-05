"""Grep tool: content search using regex.

Mirrors Claude Code's GrepTool:
- ripgrep integration with context lines (-B/-A/-C)
- Multiple output modes (content, files_with_matches, count)
- VCS directory exclusion (.git, .svn, .hg, etc.)
- Case-insensitive and multiline support
- Result pagination via offset
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ...tool import Tool
from ...types.tools import ToolContext, ToolResult

# VCS directories to exclude from search
VCS_DIRS = {".git", ".svn", ".hg", ".bzr", ".jj", ".sl"}


class GrepInput(BaseModel):
    pattern: str = Field(description="Regex pattern to search for")
    path: str = Field(default=".", description="File or directory to search in")
    include: str = Field(default="", description='File glob filter (e.g. "*.py")')
    max_results: int = Field(default=250, description="Maximum number of results")
    before_context: int = Field(default=0, description="Lines of context before match (-B)", alias="-B")
    after_context: int = Field(default=0, description="Lines of context after match (-A)", alias="-A")
    context: int = Field(default=0, description="Lines of context around match (-C)", alias="-C")
    case_insensitive: bool = Field(default=False, description="Case insensitive search", alias="-i")
    line_numbers: bool = Field(default=True, description="Show line numbers", alias="-n")
    multiline: bool = Field(default=False, description="Enable multiline matching")
    output_mode: str = Field(
        default="content",
        description='Output mode: "content" (matching lines), "files_with_matches" (file paths only), "count" (match counts)',
    )
    offset: int = Field(default=0, description="Skip first N results")

    model_config = {"populate_by_name": True}


class GrepTool(Tool):
    name = "grep"
    description = "Search file contents using regex. Uses ripgrep if available, else Python re."
    parameters = GrepInput
    is_concurrency_safe = True
    is_read_only = True
    is_compactable = True
    max_result_size = 100_000

    def is_search_or_read(self, args: dict[str, Any]) -> dict[str, bool]:
        return {"is_search": True, "is_read": False, "is_list": False}

    def get_activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args and args.get("pattern"):
            return f"Searching for '{args['pattern'][:30]}'"
        return "Searching"

    async def call(self, args: BaseModel, context: ToolContext, **kwargs) -> ToolResult:
        assert isinstance(args, GrepInput)
        search_path = Path(args.path)

        if not search_path.is_absolute():
            search_path = Path(context.cwd) / search_path

        # Try ripgrep first
        try:
            return await self._rg_search(args, search_path)
        except FileNotFoundError:
            pass

        # Fallback to Python
        return self._python_search(args, search_path)

    async def _rg_search(self, args: GrepInput, path: Path) -> ToolResult:
        import asyncio

        cmd = ["rg", "--no-heading", "--color=never"]

        # Output mode
        if args.output_mode == "files_with_matches":
            cmd += ["--files-with-matches"]
        elif args.output_mode == "count":
            cmd += ["--count"]
        else:
            # content mode
            if args.line_numbers:
                cmd += ["--line-number"]

        cmd += ["--max-count", str(args.max_results + args.offset)]

        # Context lines
        ctx = args.context
        if ctx > 0:
            cmd += ["-C", str(ctx)]
        else:
            if args.before_context > 0:
                cmd += ["-B", str(args.before_context)]
            if args.after_context > 0:
                cmd += ["-A", str(args.after_context)]

        # Flags
        if args.case_insensitive:
            cmd += ["-i"]
        if args.multiline:
            cmd += ["-U", "--multiline-dotall"]

        # File glob filter
        if args.include:
            cmd += ["--glob", args.include]

        # VCS exclusion
        for vcs in VCS_DIRS:
            cmd += ["--glob", f"!{vcs}"]

        cmd += [args.pattern, str(path)]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode == 1:
            return ToolResult(content="No matches found.")
        if proc.returncode not in (0, 1):
            raise FileNotFoundError("rg not available")

        content = stdout.decode(errors="replace").strip()
        if not content:
            return ToolResult(content="No matches found.")

        # Apply offset
        if args.offset > 0:
            lines = content.split("\n")
            lines = lines[args.offset:]
            content = "\n".join(lines)

        return ToolResult(content=content if content else "No matches found.")

    def _python_search(self, args: GrepInput, path: Path) -> ToolResult:
        flags = 0
        if args.case_insensitive:
            flags |= re.IGNORECASE
        if args.multiline:
            flags |= re.MULTILINE | re.DOTALL

        try:
            regex = re.compile(args.pattern, flags)
        except re.error as e:
            return ToolResult(content=f"Invalid regex: {e}", is_error=True)

        matches: list[str] = []

        if path.is_file():
            files = [path]
        else:
            glob_pattern = args.include or "**/*"
            files = sorted(path.glob(glob_pattern))

        for file in files:
            if not file.is_file():
                continue
            # Skip VCS directories
            if any(part in VCS_DIRS for part in file.parts):
                continue
            try:
                text = file.read_text(errors="replace")
                lines = text.splitlines()
                for i, line in enumerate(lines, 1):
                    if regex.search(line):
                        if args.output_mode == "files_with_matches":
                            matches.append(str(file))
                            break  # one match per file
                        elif args.output_mode == "count":
                            # handled below
                            pass
                        else:
                            prefix = f"{file}:{i}:" if args.line_numbers else f"{file}:"
                            matches.append(f"{prefix}{line}")
                        if len(matches) >= args.max_results + args.offset:
                            break
            except Exception:
                continue
            if len(matches) >= args.max_results + args.offset:
                break

        # Apply offset
        if args.offset > 0:
            matches = matches[args.offset:]
        matches = matches[:args.max_results]

        if not matches:
            return ToolResult(content="No matches found.")

        return ToolResult(content="\n".join(matches))
