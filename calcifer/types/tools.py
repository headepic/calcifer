"""Tool-related types: ToolContext, ToolResult, ToolProgress.

Mirrors Claude Code's Tool.ts type definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolContext:
    """Context passed to tool execution.

    Carries shared state across tool calls within a turn.
    """

    cwd: str = "."
    messages: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Tool orchestration
    max_concurrency: int = 10
    abort_signal: bool = False  # Set to True to cancel in-progress tools

    # File state cache — tracks which files have been read (for read-before-edit checks)
    read_file_state: dict[str, float] = field(default_factory=dict)  # path → mtime

    # Query chain tracking
    chain_id: str | None = None
    chain_depth: int = 0


@dataclass
class ToolResult:
    """Result of a tool execution."""

    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    # Optional: new messages to inject (e.g., attachments discovered during tool execution)
    new_messages: list[Any] = field(default_factory=list)

    # Optional: modify context for subsequent tools (like Claude Code's contextModifier)
    context_modifier: Callable[[ToolContext], ToolContext] | None = None


# -- Tool progress --

@dataclass
class ToolProgress:
    """Progress update from a running tool."""

    tool_use_id: str
    type: str  # "bash_output", "download_progress", etc.
    data: dict[str, Any] = field(default_factory=dict)

    # Bash-specific
    stdout: str | None = None
    stderr: str | None = None
    elapsed_ms: int | None = None

    # Generic
    message: str | None = None
    percentage: float | None = None


# -- Validation --

@dataclass
class ValidationResult:
    """Result of input validation."""

    valid: bool
    message: str = ""
    error_code: int = 0
