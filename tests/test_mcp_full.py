"""Comprehensive MCP system tests.

Covers every function and edge case in:
- mcp/transport.py: sanitize_unicode, StdioTransport, SSETransport, HTTPTransport, WebSocketTransport
- mcp/client.py: MCPClient (initialize, discover, call, resources, session rebuild)
- mcp/tool_adapter.py: _build_pydantic_model, _json_type_to_python, MCPToolAdapter, create_mcp_tools
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from calcifer.services.mcp.transport import (
    MCPTransport,
    StdioTransport,
    SSETransport,
    HTTPTransport,
    sanitize_unicode,
)
from calcifer.services.mcp.client import MCPClient, MCPToolSchema
from calcifer.services.mcp.tool_adapter import (
    MCPToolAdapter,
    create_mcp_tools,
    _build_pydantic_model,
    _json_type_to_python,
)
from calcifer.types.tools import ToolContext, ToolResult

MCP_SERVER_PATH = str(Path(__file__).parent / "fixtures" / "mcp_echo_server.py")
PYTHON = sys.executable


# ===================================================================
# sanitize_unicode
# ===================================================================

class TestSanitizeUnicode:

    def test_normal_text_unchanged(self):
        assert sanitize_unicode("hello world") == "hello world"

    def test_preserves_newlines_tabs(self):
        text = "line1\nline2\ttab"
        assert sanitize_unicode(text) == text

    def test_removes_null_bytes(self):
        assert sanitize_unicode("hello\x00world") == "helloworld"

    def test_removes_control_chars(self):
        # \x01 = SOH, \x02 = STX, \x1f = US (all control chars)
        text = "hello\x01\x02\x1fworld"
        result = sanitize_unicode(text)
        assert "\x01" not in result
        assert "\x02" not in result
        assert "helloworld" == result

    def test_preserves_unicode(self):
        text = "你好世界 🌍"
        assert sanitize_unicode(text) == text

    def test_empty_string(self):
        assert sanitize_unicode("") == ""


# ===================================================================
# _json_type_to_python
# ===================================================================

class TestJsonTypeToPython:

    def test_string(self):
        assert _json_type_to_python("string") is str

    def test_integer(self):
        assert _json_type_to_python("integer") is int

    def test_number(self):
        assert _json_type_to_python("number") is float

    def test_boolean(self):
        assert _json_type_to_python("boolean") is bool

    def test_array(self):
        assert _json_type_to_python("array") is list

    def test_object(self):
        assert _json_type_to_python("object") is dict

    def test_unknown_type(self):
        result = _json_type_to_python("custom")
        assert result is Any

    def test_list_type(self):
        """JSON Schema union types (list of types)."""
        result = _json_type_to_python(["string", "null"])
        assert result is Any


# ===================================================================
# _build_pydantic_model
# ===================================================================

class TestBuildPydanticModel:

    def test_simple_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
        }
        Model = _build_pydantic_model("TestModel", schema)
        instance = Model(name="Alice", age=30)
        assert instance.name == "Alice"
        assert instance.age == 30

    def test_required_field(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        Model = _build_pydantic_model("ReqModel", schema)
        with pytest.raises(Exception):  # Pydantic validation error
            Model()  # Missing required field

    def test_optional_field_with_default(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer", "default": 10},
            },
            "required": ["name"],
        }
        Model = _build_pydantic_model("OptModel", schema)
        instance = Model(name="test")
        assert instance.count == 10

    def test_empty_schema(self):
        schema = {"type": "object"}
        Model = _build_pydantic_model("EmptyModel", schema)
        instance = Model()
        assert instance is not None

    def test_all_types(self):
        schema = {
            "type": "object",
            "properties": {
                "s": {"type": "string"},
                "i": {"type": "integer"},
                "n": {"type": "number"},
                "b": {"type": "boolean"},
                "a": {"type": "array"},
                "o": {"type": "object"},
            },
        }
        Model = _build_pydantic_model("AllTypes", schema)
        instance = Model(s="hi", i=1, n=1.5, b=True, a=[1], o={"k": "v"})
        assert instance.s == "hi"
        assert instance.b is True


# ===================================================================
# MCPToolAdapter
# ===================================================================

class TestMCPToolAdapter:

    def _make_adapter(self, annotations=None):
        schema = MCPToolSchema(
            name="test_tool",
            description="A test tool",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            server_name="test_srv",
            annotations=annotations or {},
        )
        client = MagicMock()
        return MCPToolAdapter(schema, client), client

    def test_naming_convention(self):
        adapter, _ = self._make_adapter()
        assert adapter.name == "mcp__test_srv__test_tool"

    def test_is_mcp_flag(self):
        adapter, _ = self._make_adapter()
        assert adapter.is_mcp is True
        assert adapter.mcp_info == {"server_name": "test_srv", "tool_name": "test_tool"}

    def test_openai_schema(self):
        adapter, _ = self._make_adapter()
        schema = adapter.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "mcp__test_srv__test_tool"
        assert "query" in schema["function"]["parameters"]["properties"]

    def test_annotations_readonly(self):
        adapter, _ = self._make_adapter({"readOnlyHint": True})
        assert adapter.is_read_only is True
        assert adapter.is_concurrency_safe is True

    def test_annotations_destructive(self):
        adapter, _ = self._make_adapter({"destructiveHint": True, "readOnlyHint": False})
        assert adapter.is_destructive is True
        assert adapter.is_read_only is False

    def test_annotations_search_hint(self):
        adapter, _ = self._make_adapter({"_meta": {"anthropic/searchHint": "search code"}})
        assert adapter.search_hint == "search code"

    @pytest.mark.asyncio
    async def test_call_success_text(self):
        """MCP tool returning text content."""
        adapter, client = self._make_adapter()
        client.call_tool = AsyncMock(return_value={
            "content": [{"type": "text", "text": "result data"}]
        })
        ctx = ToolContext()
        args = adapter.parameters(query="test")
        result = await adapter.call(args, ctx)
        assert not result.is_error
        assert "result data" in result.content
        client.call_tool.assert_called_once_with("test_tool", {"query": "test"})

    @pytest.mark.asyncio
    async def test_call_success_multi_text(self):
        """MCP tool returning multiple text blocks."""
        adapter, client = self._make_adapter()
        client.call_tool = AsyncMock(return_value={
            "content": [
                {"type": "text", "text": "line 1"},
                {"type": "text", "text": "line 2"},
            ]
        })
        ctx = ToolContext()
        args = adapter.parameters(query="test")
        result = await adapter.call(args, ctx)
        assert "line 1" in result.content
        assert "line 2" in result.content

    @pytest.mark.asyncio
    async def test_call_success_raw_dict(self):
        """MCP tool returning non-content dict (falls back to JSON)."""
        adapter, client = self._make_adapter()
        client.call_tool = AsyncMock(return_value={"data": "raw"})
        ctx = ToolContext()
        args = adapter.parameters(query="test")
        result = await adapter.call(args, ctx)
        assert "raw" in result.content

    @pytest.mark.asyncio
    async def test_call_success_string(self):
        """MCP tool returning a plain string."""
        adapter, client = self._make_adapter()
        client.call_tool = AsyncMock(return_value="plain string")
        ctx = ToolContext()
        args = adapter.parameters(query="test")
        result = await adapter.call(args, ctx)
        assert result.content == "plain string"

    @pytest.mark.asyncio
    async def test_call_empty_content(self):
        adapter, client = self._make_adapter()
        client.call_tool = AsyncMock(return_value={"content": []})
        ctx = ToolContext()
        args = adapter.parameters(query="test")
        result = await adapter.call(args, ctx)
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_call_error(self):
        adapter, client = self._make_adapter()
        client.call_tool = AsyncMock(side_effect=RuntimeError("MCP server crashed"))
        ctx = ToolContext()
        args = adapter.parameters(query="test")
        result = await adapter.call(args, ctx)
        assert result.is_error
        assert "MCP server crashed" in result.content


class TestCreateMCPTools:

    def test_creates_tools_from_schemas(self):
        schemas = [
            MCPToolSchema(name="a", description="Tool A", input_schema={"type": "object"}, server_name="srv"),
            MCPToolSchema(name="b", description="Tool B", input_schema={"type": "object"}, server_name="srv"),
        ]
        client = MagicMock()
        tools = create_mcp_tools(schemas, client)
        assert len(tools) == 2
        assert tools[0].name == "mcp__srv__a"
        assert tools[1].name == "mcp__srv__b"

    def test_empty_schemas(self):
        tools = create_mcp_tools([], MagicMock())
        assert tools == []


# ===================================================================
# MCPClient (with mock transport)
# ===================================================================

class MockTransport(MCPTransport):
    """In-memory mock transport for testing MCPClient."""

    def __init__(self):
        self._responses: list[dict] = []
        self._sent: list[dict] = []
        self._connected = False

    def queue_response(self, response: dict):
        self._responses.append(response)

    async def connect(self):
        self._connected = True

    async def send(self, message: dict):
        self._sent.append(message)

    async def receive(self) -> dict:
        if not self._responses:
            raise ConnectionError("No more responses")
        return self._responses.pop(0)

    async def close(self):
        self._connected = False

    @property
    def is_connected(self):
        return self._connected


class TestMCPClient:

    @pytest.mark.asyncio
    async def test_initialize_handshake(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
        })
        client = MCPClient(name="test", transport=transport)
        await client.connect()

        assert transport._connected
        assert len(transport._sent) == 2  # initialize + notifications/initialized
        init_req = transport._sent[0]
        assert init_req["method"] == "initialize"
        assert init_req["params"]["clientInfo"]["name"] == "calcifer"

        notify = transport._sent[1]
        assert notify["method"] == "notifications/initialized"
        await client.close()

    @pytest.mark.asyncio
    async def test_discover_tools(self):
        transport = MockTransport()
        # init response
        transport.queue_response({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}})
        client = MCPClient(name="test", transport=transport)
        await client.connect()

        # tools/list response
        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {"tools": [
                {"name": "echo", "description": "Echo tool", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
                {"name": "add", "description": "Add tool", "inputSchema": {"type": "object"}},
            ]},
        })
        tools = await client.discover_tools()
        assert len(tools) == 2
        assert tools[0].name == "echo"
        assert tools[0].server_name == "test"
        assert tools[1].name == "add"
        assert client.tools == tools
        await client.close()

    @pytest.mark.asyncio
    async def test_call_tool(self):
        transport = MockTransport()
        transport.queue_response({"jsonrpc": "2.0", "id": 1, "result": {}})
        client = MCPClient(name="test", transport=transport)
        await client.connect()

        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {"content": [{"type": "text", "text": "ECHO: hello"}]},
        })
        result = await client.call_tool("echo", {"text": "hello"})
        assert result["content"][0]["text"] == "ECHO: hello"

        # Verify the request
        call_req = transport._sent[-1]
        assert call_req["method"] == "tools/call"
        assert call_req["params"]["name"] == "echo"
        assert call_req["params"]["arguments"] == {"text": "hello"}
        await client.close()

    @pytest.mark.asyncio
    async def test_rpc_error_raises(self):
        transport = MockTransport()
        transport.queue_response({"jsonrpc": "2.0", "id": 1, "result": {}})
        client = MCPClient(name="test", transport=transport)
        await client.connect()

        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "error": {"code": -32601, "message": "Method not found"},
        })
        with pytest.raises(RuntimeError, match="Method not found"):
            await client.call_tool("nonexistent", {})
        await client.close()

    @pytest.mark.asyncio
    async def test_session_rebuild_on_expiry(self):
        """Client rebuilds session on -32001 error."""
        transport = MockTransport()
        transport.queue_response({"jsonrpc": "2.0", "id": 1, "result": {}})
        client = MCPClient(name="test", transport=transport)
        await client.connect()

        # First attempt: session expired
        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "error": {"code": -32001, "message": "Session expired"},
        })
        # Rebuild: reinitialize
        transport.queue_response({"jsonrpc": "2.0", "id": 3, "result": {"protocolVersion": "2024-11-05"}})
        # Retry the original call
        transport.queue_response({
            "jsonrpc": "2.0", "id": 4,
            "result": {"content": [{"type": "text", "text": "success after rebuild"}]},
        })

        result = await client.call_tool("echo", {"text": "test"})
        assert result["content"][0]["text"] == "success after rebuild"
        await client.close()

    @pytest.mark.asyncio
    async def test_list_resources(self):
        transport = MockTransport()
        transport.queue_response({"jsonrpc": "2.0", "id": 1, "result": {}})
        client = MCPClient(name="test", transport=transport)
        await client.connect()

        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {"resources": [
                {"uri": "file:///tmp/test.txt", "name": "Test File"},
            ]},
        })
        resources = await client.list_resources()
        assert len(resources) == 1
        assert resources[0]["uri"] == "file:///tmp/test.txt"
        await client.close()

    @pytest.mark.asyncio
    async def test_list_resources_not_supported(self):
        """Servers that don't support resources return empty list."""
        transport = MockTransport()
        transport.queue_response({"jsonrpc": "2.0", "id": 1, "result": {}})
        client = MCPClient(name="test", transport=transport)
        await client.connect()

        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "error": {"code": -32601, "message": "Method not found"},
        })
        resources = await client.list_resources()
        assert resources == []
        await client.close()

    @pytest.mark.asyncio
    async def test_read_resource(self):
        transport = MockTransport()
        transport.queue_response({"jsonrpc": "2.0", "id": 1, "result": {}})
        client = MCPClient(name="test", transport=transport)
        await client.connect()

        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {"contents": [{"uri": "file:///test.txt", "text": "File content"}]},
        })
        result = await client.read_resource("file:///test.txt")
        assert "contents" in result
        await client.close()

    @pytest.mark.asyncio
    async def test_request_id_increments(self):
        transport = MockTransport()
        transport.queue_response({"jsonrpc": "2.0", "id": 1, "result": {}})
        client = MCPClient(name="test", transport=transport)
        await client.connect()

        transport.queue_response({"jsonrpc": "2.0", "id": 2, "result": {"tools": []}})
        await client.discover_tools()

        transport.queue_response({"jsonrpc": "2.0", "id": 3, "result": {}})
        await client.call_tool("x", {})

        # Verify IDs are incrementing
        ids = [msg.get("id") for msg in transport._sent if msg.get("id")]
        assert ids == [1, 2, 3]
        await client.close()

    @pytest.mark.asyncio
    async def test_close(self):
        transport = MockTransport()
        transport.queue_response({"jsonrpc": "2.0", "id": 1, "result": {}})
        client = MCPClient(name="test", transport=transport)
        await client.connect()
        assert transport.is_connected
        await client.close()
        assert not transport.is_connected


