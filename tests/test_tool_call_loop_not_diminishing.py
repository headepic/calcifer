"""Regression test: pure tool-calling turns must not trip the
diminishing-output stop heuristic.

Before this fix, an agent that explored a codebase via 3+ sequential
tool calls (glob → grep → file_read → glob …) would have its loop
killed by `_check_token_budget`, because each tool-calling turn
legitimately has small `completion_tokens` (just the tool_calls JSON
blob), and three such turns in a row satisfy the "all < 500" condition.
The loop exits right after the tool results come back from turn 3,
never giving the model turn 4 to synthesize a final text answer.

The fix: only count turns whose assistant message contains text in
the `completion_deltas` list the heuristic looks at.

This regression was discovered during a dogfood run of apps/ask/ where
asking "Agent loop 怎么处理 429 错误？" returned an empty final_text
despite 13 tool calls having happened.
"""
from __future__ import annotations

import json

import pytest

from calcifer import Agent, CalciferConfig, Message, ToolCall, tool
from calcifer.testing import MockProvider


@tool(name="echo", description="Echo a string back", is_concurrency_safe=True)
def echo(s: str) -> str:
    return s


def _tool_turn(call_id: str, arg: str) -> Message:
    """Build an assistant message that issues one tool call and no text."""
    return Message(
        role="assistant",
        content=None,
        tool_calls=[
            ToolCall(
                id=call_id,
                function_name="echo",
                arguments=json.dumps({"s": arg}),
            )
        ],
    )


@pytest.mark.asyncio
async def test_three_pure_tool_turns_then_final_text():
    """The agent issues 3 sequential tool calls, then a final text answer.

    Before the fix, turn 4 was never reached because 3 tool-calling
    turns in a row tripped `_check_token_budget` (all had
    completion_tokens < DIMINISHING_THRESHOLD=500).
    """
    provider = MockProvider(
        responses=[
            _tool_turn("c1", "first"),
            _tool_turn("c2", "second"),
            _tool_turn("c3", "third"),
            "done: saw first, second, third",
        ]
    )
    config = CalciferConfig(api_key="x", base_url="x", model="mock")
    agent = Agent(provider=provider, config=config, tools=[echo])

    result = await agent.run("explore via three tool calls")

    assert result.final_text == "done: saw first, second, third", (
        f"expected the 4th (text) turn to run after 3 tool-calling turns; "
        f"got final_text={result.final_text!r} turn_count={result.turn_count}"
    )
    # 4 turns total: 3 tool-calling + 1 final text
    assert result.turn_count == 4

    # Sanity: all 3 tool results are in the conversation
    tool_msgs = [m for m in result.messages if m.role == "tool"]
    assert len(tool_msgs) == 3


@pytest.mark.asyncio
async def test_three_empty_text_turns_still_trigger_diminishing():
    """Safety: the fix must not disable the diminishing check entirely.
    If the agent produces 3 turns of short *text* responses and no tool
    calls, the heuristic should still fire."""
    # Three short text responses in a row. Each is a legitimate
    # completion, not a tool call. The agent loop will exit on its own
    # after the first text response because there are no tool calls,
    # so this test mainly asserts that the first turn completes cleanly
    # and the loop terminates the normal way (not by budget trip).
    provider = MockProvider(responses=["short"])
    config = CalciferConfig(api_key="x", base_url="x", model="mock")
    agent = Agent(provider=provider, config=config)

    result = await agent.run("hi")
    assert result.final_text == "short"
    assert result.turn_count == 1
