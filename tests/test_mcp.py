"""Tests for MCP client and tool adapter."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from calcifer.services.mcp.client import MCPClient, MCPToolSchema
from calcifer.services.mcp.tool_adapter import MCPToolAdapter, create_mcp_tools
from calcifer.types.tools import ToolContext


class MockTransport:
    """Mock MCP transport for testing."""

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self._sent: list[dict] = []

    async def connect(self):
        pass

    async def send(self, message: dict):
        self._sent.append(message)

    async def receive(self) -> dict:
        if self._responses:
            return self._responses.pop(0)
        raise ConnectionError("No more responses")

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_mcp_client_initialize():
    transport = MockTransport([
        # initialize response
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "test", "version": "1.0"},
            },
        },
    ])

    client = MCPClient(name="test", transport=transport)
    await client.connect()

    # Should have sent initialize request + initialized notification
    assert len(transport._sent) == 2
    assert transport._sent[0]["method"] == "initialize"
    assert transport._sent[1]["method"] == "notifications/initialized"


@pytest.mark.asyncio
async def test_mcp_client_discover_tools():
    transport = MockTransport([
        # initialize
        {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
        # tools/list
        {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    {
                        "name": "add",
                        "description": "Add two numbers",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "a": {"type": "integer"},
                                "b": {"type": "integer"},
                            },
                            "required": ["a", "b"],
                        },
                    },
                ]
            },
        },
    ])

    client = MCPClient(name="math", transport=transport)
    await client.connect()
    tools = await client.discover_tools()

    assert len(tools) == 1
    assert tools[0].name == "add"
    assert tools[0].server_name == "math"


@pytest.mark.asyncio
async def test_mcp_client_call_tool():
    transport = MockTransport([
        # initialize
        {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
        # tools/call
        {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [{"type": "text", "text": "5"}],
            },
        },
    ])

    client = MCPClient(name="math", transport=transport)
    await client.connect()
    result = await client.call_tool("add", {"a": 2, "b": 3})

    assert result["content"][0]["text"] == "5"


@pytest.mark.asyncio
async def test_mcp_tool_adapter():
    schema = MCPToolSchema(
        name="echo",
        description="Echo input",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        server_name="test",
    )

    # Mock client
    mock_client = MCPClient(name="test", transport=MockTransport([]))
    mock_client.call_tool = AsyncMock(
        return_value={"content": [{"type": "text", "text": "hello"}]}
    )

    adapter = MCPToolAdapter(schema, mock_client)

    assert adapter.name == "mcp__test__echo"
    assert adapter.is_concurrency_safe is True

    # Test schema generation
    openai_schema = adapter.to_openai_schema()
    assert openai_schema["function"]["name"] == "mcp__test__echo"

    # Test call
    args = adapter.validate_input({"text": "hello"})
    result = await adapter.call(args, ToolContext())
    assert result.content == "hello"


def test_create_mcp_tools():
    schemas = [
        MCPToolSchema(name="a", description="A", input_schema={"type": "object"}, server_name="s"),
        MCPToolSchema(name="b", description="B", input_schema={"type": "object"}, server_name="s"),
    ]

    mock_client = MCPClient(name="s", transport=MockTransport([]))
    tools = create_mcp_tools(schemas, mock_client)

    assert len(tools) == 2
    assert tools[0].name == "mcp__s__a"
    assert tools[1].name == "mcp__s__b"


# ================ Auth refresh callback tests ================
#
# on_auth_error is a pluggable callback invoked when the HTTP transport
# raises httpx.HTTPStatusError with 401/403. Four scenarios:
#   1. Baseline: no callback set → error propagates (no_callback test)
#   2. Callback returns new headers → retry succeeds (callback_success)
#   3. Callback returns None → error propagates (callback_none)
#   4. Callback raises an exception → original error propagates (callback_exception)


class _FakeHTTPStatusError(Exception):
    """Stand-in for httpx.HTTPStatusError — just carries a .response.status_code."""
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.response = type("R", (), {"status_code": status_code})()


class AuthRefreshTransport:
    """Mock transport that raises an auth error on first send, then succeeds.

    Tracks: headers updated via update_headers, number of send calls.
    """

    def __init__(
        self,
        auth_fail_status: int = 401,
        fail_count: int = 1,
        success_responses: list[dict] | None = None,
    ):
        self._fail_count = fail_count
        self._auth_fail_status = auth_fail_status
        self.send_count = 0
        self.updated_headers: list[dict] = []
        self._responses = list(success_responses or [])

    async def connect(self):
        pass

    async def send(self, message: dict):
        self.send_count += 1
        if self.send_count <= self._fail_count:
            # Raise a fake HTTPStatusError — MCPClient's handler imports httpx
            # and catches httpx.HTTPStatusError, so we must raise a REAL one.
            import httpx
            request = httpx.Request("POST", "http://test")
            response = httpx.Response(self._auth_fail_status, request=request)
            raise httpx.HTTPStatusError(
                f"HTTP {self._auth_fail_status}",
                request=request, response=response,
            )

    async def receive(self) -> dict:
        if self._responses:
            return self._responses.pop(0)
        raise ConnectionError("No more responses")

    async def close(self):
        pass

    async def update_headers(self, headers: dict):
        self.updated_headers.append(dict(headers))


@pytest.mark.asyncio
async def test_mcp_auth_refresh_callback_success():
    """401 → callback returns new headers → retry succeeds."""
    transport = AuthRefreshTransport(
        fail_count=1,
        success_responses=[{"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}],
    )

    async def on_auth_error(server_name: str) -> dict[str, str] | None:
        assert server_name == "test-server"
        return {"Authorization": "Bearer new-token"}

    client = MCPClient(
        name="test-server",
        transport=transport,
        on_auth_error=on_auth_error,
    )

    result = await client._send_request("tools/list")
    assert result == {"ok": True}
    # First send failed with 401, second send succeeded
    assert transport.send_count == 2
    # update_headers was called with the new auth
    assert transport.updated_headers == [{"Authorization": "Bearer new-token"}]


@pytest.mark.asyncio
async def test_mcp_auth_refresh_callback_none():
    """401 → callback returns None → original error propagates."""
    transport = AuthRefreshTransport(fail_count=1)

    async def on_auth_error(server_name: str) -> dict[str, str] | None:
        return None

    client = MCPClient(
        name="srv",
        transport=transport,
        on_auth_error=on_auth_error,
    )

    import httpx
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client._send_request("tools/list")
    assert exc_info.value.response.status_code == 401
    # Only one send attempt (no retry)
    assert transport.send_count == 1
    assert transport.updated_headers == []


@pytest.mark.asyncio
async def test_mcp_auth_refresh_no_callback():
    """401 with no callback set → error propagates (baseline behavior)."""
    transport = AuthRefreshTransport(fail_count=1)

    client = MCPClient(name="srv", transport=transport)  # on_auth_error=None (default)

    import httpx
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client._send_request("tools/list")
    assert exc_info.value.response.status_code == 401
    assert transport.send_count == 1
    assert transport.updated_headers == []


@pytest.mark.asyncio
async def test_mcp_auth_refresh_callback_exception():
    """Callback raises → original auth error propagates (not the callback's)."""
    transport = AuthRefreshTransport(fail_count=1, auth_fail_status=403)

    callback_called = {"count": 0}

    async def on_auth_error(server_name: str) -> dict[str, str] | None:
        callback_called["count"] += 1
        raise RuntimeError("refresh service is down")

    client = MCPClient(
        name="srv",
        transport=transport,
        on_auth_error=on_auth_error,
    )

    import httpx
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client._send_request("tools/list")
    # Original 403 is raised, not the callback's RuntimeError
    assert exc_info.value.response.status_code == 403
    assert callback_called["count"] == 1
    # No retry after callback failure
    assert transport.send_count == 1


@pytest.mark.asyncio
async def test_mcp_auth_refresh_only_retries_once():
    """Even if the retry also gets 401, we don't loop (max 1 refresh attempt)."""
    transport = AuthRefreshTransport(fail_count=5)  # would fail 5 times if retried

    refresh_count = {"n": 0}

    async def on_auth_error(server_name: str) -> dict[str, str] | None:
        refresh_count["n"] += 1
        return {"Authorization": f"Bearer attempt-{refresh_count['n']}"}

    client = MCPClient(
        name="srv",
        transport=transport,
        on_auth_error=on_auth_error,
    )

    import httpx
    with pytest.raises(httpx.HTTPStatusError):
        await client._send_request("tools/list")

    # Callback called exactly once (no loop)
    assert refresh_count["n"] == 1
    # Exactly 2 sends: initial + 1 retry
    assert transport.send_count == 2
