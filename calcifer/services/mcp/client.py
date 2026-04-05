"""MCP Client: JSON-RPC 2.0 protocol over configurable transports.

Implements the Model Context Protocol client:
- initialize handshake
- tools/list discovery
- tools/call execution
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .transport import MCPTransport

logger = logging.getLogger(__name__)


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
    """Client for a single MCP server."""

    name: str
    transport: MCPTransport
    _request_id: int = field(default=0, init=False)
    _tools: list[MCPToolSchema] = field(default_factory=list, init=False)
    _schema_cache: list[MCPToolSchema] = field(default_factory=list, init=False)

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send_request(
        self, method: str, params: dict[str, Any] | None = None,
        _retry_count: int = 0,
    ) -> Any:
        """Send a JSON-RPC request and wait for response.

        Handles session expiry: HTTP 404 or error code -32001 triggers
        session rebuild (reconnect + reinitialize).
        """
        request = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
        }
        if params is not None:
            request["params"] = params

        await self.transport.send(request)

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
        await self.transport.send(
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
        await self.transport.send(
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