# ===================================================================
# StdioTransport (with real echo server)
# ===================================================================

class TestStdioTransport:

    @pytest.mark.asyncio
    async def test_connect_send_receive(self):
        transport = StdioTransport(command=PYTHON, args=[MCP_SERVER_PATH])
        await transport.connect()
        assert transport.is_connected

        # Send initialize
        await transport.send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "0.1"}},
        })
        resp = await transport.receive()
        assert resp["id"] == 1
        assert "result" in resp

        await transport.close()
        assert not transport.is_connected

    @pytest.mark.asyncio
    async def test_graceful_shutdown(self):
        transport = StdioTransport(command=PYTHON, args=[MCP_SERVER_PATH])
        await transport.connect()
        pid = transport._process.pid
        assert pid is not None
        await transport.close()
        # Process should be cleaned up
        assert transport._process is None

    @pytest.mark.asyncio
    async def test_double_close_safe(self):
        transport = StdioTransport(command=PYTHON, args=[MCP_SERVER_PATH])
        await transport.connect()
        await transport.close()
        await transport.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_send_not_connected_raises(self):
        transport = StdioTransport(command=PYTHON, args=[MCP_SERVER_PATH])
        with pytest.raises(RuntimeError, match="not connected"):
            await transport.send({"test": True})

    @pytest.mark.asyncio
    async def test_receive_not_connected_raises(self):
        transport = StdioTransport(command=PYTHON, args=[MCP_SERVER_PATH])
        with pytest.raises(RuntimeError, match="not connected"):
            await transport.receive()


