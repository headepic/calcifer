"""Regression tests for final_text extraction across resumed conversations."""

from __future__ import annotations

import json

import pytest

from calcifer import Agent, CalciferConfig, Message, ToolCall, tool
from calcifer.testing import MockProvider


@tool(name="lookup", description="Look up a value")
def lookup(query: str) -> str:
    return f"fresh result for {query}"


def _tool_only_turn(query: str, *, call_id: str = "tc_lookup") -> Message:
    return Message(
        role="assistant",
        content=None,
        tool_calls=[
            ToolCall(
                id=call_id,
                function_name="lookup",
                arguments=json.dumps({"query": query}),
            )
        ],
    )


@pytest.mark.asyncio
async def test_run_final_text_does_not_reuse_historical_assistant_content():
    provider = MockProvider(
        [
            "prior answer",
            _tool_only_turn("current non-streaming query"),
        ]
    )
    agent = Agent(
        config=CalciferConfig(api_key="mock", base_url="mock", model="mock", max_turns=1),
        provider=provider,
        tools=[lookup],
    )

    prior = await agent.run("first request")
    current = await agent.run("second request", messages=prior.messages)

    assert prior.final_text == "prior answer"
    assert current.final_text == ""
    assert current.messages[-2].role == "assistant"
    assert current.messages[-2].content is None
    assert current.messages[-2].tool_calls


@pytest.mark.asyncio
async def test_run_stream_final_text_does_not_reuse_historical_assistant_content():
    provider = MockProvider(
        [
            "prior answer",
            _tool_only_turn("current streaming query"),
        ]
    )
    agent = Agent(
        config=CalciferConfig(api_key="mock", base_url="mock", model="mock", max_turns=1),
        provider=provider,
        tools=[lookup],
    )

    prior_events = [event async for event in agent.run_stream("first request")]
    prior = [event.result for event in prior_events if event.type == "run_complete"][-1]
    current_events = [
        event async for event in agent.run_stream("second request", messages=prior.messages)
    ]
    current = [event.result for event in current_events if event.type == "run_complete"][-1]

    assert prior.final_text == "prior answer"
    assert current.final_text == ""
    assert current.messages[-2].role == "assistant"
    assert current.messages[-2].content is None
    assert current.messages[-2].tool_calls
