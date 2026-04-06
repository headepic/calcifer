"""MCP Client: JSON-RPC 2.0 protocol over configurable transports.

Implements the Model Context Protocol client:
- initialize handshake
- tools/list discovery
- tools/call execution
- on_auth_error refresh callback for HTTP 401/403
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .transport import MCPTransport

logger = logging.getLogger(__name__)

# Callback signature: (server_name) -> new headers or None to give up
OnAuthErrorFn = Callable[[str], Awaitable["dict[str, str] | None"]]


@dataclass
class MCPToolSchema:
    """Schema for a tool discovered from an MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str
    # Annotations from MCP tool definition (readOnlyHint, destructiveHint, etc.)
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPClient:
    """Client for a single MCP server.

    Pluggable auth refresh: pass `on_auth_error=<async callable>` to the
    constructor. On HTTP 401/403 from the transport layer, the callback
    is invoked with the server name and must return either a dict of new
    headers (triggers single retry with updated headers) or None
    (re-raises the original auth error). Callback exceptions are logged
    and the original auth error is re-raised.
    """

    name: str
    transport: MCPTransport
    on_auth_error: OnAuthErrorFn | None = None
    _request_id: int = field(default=0, init=False)
    _tools: list[MCPToolSchema] = field(default_factory=list, init=False)
    _schema_cache: list[MCPToolSchema] = field(default_factory=list, init=False)

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _transport_send(
        self, request: dict[str, Any], _auth_retry_count: int = 0,
    ) -> None:
        """Send a request through the transport with auth-refresh handling.

        Catches httpx.HTTPStatusError at the transport boundary (where it's
        raised by raise_for_status()). On 401/403 with a callback and no
        prior auth retry, invoke the callback and retry once with new
        headers. This layer is separate from _send_request's JSON-RPC
        response loop because HTTP auth errors never reach that loop.
        """
        try:
            import httpx
        except ImportError:
            # httpx not importable for some reason — just call send
            await self.transport.send(request)
            return

        try:
            await self.transport.send(request)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            should_refresh = (
                status in (401, 403)
                and self.on_auth_error is not None
                and _auth_retry_count == 0
            )
            if not should_refresh:
                raise

            logger.info(
                "MCP %s: auth error %d, invoking on_auth_error callback",
                self.name, status,
            )
            try:
                new_headers = await self.on_auth_error(self.name)  # type: ignore[misc]
            except Exception as cb_exc:
                logger.warning(
                    "MCP %s: on_auth_error callback raised %s; re-raising original auth error",
                    self.name, cb_exc,
                )
                raise e from cb_exc

            if not new_headers:
                logger.info(
                    "MCP %s: on_auth_error returned no new headers; re-raising",
                    self.name,
                )
                raise

            await self.transport.update_headers(new_headers)
            logger.info("MCP %s: retrying with refreshed auth headers", self.name)
            return await self._transport_send(request, _auth_retry_count=1)

    async def _send_request(
        self, method: str, params: dict[str, Any] | None = None,
        _retry_count: int = 0,
    ) -> Any:
        """Send a JSON-RPC request and wait for response.

        Handles session expiry: HTTP 404 or error code -32001 triggers
        session rebuild (reconnect + reinitialize).

        HTTP 401/403 is handled one layer down in _transport_send via the
        on_auth_error callback.
        """
        request = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
        }
        if params is not None:
            request["params"] = params

        await self._transport_send(request)

        while True:
            response = await self.transport.receive()
            if response.get("id") == request["id"]:
                if "error" in response:
                    error = response["error"]
                    error_code = error.get("code", 0)

                    # Session expiry detection (HTTP 404 or -32001)
                    if error_code == -32001 and _retry_count < 1:
                        logger.warning("MCP %s: session expired, rebuilding...", self.name)
                        await self._rebuild_session()
                        return await self._send_request(method, params, _retry_count=1)

                    raise RuntimeError(
                        f"MCP error {error_code}: {error.get('message')}"
                    )
                return response.get("result")

    async def _rebuild_session(self) -> None:
        """Rebuild an expired MCP session by reconnecting."""
        logger.info("MCP %s: rebuilding session", self.name)
        try:
            await self.transport.close()
        except Exception:
            pass
        await self.transport.connect()
        await self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "calcifer", "version": "0.1.0"},
            },
        )
        await self._transport_send(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        # Re-discover tools using cache (server likely hasn't changed)
        if self._schema_cache:
            await self.discover_tools(use_cache=True)
        logger.info("MCP %s: session rebuilt", self.name)

    async def connect(self) -> None:
        """Connect transport and perform MCP initialize handshake."""
        await self.transport.connect()

        # Initialize
        result = await self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "calcifer",
                    "version": "0.1.0",
                },
            },
        )
        logger.debug("MCP %s initialized: %s", self.name, result)

        # Send initialized notification
        await self._transport_send(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )

    async def discover_tools(self, use_cache: bool = False) -> list[MCPToolSchema]:
        """Discover tools from the MCP server.

        If use_cache=True and we have a cached schema list (from a prior
        discover_tools call), return it without hitting the server. This is
        useful after reconnecting — avoids re-listing tools when the server
        likely hasn't changed.
        """
        if use_cache and self._schema_cache:
            logger.info("MCP %s: using cached schemas (%d tools)", self.name, len(self._schema_cache))
            self._tools = list(self._schema_cache)
            return self._tools

        result = await self._send_request("tools/list")
        tools_data = result.get("tools", [])

        self._tools = []
        for td in tools_data:
            self._tools.append(
                MCPToolSchema(
                    name=td.get("name", ""),
                    description=td.get("description", ""),
                    input_schema=td.get("inputSchema", {"type": "object"}),
                    server_name=self.name,
                    annotations=td.get("annotations", {}),
                )
            )

        # Cache for reconnection
        self._schema_cache = list(self._tools)

        logger.info(
            "MCP %s: discovered %d tools: %s",
            self.name,
            len(self._tools),
            [t.name for t in self._tools],
        )
        return self._tools

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        """Call a tool on the MCP server."""
        result = await self._send_request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )
        return result

    @property
    def tools(self) -> list[MCPToolSchema]:
        return list(self._tools)

    # -- Resources --

    async def list_resources(self) -> list[dict[str, Any]]:
        """List available resources from the MCP server."""
        try:
            result = await self._send_request("resources/list")
            resources = result.get("resources", [])
            logger.info("MCP %s: %d resources available", self.name, len(resources))
            return resources
        except Exception as e:
            logger.debug("MCP %s: resources not supported: %s", self.name, e)
            return []

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """Read a resource by URI."""
        result = await self._send_request("resources/read", {"uri": uri})
        return result

    async def close(self) -> None:
        """Close the connection."""
        await self.transport.close()
