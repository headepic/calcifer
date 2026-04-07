"""Tests for calcifer.testing — MockProvider + assertion helpers."""

from __future__ import annotations

import inspect

import pytest

from calcifer import Agent, AgentResult, CalciferConfig, Message, ToolCall, tool
from calcifer.types.message import Usage
from calcifer.testing import (
    MockProvider,
    assert_message_count,
    assert_tool_called,
)


# ────────────────────────────────────────────────────────────────────
# MockProvider — basic round-trip
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mock_provider_basic_text_response():
    """A string response is turned into an assistant Message and the
    Agent returns its final_text."""
    provider = MockProvider(["Hello from the mock!"])
    agent = Agent(config=CalciferConfig(api_key="test"), provider=provider)

    result = await agent.run("hi")

    assert result.final_text == "Hello from the mock!"
    assert result.turn_count == 1
    assert len(provider.calls) == 1
    assert provider.calls[0]["method"] == "chat_completion"


@pytest.mark.asyncio
async def test_mock_provider_multi_turn_tool_call():
    """A tool-call dict followed by a text response drives the agent
    loop: agent invokes the tool, loops back, then finalizes."""

    @tool(name="add", description="Add two numbers")
    def add(a: int, b: int) -> str:
        return str(a + b)

    provider = MockProvider(
        [
            {"tool_calls": [{"name": "add", "arguments": {"a": 1, "b": 2}}]},
            "The answer is 3.",
        ]
    )
    agent = Agent(
        config=CalciferConfig(api_key="test"),
        tools=[add],
        provider=provider,
    )

    result = await agent.run("1 plus 2?")

    assert result.final_text == "The answer is 3."
    assert result.turn_count == 2
    # tool message carrying the real add() output should be in history
    tool_msgs = [m for m in result.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content == "3"


# ────────────────────────────────────────────────────────────────────
# MockProvider — exhaustion policies
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mock_provider_exhausted_raises():
    """Default behavior: running past the last response raises."""
    provider = MockProvider(["only one"])
    agent = Agent(config=CalciferConfig(api_key="test"), provider=provider)

    # First run consumes the one response.
    result = await agent.run("hi")
    assert result.final_text == "only one"

    # Second run has nothing left.
    with pytest.raises(RuntimeError, match="MockProvider exhausted"):
        await agent.run("hi again")


@pytest.mark.asyncio
async def test_mock_provider_exhausted_repeats():
    """exhausted='repeat' keeps returning the last response forever."""
    provider = MockProvider(["stuck"], exhausted="repeat")
    agent = Agent(config=CalciferConfig(api_key="test"), provider=provider)

    for _ in range(3):
        result = await agent.run("ping")
        assert result.final_text == "stuck"


# ────────────────────────────────────────────────────────────────────
# assert_tool_called
# ────────────────────────────────────────────────────────────────────


def _fake_result_with_calls(calls: list[tuple[str, str]]) -> AgentResult:
    """Build a minimal AgentResult whose messages contain the given
    (tool_name, arguments_json) tool calls on a single assistant
    message."""
    tool_calls = [
        ToolCall(id=f"tc_{i}", function_name=name, arguments=args)
        for i, (name, args) in enumerate(calls)
    ]
    msg = Message(role="assistant", content=None, tool_calls=tool_calls)
    return AgentResult(messages=[msg], final_text="", usage=Usage(), turn_count=1)


def test_assert_tool_called_passes():
    result = _fake_result_with_calls([("bash", '{"cmd":"ls"}')])
    assert_tool_called(result, "bash")  # no raise


def test_assert_tool_called_fails_with_useful_message():
    result = _fake_result_with_calls(
        [("add", '{"a":1,"b":2}'), ("read", '{"path":"/tmp/x"}')]
    )
    with pytest.raises(AssertionError) as exc_info:
        assert_tool_called(result, "bash")

    msg = str(exc_info.value)
    # The error must list the tools that WERE called so the developer
    # can fix their test without re-running.
    assert "bash" in msg
    assert "add" in msg
    assert "read" in msg


def test_assert_tool_called_args_contains():
    result = _fake_result_with_calls(
        [("add", '{"a":1,"b":2}'), ("add", '{"a":3,"b":4}')]
    )
    # Subset match — first call matches.
    assert_tool_called(result, "add", args_contains={"a": 1})
    # Second call matches.
    assert_tool_called(result, "add", args_contains={"a": 3, "b": 4})

    with pytest.raises(AssertionError, match=r"no call had args containing"):
        assert_tool_called(result, "add", args_contains={"a": 99})


# ────────────────────────────────────────────────────────────────────
# assert_message_count
# ────────────────────────────────────────────────────────────────────


def test_assert_message_count_happy():
    result = AgentResult(
        messages=[
            Message(role="user", content="hi"),
            Message(role="assistant", content="hello"),
            Message(role="user", content="again"),
        ],
        final_text="hello",
        usage=Usage(),
        turn_count=1,
    )
    assert_message_count(result, count=3)
    assert_message_count(result, count=2, role="user")
    assert_message_count(result, count=1, role="assistant")


def test_assert_message_count_fail():
    result = AgentResult(
        messages=[Message(role="user", content="hi")],
        final_text="",
        usage=Usage(),
        turn_count=0,
    )
    with pytest.raises(AssertionError, match="expected 5 messages"):
        assert_message_count(result, count=5)


# ────────────────────────────────────────────────────────────────────
# Agent(provider=) injection seam
# ────────────────────────────────────────────────────────────────────


def test_agent_accepts_provider_injection():
    """Agent.__init__ must accept a `provider=` kwarg and route
    through the injected object rather than building an LLMProvider."""
    sig = inspect.signature(Agent.__init__)
    assert "provider" in sig.parameters, (
        f"Agent.__init__ missing provider kwarg: {list(sig.parameters.keys())}"
    )

    provider = MockProvider(["injected!"])
    agent = Agent(config=CalciferConfig(api_key="test"), provider=provider)

    # The injected provider is the one the agent uses.
    assert agent._provider is provider
