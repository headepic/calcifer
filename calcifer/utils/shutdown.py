"""Graceful shutdown: structured cleanup with signal handling.

Mirrors Claude Code's utils/gracefulShutdown.ts:
- Cleanup registry (register/unregister cleanup functions)
- Ordered shutdown: cleanup → hooks → flush → force exit
- Failsafe timer (max 5s + hook budget)
- SIGINT/SIGTERM/SIGHUP signal handling
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Shutdown timing
CLEANUP_TIMEOUT_S = 2.0
HOOK_TIMEOUT_S = 5.0
FAILSAFE_TIMEOUT_S = 10.0


CleanupFn = Callable[[], Awaitable[None] | None]


class ShutdownManager:
    """Manages graceful shutdown with ordered cleanup."""

    def __init__(self) -> None:
        self._cleanups: list[tuple[str, CleanupFn]] = []
        self._shutdown_hooks: list[CleanupFn] = []
        self._is_shutting_down = False
        self._signals_installed = False

    def register_cleanup(self, name: str, fn: CleanupFn) -> None:
        """Register a cleanup function. Called during shutdown in LIFO order."""
        self._cleanups.append((name, fn))

    def unregister_cleanup(self, name: str) -> None:
        """Remove a cleanup function by name."""
        self._cleanups = [(n, f) for n, f in self._cleanups if n != name]

    def register_shutdown_hook(self, fn: CleanupFn) -> None:
        """Register a hook called after cleanup (e.g., session end hook)."""
        self._shutdown_hooks.append(fn)

    def install_signal_handlers(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Install SIGINT/SIGTERM handlers for graceful shutdown."""
        if self._signals_installed:
            return

        _loop = loop or asyncio.get_event_loop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                _loop.add_signal_handler(sig, lambda s=sig: asyncio.ensure_future(self.shutdown(str(s))))
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                signal.signal(sig, lambda s, f: asyncio.ensure_future(self.shutdown(str(s))))

        # SIGHUP (terminal disconnected)
        if hasattr(signal, "SIGHUP"):
            try:
                _loop.add_signal_handler(signal.SIGHUP, lambda: asyncio.ensure_future(self.shutdown("SIGHUP")))
            except (NotImplementedError, OSError):
                pass

        self._signals_installed = True

    async def shutdown(self, reason: str = "unknown") -> None:
        """Execute the shutdown sequence.

        Order:
        1. Run cleanup functions (LIFO, 2s timeout each)
        2. Run shutdown hooks (5s total)
        3. Force exit if still running after failsafe timer
        """
        if self._is_shutting_down:
            logger.debug("Shutdown already in progress, forcing exit")
            sys.exit(1)
            return

        self._is_shutting_down = True
        logger.info("Shutdown initiated: %s", reason)

        # Failsafe: force exit after timeout
        failsafe = asyncio.get_event_loop().call_later(
            FAILSAFE_TIMEOUT_S, lambda: sys.exit(1)
        )

        # Step 1: Cleanup functions (LIFO)
        for name, fn in reversed(self._cleanups):
            try:
                result = fn()
                if asyncio.iscoroutine(result):
                    await asyncio.wait_for(result, timeout=CLEANUP_TIMEOUT_S)
                logger.debug("Cleanup '%s' completed", name)
            except asyncio.TimeoutError:
                logger.warning("Cleanup '%s' timed out", name)
            except Exception as e:
                logger.warning("Cleanup '%s' failed: %s", name, e)

        # Step 2: Shutdown hooks
        for hook in self._shutdown_hooks:
            try:
                result = hook()
                if asyncio.iscoroutine(result):
                    await asyncio.wait_for(result, timeout=HOOK_TIMEOUT_S)
            except asyncio.TimeoutError:
                logger.warning("Shutdown hook timed out")
            except Exception as e:
                logger.warning("Shutdown hook failed: %s", e)

        failsafe.cancel()
        logger.info("Shutdown complete")


# Global singleton
_manager: ShutdownManager | None = None


def get_shutdown_manager() -> ShutdownManager:
    """Get the global shutdown manager."""
    global _manager
    if _manager is None:
        _manager = ShutdownManager()
    return _manager
