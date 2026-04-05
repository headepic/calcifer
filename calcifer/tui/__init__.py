"""Terminal UI for Calcifer agent runner.

Three modes:
- Interactive TUI (default): Rich + Prompt Toolkit chat interface
- Print mode (-p): Non-interactive streaming to stdout
- Backend mode (--backend): JSON-lines protocol for external frontends
"""

from .app import run_tui, run_print_mode

__all__ = ["run_tui", "run_print_mode"]
