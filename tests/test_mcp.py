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