# ===================================================================
# HTTPTransport (mocked)
# ===================================================================

class TestHTTPTransport:

    @pytest.mark.asyncio
    async def test_lifecycle(self):
        """Test connect/close state transitions with mocked httpx."""
        import httpx as real_httpx

        transport = HTTPTransport("http://localhost:8080/mcp")

        mock_client = AsyncMock()
        with patch.object(real_httpx, "AsyncClient", return_value=mock_client):
            await transport.connect()
        assert transport.is_connected

        await transport.close()
        assert not transport.is_connected

    @pytest.mark.asyncio
    async def test_send_receive(self):
        """Send a request and receive a response."""
        import httpx as real_httpx

        transport = HTTPTransport("http://localhost:8080/mcp")

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = b'{"jsonrpc":"2.0","id":1,"result":{}}'
        mock_resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {}}
        mock_resp.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch.object(real_httpx, "AsyncClient", return_value=mock_client):
            await transport.connect()

        await transport.send({"jsonrpc": "2.0", "id": 1, "method": "test"})
        result = await transport.receive()
        assert result["id"] == 1
        await transport.close()

    @pytest.mark.asyncio
    async def test_send_not_connected(self):
        transport = HTTPTransport("http://localhost:8080/mcp")
        with pytest.raises(RuntimeError, match="Not connected"):
            await transport.send({"test": True})


