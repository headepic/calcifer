"""Color theme and styling constants for the TUI."""

from rich.style import Style
from rich.theme import Theme

# Role colors (inspired by Claude Code's color scheme)
USER_STYLE = Style(color="white", bold=True)
ASSISTANT_STYLE = Style(color="bright_green", bold=True)
TOOL_STYLE = Style(color="bright_cyan", bold=True)
TOOL_RESULT_STYLE = Style(color="cyan", dim=True)
SYSTEM_STYLE = Style(color="yellow")
ERROR_STYLE = Style(color="red", bold=True)
DIM_STYLE = Style(dim=True)
COST_STYLE = Style(color="bright_yellow")
SPINNER_STYLE = Style(color="bright_blue", bold=True)

# Glyphs (matching Claude Code's UI)
USER_GLYPH = "❯"
ASSISTANT_GLYPH = "⏺"
TOOL_GLYPH = "⏵"
RESULT_GLYPH = "  ↳"
SYSTEM_GLYPH = "ℹ"
ERROR_GLYPH = "✗"
SUCCESS_GLYPH = "✓"

# Spinner frames
SPINNER_FRAMES = ["◜", "◠", "◝", "◞", "◡", "◟"]
SPINNER_VERBS = [
    "Thinking", "Reasoning", "Processing", "Analyzing",
    "Considering", "Working", "Evaluating",
]

# Theme for Rich console
CALCIFER_THEME = Theme({
    "user": "white bold",
    "assistant": "bright_green bold",
    "tool": "bright_cyan bold",
    "tool.result": "cyan dim",
    "system": "yellow",
    "error": "red bold",
    "dim": "dim",
    "cost": "bright_yellow",
    "spinner": "bright_blue bold",
    "status.key": "bright_blue",
    "status.value": "white",
    "header": "bright_white on blue",
})
