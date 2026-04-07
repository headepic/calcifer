"""Unit tests for the `ask` consumer using calcifer.testing.MockProvider.

These tests run without a real LLM — they:

1. Build a MockProvider that returns canned (tool call | final text) sequences
2. Inject it into an Agent via `Agent(provider=...)`
3. Call `ask.app.ask(question, agent=agent)`
4. Assert on final text and tool call behavior

This is the intended testing pattern for any calcifer SDK consumer.
"""
from __future__ import annotations

import json

import pytest

from calcifer import Agent, CalciferConfig, Message, ToolCall, tool
from calcifer.testing import MockProvider, assert_message_count, assert_tool_called

from ask.app import SYSTEM_PROMPT, ask, git_log


def _make_agent(responses: list) -> Agent:
    """Build an Agent with a MockProvider injected, no real HTTP."""
    provider = MockProvider(responses=responses)
    config = CalciferConfig(
        api_key="mock",
        base_url="mock",
        model="mock",
        system_prompt=SYSTEM_PROMPT,
    )
    # Give it the same git_log custom tool the real app uses, plus a few
    # trivial builtins so the schema list is realistic.
    return Agent(provider=provider, config=config, tools=[git_log])


# -- Core behavior ---------------------------------------------------------


def test_ask_returns_canned_text_when_model_answers_directly():
    """If the model replies with pure text (no tool calls), ask() just
    passes that through."""
    agent = _make_agent(responses=["This codebase handles 429 errors via exponential backoff."])
    answer = ask("how are 429 errors handled?", agent=agent)
    assert "429" in answer
    assert "backoff" in answer


def test_ask_runs_tool_then_final_answer():
    """Two-turn conversation: model requests git_log, then summarizes it."""
    tool_request = Message(
        role="assistant",
        content=None,
        tool_calls=[
            ToolCall(
                id="c1",
                function_name="git_log",
                arguments=json.dumps({"path": ".", "limit": 3}),
            )
        ],
    )
    final_answer = "The last few commits were: refactor, test, and docs."
    agent = _make_agent(responses=[tool_request, final_answer])

    answer = ask("summarize recent commits", agent=agent)

    assert "refactor" in answer
    assert "docs" in answer


def test_ask_tool_call_is_observable_via_assert_tool_called():
    """Demonstrates the `assert_tool_called` helper from calcifer.testing."""
    tool_request = Message(
        role="assistant",
        content=None,
        tool_calls=[
            ToolCall(
                id="c1",
                function_name="git_log",
                arguments=json.dumps({"path": "apps/", "limit": 5}),
            )
        ],
    )
    agent = _make_agent(responses=[tool_request, "Looked at apps/"])
    result = agent.run_sync("what's in apps/?")

    assert_tool_called(result, "git_log", args_contains={"path": "apps/"})
    assert result.final_text == "Looked at apps/"


def test_ask_message_count_helper():
    """Sanity-check the assert_message_count helper across assistant turns."""
    agent = _make_agent(responses=["short answer"])
    result = agent.run_sync("hi")
    # One assistant message (the final text)
    assert_message_count(result, count=1, role="assistant")


# -- Custom tool is discoverable by name -----------------------------------


def test_git_log_tool_is_registered_under_expected_name():
    """Sanity-check the @tool decorator wired our custom function up with
    the right name and schema. This catches decorator-time regressions."""
    assert git_log.name == "git_log"
    assert "git" in git_log.description.lower() or "commit" in git_log.description.lower()
    # Should expose an OpenAI-compatible schema
    schema = git_log.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "git_log"
    params = schema["function"]["parameters"]["properties"]
    assert "path" in params
    assert "limit" in params
