"""P0 verification: tool system, message types, agent loop."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from calcifer import Agent, CalciferConfig, Message, ToolCall, ToolResult, Usage, tool


# -- Tool system tests --


def test_tool_decorator_creates_function_tool():
    @tool(name="add", description="Add two numbers")
    def add(a: int, b: int) -> str:
        return str(a + b)

    assert add.name == "add"
    assert add.description == "Add two numbers"
    assert add.is_concurrency_safe is False
    assert add.is_read_only is False


def test_tool_schema_generation():
    @tool(name="greet", description="Greet someone")
    def greet(name: str, excited: bool = False) -> str:
        return f"Hello {name}{'!' if excited else '.'}"

    schema = greet.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "greet"
    assert schema["function"]["description"] == "Greet someone"

    params = schema["function"]["parameters"]
    assert params["type"] == "object"
    assert "name" in params["properties"]
    assert "excited" in params["properties"]
    assert "name" in params["required"]


@pytest.mark.asyncio
async def test_tool_call_sync_function():
    @tool(name="add", description="Add two numbers")
    def add(a: int, b: int) -> str:
        return str(a + b)

    from calcifer.types.tools import ToolContext

    result = await add.call(add.validate_input({"a": 2, "b": 3}), ToolContext())
    assert result.content == "5"
    assert result.is_error is False


@pytest.mark.asyncio
async def test_tool_call_async_function():
    @tool(name="async_add", description="Async add")
    async def async_add(a: int, b: int) -> str:
        return str(a + b)

    from calcifer.types.tools import ToolContext

    result = await async_add.call(
        async_add.validate_input({"a": 10, "b": 20}), ToolContext()
    )
    assert result.content == "30"


@pytest.mark.asyncio
async def test_tool_call_error_handling():
    @tool(name="fail", description="Always fails")
    def fail() -> str:
        raise ValueError("boom")

    from calcifer.types.tools import ToolContext

    result = await fail.call(fail.validate_input({}), ToolContext())
    assert result.is_error is True
    assert "boom" in result.content


def test_tool_truncation():
    @tool(name="big", description="Big output", max_result_size=100)
    def big() -> str:
        return "x" * 200

    assert big.max_result_size == 100
    truncated = big.truncate_result("x" * 200)
    assert len(truncated) < 200
    assert "truncated" in truncated


# -- Message tests --


def test_message_to_openai_user():
    msg = Message(role="user", content="hello")
    d = msg.to_openai()
    assert d == {"role": "user", "content": "hello"}


def test_message_to_openai_assistant_with_tool_calls():
    msg = Message(
        role="assistant",
        content=None,
        tool_calls=[
            ToolCall(id="tc_1", function_name="add", arguments='{"a":1,"b":2}')
        ],
    )
    d = msg.to_openai()
    assert d["role"] == "assistant"
    assert len(d["tool_calls"]) == 1
    assert d["tool_calls"][0]["function"]["name"] == "add"


def test_message_to_openai_tool_result():
    msg = Message(role="tool", content="5", tool_call_id="tc_1")
    d = msg.to_openai()
    assert d == {"role": "tool", "content": "5", "tool_call_id": "tc_1"}


def test_usage_accumulation():
    u = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    u += Usage(prompt_tokens=20, completion_tokens=10, total_tokens=30)
    assert u.prompt_tokens == 30
    assert u.completion_tokens == 15
    assert u.total_tokens == 45


# -- Agent loop tests (mocked LLM) --


@pytest.mark.asyncio
async def test_agent_simple_text_response():
    """Agent returns immediately when LLM gives a text-only response."""
    config = CalciferConfig(api_key="test-key")

    agent = Agent(config=config)

    # Mock the provider to return a plain text response
    mock_response = (
        Message(role="assistant", content="Hello! I'm here to help."),
        Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )

    with patch.object(
        agent._provider, "chat_completion", new_callable=AsyncMock
    ) as mock_chat:
        mock_chat.return_value = mock_response
        result = await agent.run("Hello")

    assert result.final_text == "Hello! I'm here to help."
    assert result.turn_count == 1
    assert result.usage.total_tokens == 15
    mock_chat.assert_called_once()


@pytest.mark.asyncio
async def test_agent_tool_call_loop():
    """Agent executes tool calls and loops back to LLM."""

    @tool(name="add", description="Add two numbers")
    def add(a: int, b: int) -> str:
        return str(a + b)

    config = CalciferConfig(api_key="test-key")
    agent = Agent(config=config, tools=[add])

    # Turn 1: LLM requests tool call
    turn1_response = (
        Message(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="tc_1",
                    function_name="add",
                    arguments='{"a": 2, "b": 3}',
                )
            ],
        ),
        Usage(prompt_tokens=20, completion_tokens=10, total_tokens=30),
    )

    # Turn 2: LLM returns final text
    turn2_response = (
        Message(role="assistant", content="The answer is 5."),
        Usage(prompt_tokens=30, completion_tokens=8, total_tokens=38),
    )

    with patch.object(
        agent._provider, "chat_completion", new_callable=AsyncMock
    ) as mock_chat:
        mock_chat.side_effect = [turn1_response, turn2_response]
        result = await agent.run("What is 2 + 3?")

    assert result.final_text == "The answer is 5."
    assert result.turn_count == 2
    assert result.usage.total_tokens == 68
    assert mock_chat.call_count == 2

    # Verify conversation history includes tool result
    tool_msgs = [m for m in result.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content == "5"
    assert tool_msgs[0].tool_call_id == "tc_1"


@pytest.mark.asyncio
async def test_agent_unknown_tool():
    """Agent handles unknown tool gracefully."""
    config = CalciferConfig(api_key="test-key")
    agent = Agent(config=config)

    turn1_response = (
        Message(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="tc_1",
                    function_name="nonexistent",
                    arguments="{}",
                )
            ],
        ),
        Usage(),
    )
    turn2_response = (
        Message(role="assistant", content="Sorry, that tool doesn't exist."),
        Usage(),
    )

    with patch.object(
        agent._provider, "chat_completion", new_callable=AsyncMock
    ) as mock_chat:
        mock_chat.side_effect = [turn1_response, turn2_response]
        result = await agent.run("Use nonexistent tool")

    tool_msgs = [m for m in result.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "No such tool available" in tool_msgs[0].content


@pytest.mark.asyncio
async def test_agent_max_turns():
    """Agent stops after max_turns."""
    config = CalciferConfig(api_key="test-key", max_turns=2)

    @tool(name="noop", description="Do nothing")
    def noop() -> str:
        return "ok"

    agent = Agent(config=config, tools=[noop])

    # LLM always requests tool calls (never stops)
    always_tool = (
        Message(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(id="tc_1", function_name="noop", arguments="{}")
            ],
        ),
        Usage(),
    )

    with patch.object(
        agent._provider, "chat_completion", new_callable=AsyncMock
    ) as mock_chat:
        mock_chat.return_value = always_tool
        result = await agent.run("Loop forever")

    assert result.turn_count == 2


# ────────────────────────────────────────────────────────────────────
# run_sync wrapper (sdk-agent-run-sync)
# ────────────────────────────────────────────────────────────────────


def test_agent_run_sync_basic():
    """run_sync wraps async run and returns AgentResult without await."""
    import inspect
    assert not inspect.iscoroutinefunction(Agent.run_sync), (
        "run_sync should be sync, not a coroutine function"
    )

    agent = Agent(api_key="test-key")
    mock_response = (
        Message(role="assistant", content="Hello from sync!"),
        Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    with patch.object(
        agent._provider, "chat_completion", new_callable=AsyncMock
    ) as mock_chat:
        mock_chat.return_value = mock_response
        result = agent.run_sync("Hi")

    assert result.final_text == "Hello from sync!"
    assert result.turn_count == 1


@pytest.mark.asyncio
async def test_agent_run_sync_inside_loop_raises():
    """Calling run_sync from inside a running asyncio loop raises clearly."""
    agent = Agent(api_key="test-key")
    with pytest.raises(RuntimeError, match="cannot be called from inside"):
        agent.run_sync("Hi")
