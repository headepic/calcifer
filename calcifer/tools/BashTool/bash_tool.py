"""Bash tool: execute shell commands with security, background support, and progress.

Mirrors Claude Code's BashTool/:
- Command parsing for security classification
- Read-only command detection (whitelisted commands)
- Background task support (run_in_background)
- Output truncation with disk persistence for large outputs
- Progress reporting for long-running commands
- Timeout handling
- Exit code tracking
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from ...tool import Tool
from ...types.tools import (
    ToolContext,
    ToolProgress,
    ToolResult,
    ValidationResult,
)

# Read-only commands safe for auto-approval
READ_ONLY_COMMANDS = {
    "cat", "head", "tail", "less", "more", "wc", "stat", "file",
    "ls", "tree", "du", "df", "find", "which", "whereis", "type",
    "grep", "rg", "ag", "ack", "sed", "awk", "cut", "sort", "uniq", "tr",
    "echo", "printf", "true", "false", "test",
    "pwd", "whoami", "hostname", "date", "env", "printenv",
    "jq", "yq", "xargs",
}

READ_ONLY_GIT = {
    "git status", "git log", "git diff", "git show", "git branch",
    "git remote", "git tag", "git stash list",
}

# Dangerous patterns
DANGEROUS_PATTERNS = [
    r"\brm\s+(-[rf]+\s+)*/",
    r"\bsudo\b",
    r"\bchmod\s+777\b",
    r"\b>\s*/dev/sd",
    r"\bmkfs\b",
    r"\bdd\s+.*of=/dev/",
]

# Output limits
MAX_OUTPUT_BYTES = 20 * 1024 * 1024
DISK_PERSIST_THRESHOLD = 30_000
PREVIEW_SIZE = 2_000
PROGRESS_THRESHOLD_MS = 2000


class BashInput(BaseModel):
    command: str = Field(description="The shell command to execute")
    timeout: int = Field(default=120, description="Timeout in seconds (max 600)")
    run_in_background: bool = Field(default=False, description="Run command in background")
    description: str = Field(default="", description="Description of what this command does")


class BashTool(Tool):
    name = "bash"
    description = "Execute a shell command and return its output."
    parameters = BashInput
    is_concurrency_safe = False
    is_read_only = False
    is_compactable = True  # Bash output is ephemeral, safe to clear after use
    max_result_size = 30_000

    def __init__(self, sandbox_config: Any = None):
        from ...utils.sandbox import SandboxManager
        self._sandbox = SandboxManager(sandbox_config)

    def _parse_first_command(self, command: str) -> str:
        cmd = command.strip()
        for sep in ["|", "&&", "||", ";"]:
            cmd = cmd.split(sep)[0].strip()
        parts = cmd.split()
        return parts[0] if parts else ""

    def _is_read_only(self, command: str) -> bool:
        first = self._parse_first_command(command)
        if first in READ_ONLY_COMMANDS:
            return True
        parts = command.strip().split()
        if len(parts) >= 2 and f"{parts[0]} {parts[1]}" in READ_ONLY_GIT:
            return True
        return False

    def _check_dangerous(self, command: str) -> str | None:
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, command):
                return f"Dangerous command pattern: {pattern}"
        return None

    async def check_input(self, args: dict[str, Any], context: ToolContext) -> ValidationResult:
        command = args.get("command", "")
        if not command.strip():
            return ValidationResult(valid=False, message="Empty command")
        danger = self._check_dangerous(command)
        if danger:
            return ValidationResult(valid=False, message=danger)
        return ValidationResult(valid=True)

    def to_auto_classifier_input(self, args: dict[str, Any]) -> str:
        return args.get("command", "")

    def get_activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args and args.get("command"):
            cmd = args["command"]
            return f"Running `{cmd[:60]}{'...' if len(cmd) > 60 else ''}`"
        return "Running command"

    def is_search_or_read(self, args: dict[str, Any]) -> dict[str, bool]:
        first = self._parse_first_command(args.get("command", ""))
        return {
            "is_search": first in {"find", "grep", "rg", "ag", "ack", "locate", "which", "whereis"},
            "is_read": first in {"cat", "head", "tail", "less", "more", "wc", "stat", "file", "jq", "awk"},
            "is_list": first in {"ls", "tree", "du"},
        }

    async def call(
        self, args: BaseModel, context: ToolContext,
        on_progress: Callable[[ToolProgress], None] | None = None,
    ) -> ToolResult:
        assert isinstance(args, BashInput)
        timeout = min(args.timeout, 600)

        if args.run_in_background:
            return await self._run_background(args.command, context)

        # Apply sandbox wrapping if configured
        command = self._sandbox.wrap_command(args.command, context.cwd)

        start_time = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=context.cwd,
                env={**os.environ, "TERM": "dumb"},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            return ToolResult(content=f"Command timed out after {timeout}s", is_error=True)
        except Exception as e:
            return ToolResult(content=f"Error running command: {e}", is_error=True)

        # Report elapsed time for slow commands
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        if on_progress and elapsed_ms > PROGRESS_THRESHOLD_MS:
            on_progress(ToolProgress(
                tool_use_id="", type="bash_complete", elapsed_ms=elapsed_ms,
            ))

        # Hard output limit
        stdout_str = stdout.decode(errors="replace")[:MAX_OUTPUT_BYTES]
        stderr_str = stderr.decode(errors="replace")[:MAX_OUTPUT_BYTES]

        return self._format_output(stdout_str, stderr_str, proc.returncode or 0)

    def _format_output(self, stdout: str, stderr: str, returncode: int) -> ToolResult:
        parts: list[str] = []
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append(f"STDERR:\n{stderr}")
        if returncode != 0:
            parts.append(f"Exit code: {returncode}")

        content = "\n".join(parts) if parts else "(no output)"

        if len(content) > DISK_PERSIST_THRESHOLD:
            return self._persist_large_output(content)

        return ToolResult(content=self.truncate_result(content))

    def _persist_large_output(self, content: str) -> ToolResult:
        output_dir = Path(tempfile.gettempdir()) / "calcifer-tool-results"
        output_dir.mkdir(parents=True, exist_ok=True)
        import uuid
        path = output_dir / f"bash_{uuid.uuid4().hex[:8]}.txt"
        path.write_text(content)
        preview = content[:PREVIEW_SIZE]
        return ToolResult(
            content=f"{preview}\n\n... [{len(content)} total chars, saved to {path}]\nUse file_read for full output.",
            metadata={"persisted_path": str(path), "total_chars": len(content)},
        )

    async def _run_background(self, command: str, context: ToolContext) -> ToolResult:
        try:
            proc = await asyncio.create_subprocess_shell(
                command, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, cwd=context.cwd,
            )
            return ToolResult(
                content=f"Command started in background (PID: {proc.pid})",
                metadata={"pid": proc.pid, "background": True},
            )
        except Exception as e:
            return ToolResult(content=f"Failed to start background command: {e}", is_error=True)
