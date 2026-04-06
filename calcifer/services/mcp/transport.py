"""MCP transports: stdio, SSE, HTTP, WebSocket.

Implements JSON-RPC 2.0 message framing with:
- Connection lifecycle (timeout, reconnect, cleanup)
- Graceful shutdown (SIGINT → SIGTERM → SIGKILL for stdio)
- Health monitoring
"""

from __future__ import annotations

import asyncio
import json
import logging
import unicodedata
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

# Connection constants
CONNECT_TIMEOUT_S = 30.0
STDIO_SHUTDOWN_GRACE_S = 0.1
STDIO_KILL_TIMEOUT_S = 5.0
MAX_RECONNECT_ATTEMPTS = 3
RECONNECT_BASE_DELAY_S = 1.0


def sanitize_unicode(text: str) -> str:
    """Remove problematic Unicode characters from MCP messages."""
    # Remove null bytes and other control characters (except newline/tab)
    return "".join(
        c for c in text
        if c in ("\n", "\t", "\r") or (unicodedata.category(c) != "Cc")
    )


class MCPTransport(ABC):
    """Base class for MCP transports."""

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def send(self, message: dict[str, Any]) -> None: ...

    @abstractmethod
    async def receive(self) -> dict[str, Any]: ...

    @abstractmethod
    async def close(self) -> None: ...

    async def update_headers(self, headers: dict[str, str]) -> None:
        """Update auth / request headers for subsequent send() calls.

        Default implementation is a no-op — transports that don't carry
        HTTP-style headers (stdio) should leave this unchanged. HTTP-based
        transports override to merge into their internal header dict.

        Used by MCPClient's on_auth_error refresh path.
        """
        logger.debug("update_headers: no-op on %s", type(self).__name__)

    @property
    def is_connected(self) -> bool:
        return False


