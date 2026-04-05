"""Web GUI for Calcifer agent runner.

FastAPI + SSE backend with embedded HTML/JS frontend.
Zero external frontend dependencies — single Python process.
"""

from .server import create_app, run_server

__all__ = ["create_app", "run_server"]
