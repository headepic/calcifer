"""Message and event rendering for the TUI.

Handles:
- Streaming text delta rendering (incremental append)
- Tool call display (name, args summary, spinner, result)
- Markdown rendering in terminal
- Status bar (model, tokens, cost, turns)
- Permission prompts
"""

from __future__ import annotations

import itertools
import time
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from ..types.message import Message, StreamEvent, Usage
from .theme import (
    ASSISTANT_GLYPH, ASSISTANT_STYLE, COST_STYLE, DIM_STYLE,
    ERROR_GLYPH, ERROR_STYLE, RESULT_GLYPH, SPINNER_FRAMES,
    SPINNER_VERBS, SUCCESS_GLYPH, SYSTEM_GLYPH, SYSTEM_STYLE,
    TOOL_GLYPH, TOOL_RESULT_STYLE, TOOL_STYLE, USER_GLYPH, USER_STYLE,
)


def render_user_message(content: str) -> Text:
    """Render a user message."""
    text = Text()
    text.append(f" {USER_GLYPH} ", style=USER_STYLE)
    text.append(content)
    return text


def render_assistant_text(content: str, *, streaming: bool = False) -> Text | Markdown:
    """Render assistant text, with markdown if not streaming."""
    if streaming or len(content) < 20:
        text = Text()
        text.append(f" {ASSISTANT_GLYPH} ", style=ASSISTANT_STYLE)
        text.append(content)
        return text
    # Full markdown rendering for complete messages
    return Markdown(content)


def render_tool_call_start(tool_name: str, arguments: str) -> Text:
    """Render a tool call starting."""
    summary = _summarize_tool_args(tool_name, arguments)
    text = Text()
    text.append(f" {TOOL_GLYPH} ", style=TOOL_STYLE)
    text.append(tool_name, style=TOOL_STYLE)
    if summary:
        text.append(f" {summary}", style=DIM_STYLE)
    return text


def render_tool_result(content: str, is_error: bool = False) -> Text:
    """Render a tool result (truncated)."""
    text = Text()
    if is_error:
        text.append(f" {ERROR_GLYPH} ", style=ERROR_STYLE)
    else:
        text.append(f" {SUCCESS_GLYPH} ", style="green")
    # Truncate long results
    lines = content.split("\n")
    if len(lines) > 8:
        preview = "\n".join(lines[:6])
        text.append(preview, style=TOOL_RESULT_STYLE)
        text.append(f"\n   ... ({len(lines) - 6} more lines)", style=DIM_STYLE)
    else:
        text.append(content, style=TOOL_RESULT_STYLE)
    return text


def render_system_message(content: str) -> Text:
    """Render a system/info message."""
    text = Text()
    text.append(f" {SYSTEM_GLYPH} ", style=SYSTEM_STYLE)
    text.append(content, style=SYSTEM_STYLE)
    return text


def render_spinner(elapsed_s: float, tool_name: str | None = None) -> Text:
    """Render an animated spinner with verb."""
    frame_idx = int(elapsed_s * 8) % len(SPINNER_FRAMES)
    verb_idx = int(elapsed_s / 3) % len(SPINNER_VERBS)
    frame = SPINNER_FRAMES[frame_idx]
    verb = SPINNER_VERBS[verb_idx]

    text = Text()
    text.append(f" {frame} ", style="bright_blue bold")
    if tool_name:
        text.append(f"Running {tool_name}...", style=DIM_STYLE)
    else:
        text.append(f"{verb}...", style=DIM_STYLE)
    elapsed_display = f" ({elapsed_s:.1f}s)" if elapsed_s > 2.0 else ""
    text.append(elapsed_display, style=DIM_STYLE)
    return text


def render_status_bar(
    model: str,
    usage: Usage,
    cost: float,
    turn_count: int,
    cwd: str = "",
) -> Table:
    """Render the bottom status bar."""
    table = Table.grid(expand=True)
    table.add_column(ratio=1)
    table.add_column(justify="right")

    left = Text()
    left.append(" ● ", style="bright_green")
    left.append(model, style="status.key")
    if cwd:
        left.append(f"  📁 {_short_path(cwd)}", style="dim")

    right = Text()
    right.append(f"↓{usage.prompt_tokens}", style="status.value")
    right.append(" ", style="dim")
    right.append(f"↑{usage.completion_tokens}", style="status.value")
    right.append(f"  T{turn_count}", style="dim")
    if cost > 0:
        right.append(f"  ${cost:.4f}", style=COST_STYLE)

    table.add_row(left, right)
    return table


def render_welcome(model: str) -> Panel:
    """Render the welcome banner."""
    text = Text()
    text.append("🔥 Calcifer", style="bold bright_red")
    text.append(" Agent Runner\n", style="bold")
    text.append(f"   Model: {model}\n", style="dim")
    text.append("   Type your message, or /help for commands. Ctrl+D to exit.", style="dim")
    return Panel(text, border_style="bright_red", padding=(0, 1))


def render_compact_notification(pre_tokens: int, post_tokens: int) -> Text:
    """Render a compaction notification."""
    freed = pre_tokens - post_tokens
    text = Text()
    text.append(f" {SYSTEM_GLYPH} ", style=SYSTEM_STYLE)
    text.append(f"Context compacted: {pre_tokens:,} → {post_tokens:,} tokens ", style=SYSTEM_STYLE)
    text.append(f"(freed {freed:,})", style=DIM_STYLE)
    return text


# -- Helpers --

def _summarize_tool_args(tool_name: str, arguments: str) -> str:
    """Produce a short summary of tool arguments."""
    import json
    try:
        args = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        return ""

    if tool_name in ("bash", "execute"):
        cmd = args.get("command", "")
        return f"`{cmd[:60]}{'...' if len(cmd) > 60 else ''}`"
    if tool_name in ("file_read", "read"):
        return args.get("file_path", "")
    if tool_name in ("file_write", "write"):
        return args.get("file_path", "")
    if tool_name in ("file_edit", "edit"):
        return args.get("file_path", "")
    if tool_name in ("grep",):
        return f'/{args.get("pattern", "")}/'
    if tool_name in ("glob",):
        return args.get("pattern", "")
    # Generic: show first string value
    for v in args.values():
        if isinstance(v, str) and len(v) > 0:
            return v[:50]
    return ""


def _short_path(path: str) -> str:
    """Shorten a path for display."""
    import os
    home = os.path.expanduser("~")
    if path.startswith(home):
        return "~" + path[len(home):]
    return path