class StdioTransport(MCPTransport):
    """MCP transport over subprocess stdin/stdout.

    Lifecycle: spawn → communicate → SIGINT → wait 100ms → SIGTERM → wait 5s → SIGKILL
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ):
        self._command = command
        self._args = args or []
        self._env = env
        self._process: asyncio.subprocess.Process | None = None

    @property
    def is_connected(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def connect(self) -> None:
        import os
        env = dict(os.environ)
        if self._env:
            env.update(self._env)

        self._process = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                self._command, *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            ),
            timeout=CONNECT_TIMEOUT_S,
        )
        logger.debug("MCP stdio: started %s (PID %d)", self._command, self._process.pid)

    async def send(self, message: dict[str, Any]) -> None:
        if not self._process or not self._process.stdin:
            raise RuntimeError("Transport not connected")
        data = sanitize_unicode(json.dumps(message)) + "\n"
        self._process.stdin.write(data.encode())
        await self._process.stdin.drain()

    async def receive(self) -> dict[str, Any]:
        if not self._process or not self._process.stdout:
            raise RuntimeError("Transport not connected")
        line = await self._process.stdout.readline()
        if not line:
            raise ConnectionError("MCP subprocess closed stdout")
        return json.loads(sanitize_unicode(line.decode().strip()))

    async def close(self) -> None:
        """Graceful shutdown: SIGINT → 100ms → SIGTERM → 5s → SIGKILL."""
        if not self._process:
            return

        if self._process.returncode is not None:
            self._process = None
            return

        # Step 1: SIGINT (polite ask)
        try:
            self._process.send_signal(2)  # SIGINT
        except ProcessLookupError:
            self._process = None
            return

        try:
            await asyncio.wait_for(self._process.wait(), timeout=STDIO_SHUTDOWN_GRACE_S)
            self._process = None
            return
        except asyncio.TimeoutError:
            pass

        # Step 2: SIGTERM
        try:
            self._process.terminate()
        except ProcessLookupError:
            self._process = None
            return

        try:
            await asyncio.wait_for(self._process.wait(), timeout=STDIO_KILL_TIMEOUT_S)
        except asyncio.TimeoutError:
            # Step 3: SIGKILL
            try:
                self._process.kill()
                await self._process.wait()
            except ProcessLookupError:
                pass

        self._process = None
        logger.debug("MCP stdio: process cleaned up")


class SSETransport(MCPTransport):
    """MCP transport over HTTP Server-Sent Events with reconnection."""

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        max_reconnect: int = MAX_RECONNECT_ATTEMPTS,
    ):
        self._url = url.rstrip("/")
        self._headers = headers or {}
        self._max_reconnect = max_reconnect
        self._client: Any = None
        self._message_endpoint: str | None = None
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._sse_task: asyncio.Task[None] | None = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        import httpx
        self._client = httpx.AsyncClient(headers=self._headers, timeout=300.0)
        self._sse_task = asyncio.create_task(self._listen_sse())
        try:
            msg = await asyncio.wait_for(self._event_queue.get(), timeout=CONNECT_TIMEOUT_S)
            if "endpoint" in msg:
                self._message_endpoint = msg["endpoint"]
            self._connected = True
        except asyncio.TimeoutError:
            await self.close()
            raise ConnectionError("SSE connection timed out")

    async def _listen_sse(self) -> None:
        reconnect_count = 0
        while reconnect_count <= self._max_reconnect:
            try:
                async with self._client.stream("GET", self._url) as resp:
                    self._connected = True
                    reconnect_count = 0
                    event_data = ""
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            event_data = line[6:]
                        elif line == "" and event_data:
                            try:
                                parsed = json.loads(sanitize_unicode(event_data))
                                await self._event_queue.put(parsed)
                            except json.JSONDecodeError:
                                pass
                            event_data = ""
            except asyncio.CancelledError:
                return
            except Exception as e:
                self._connected = False
                reconnect_count += 1
                if reconnect_count > self._max_reconnect:
                    logger.error("SSE max reconnect reached: %s", e)
                    await self._event_queue.put({"error": str(e)})
                    return
                delay = RECONNECT_BASE_DELAY_S * (2 ** (reconnect_count - 1))
                logger.warning("SSE reconnecting (%d/%d) in %.1fs: %s",
                             reconnect_count, self._max_reconnect, delay, e)
                await asyncio.sleep(delay)

    async def send(self, message: dict[str, Any]) -> None:
        if not self._client:
            raise RuntimeError("Transport not connected")
        endpoint = self._message_endpoint or self._url
        resp = await self._client.post(endpoint, json=message)
        resp.raise_for_status()

    async def receive(self) -> dict[str, Any]:
        msg = await self._event_queue.get()
        if "error" in msg and not msg.get("jsonrpc"):
            raise ConnectionError(msg["error"])
        return msg

    async def update_headers(self, headers: dict[str, str]) -> None:
        """Merge new headers into this SSE transport.

        Takes effect on the next send() (POST) call. Existing SSE stream
        continues with old headers until the next reconnect.
        """
        self._headers.update(headers)
        if self._client is not None:
            # httpx.AsyncClient.headers is a mutable Headers instance
            for k, v in headers.items():
                self._client.headers[k] = v
        logger.debug("SSE transport %s: updated %d header(s)", self._url, len(headers))

    async def close(self) -> None:
        self._connected = False
        if self._sse_task:
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()


class HTTPTransport(MCPTransport):
    """MCP transport over HTTP POST (stateless, no streaming)."""

    def __init__(self, url: str, headers: dict[str, str] | None = None):
        self._url = url.rstrip("/")
        self._headers = headers or {}
        self._client: Any = None
        self._response_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        import httpx
        self._client = httpx.AsyncClient(headers=self._headers, timeout=60.0)
        self._connected = True

    async def send(self, message: dict[str, Any]) -> None:
        if not self._client:
            raise RuntimeError("Not connected")
        resp = await self._client.post(self._url, json=message)
        resp.raise_for_status()
        if resp.content:
            data = resp.json()
            await self._response_queue.put(data)

    async def receive(self) -> dict[str, Any]:
        return await self._response_queue.get()

    async def update_headers(self, headers: dict[str, str]) -> None:
        """Merge new headers into this HTTP transport.

        Takes effect on the next send() (POST) call.
        """
        self._headers.update(headers)
        if self._client is not None:
            for k, v in headers.items():
                self._client.headers[k] = v
        logger.debug("HTTP transport %s: updated %d header(s)", self._url, len(headers))

    async def close(self) -> None:
        self._connected = False
        if self._client:
            await self._client.aclose()


class WebSocketTransport(MCPTransport):
    """MCP transport over WebSocket."""

    def __init__(self, url: str, headers: dict[str, str] | None = None):
        self._url = url
        self._headers = headers or {}
        self._ws: Any = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        try:
            import websockets
        except ImportError:
            raise ImportError("websockets package required for WebSocket transport: pip install websockets")

        self._ws = await asyncio.wait_for(
            websockets.connect(self._url, additional_headers=self._headers),
            timeout=CONNECT_TIMEOUT_S,
        )
        self._connected = True

    async def send(self, message: dict[str, Any]) -> None:
        if not self._ws:
            raise RuntimeError("Not connected")
        await self._ws.send(json.dumps(message))

    async def receive(self) -> dict[str, Any]:
        if not self._ws:
            raise RuntimeError("Not connected")
        data = await self._ws.recv()
        return json.loads(data)

    async def update_headers(self, headers: dict[str, str]) -> None:
        """Update headers for the next WebSocket reconnect.

        WebSocket headers are set at handshake time, so an already-open
        connection keeps its old headers. The new values take effect
        when the transport is closed and reconnected.
        """
        self._headers.update(headers)
        logger.debug(
            "WebSocket transport %s: %d header(s) staged for next reconnect",
            self._url, len(headers),
        )

    async def close(self) -> None:
        self._connected = False
        if self._ws:
            await self._ws.close()