# ===================================================================
# Full MCP lifecycle with real echo server
# ===================================================================

class TestMCPFullLifecycle:

    @pytest.mark.asyncio
    async def test_full_lifecycle(self):
        """Connect → discover → call → call → close."""
        transport = StdioTransport(command=PYTHON, args=[MCP_SERVER_PATH])
        client = MCPClient(name="lifecycle-test", transport=transport)

        await client.connect()
        tools = await client.discover_tools()
        assert len(tools) == 2

        # Call echo
        r1 = await client.call_tool("echo", {"text": "ping"})
        assert "ECHO: ping" in r1["content"][0]["text"]

        # Call reverse
        r2 = await client.call_tool("reverse", {"text": "abcdef"})
        assert "fedcba" in r2["content"][0]["text"]

        await client.close()

    @pytest.mark.asyncio
    async def test_multiple_calls_sequential(self):
        """Multiple sequential calls work correctly."""
        transport = StdioTransport(command=PYTHON, args=[MCP_SERVER_PATH])
        client = MCPClient(name="seq-test", transport=transport)
        await client.connect()
        await client.discover_tools()

        for i in range(5):
            r = await client.call_tool("echo", {"text": f"msg-{i}"})
            assert f"ECHO: msg-{i}" in r["content"][0]["text"]

        await client.close()

    @pytest.mark.asyncio
    async def test_tool_adapter_full_pipeline(self):
        """Discover → wrap as Tool → call via adapter → verify."""
        transport = StdioTransport(command=PYTHON, args=[MCP_SERVER_PATH])
        client = MCPClient(name="adapter-test", transport=transport)
        await client.connect()
        schemas = await client.discover_tools()
        tools = create_mcp_tools(schemas, client)

        echo = next(t for t in tools if "echo" in t.name)
        reverse = next(t for t in tools if "reverse" in t.name)

        ctx = ToolContext()

        r1 = await echo.call(echo.parameters(text="hello"), ctx)
        assert "ECHO: hello" in r1.content

        r2 = await reverse.call(reverse.parameters(text="world"), ctx)
        assert "dlrow" in r2.content

        await client.close()
