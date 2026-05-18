"""Unit tests for the reusable chatbot consumer app."""

from __future__ import annotations

import json

import pytest

from calcifer import Agent, CalciferConfig, tool
from calcifer.testing import MockProvider

from calcifer_chatbot import build_system_prompt as exported_build_system_prompt
from calcifer_chatbot.app import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_DEFAULT_MODEL,
    Chatbot,
    build_chatbot,
    build_system_prompt,
    resolve_provider_config,
    select_tools,
)


def _make_bot(responses: list[str]) -> tuple[Chatbot, MockProvider]:
    provider = MockProvider(responses=responses)
    agent = Agent(
        config=CalciferConfig(
            api_key="mock",
            base_url="mock",
            model="mock",
            system_prompt="You are a test chatbot.",
        ),
        provider=provider,
    )
    return Chatbot(agent=agent), provider


def test_chatbot_preserves_conversation_between_turns():
    bot, provider = _make_bot(["first answer", "second answer"])

    first = bot.ask_sync("hello")
    second = bot.ask_sync("what did I just say?")

    assert first.final_text == "first answer"
    assert second.final_text == "second answer"
    assert len(provider.calls) == 2
    second_call_messages = provider.calls[1]["messages"]
    assert [m.content for m in second_call_messages if m.role == "user"] == [
        "hello",
        "what did I just say?",
    ]
    assert any(m.role == "assistant" and m.content == "first answer" for m in second_call_messages)


@pytest.mark.asyncio
async def test_chatbot_stream_updates_conversation():
    bot, _provider = _make_bot(["streamed answer"])

    events = [event async for event in bot.stream("hi")]

    assert any(event.type == "text_delta" and event.text == "streamed answer" for event in events)
    assert bot.conversation[-1].role == "assistant"
    assert bot.conversation[-1].content == "streamed answer"


@pytest.mark.asyncio
async def test_chatbot_stream_complete_does_not_reuse_previous_turn_reply():
    @tool(name="lookup", description="Look up fresh information")
    def lookup(query: str) -> str:
        return f"fresh result for {query}"

    provider = MockProvider(
        [
            "previous Python answer",
            {"tool_calls": [{"name": "lookup", "arguments": {"query": "Shanghai weather"}}]},
            "",
        ]
    )
    agent = Agent(
        config=CalciferConfig(
            api_key="mock",
            base_url="mock",
            model="mock",
            system_prompt="You are a test chatbot.",
        ),
        tools=[lookup],
        provider=provider,
    )
    bot = Chatbot(agent=agent)

    first_events = [event async for event in bot.stream("Python latest stable?")]
    second_events = [event async for event in bot.stream("Shanghai weather this week?")]

    first_complete = [event for event in first_events if event.type == "run_complete"][-1]
    second_complete = [event for event in second_events if event.type == "run_complete"][-1]
    assert first_complete.result.final_text == "previous Python answer"
    assert second_complete.result.final_text == ""


def test_select_tools_default_chatbot_mode_uses_web_search_only():
    tools = select_tools()
    names = {tool.name for tool in tools}

    assert names == {"web_search"}


def test_select_tools_chatbot_mode_uses_web_search_only():
    tools = select_tools("chatbot")
    names = {tool.name for tool in tools}

    assert names == {"web_search"}


def test_select_tools_rejects_removed_web_mode():
    with pytest.raises(ValueError, match="Unknown tool mode"):
        select_tools("web")  # type: ignore[arg-type]


def test_select_tools_workspace_mode_excludes_mutating_tools():
    tools = select_tools("workspace")
    names = {tool.name for tool in tools}

    assert {"file_read", "glob", "grep", "web_search"} <= names
    assert "file_write" not in names
    assert "file_edit" not in names
    assert "bash" not in names


def test_select_tools_rejects_removed_readonly_mode():
    with pytest.raises(ValueError, match="Unknown tool mode"):
        select_tools("readonly")  # type: ignore[arg-type]


def test_build_chatbot_default_chatbot_tools_are_web_search_only(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")

    bot = build_chatbot(provider="deepseek")

    assert {tool.name for tool in bot.agent._tools} == {"web_search"}
    assert "web_search" in bot.agent._config.system_prompt
    assert "local workspace" not in bot.agent._config.system_prompt


def test_build_chatbot_workspace_tools_use_workspace_prompt(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")

    bot = build_chatbot(provider="deepseek", tools="workspace")

    assert {tool.name for tool in bot.agent._tools} == {"file_read", "glob", "grep", "web_search"}
    assert "local workspace" in bot.agent._config.system_prompt
    assert "cite file paths" in bot.agent._config.system_prompt


def test_all_mode_hides_mutating_tools_without_explicit_write_request():
    provider = MockProvider(responses=["faq answer"])
    agent = Agent(
        config=CalciferConfig(api_key="mock", base_url="mock", model="mock"),
        tools=select_tools("all"),
        provider=provider,
    )
    bot = Chatbot(agent=agent, tool_mode="all")

    bot.ask_sync("把上面的内容整理成 FAQ")

    tool_names = {tool["function"]["name"] for tool in provider.calls[0]["tools"]}
    assert "file_write" not in tool_names
    assert "file_edit" not in tool_names
    assert "bash" not in tool_names
    assert "web_search" in tool_names


def test_all_mode_exposes_mutating_tools_for_explicit_write_request():
    provider = MockProvider(responses=["saved"])
    agent = Agent(
        config=CalciferConfig(api_key="mock", base_url="mock", model="mock"),
        tools=select_tools("all"),
        provider=provider,
    )
    bot = Chatbot(agent=agent, tool_mode="all")

    bot.ask_sync("把 FAQ 保存为 python_314_faq.md")

    tool_names = {tool["function"]["name"] for tool in provider.calls[0]["tools"]}
    assert "file_write" in tool_names
    assert "file_edit" in tool_names
    assert "bash" in tool_names


def test_chatbot_limits_web_search_per_request_and_resets_next_turn():
    search_calls: list[str] = []

    @tool(name="web_search", description="Search the web")
    def web_search(query: str) -> str:
        search_calls.append(query)
        return f"result for {query}"

    provider = MockProvider(
        responses=[
            {"tool_calls": [{"name": "web_search", "arguments": {"query": "q1"}}]},
            {"tool_calls": [{"name": "web_search", "arguments": {"query": "q2"}}]},
            {"tool_calls": [{"name": "web_search", "arguments": {"query": "q3"}}]},
            "first answer",
            {"tool_calls": [{"name": "web_search", "arguments": {"query": "q4"}}]},
            "second answer",
        ]
    )
    agent = Agent(
        config=CalciferConfig(api_key="mock", base_url="mock", model="mock"),
        tools=[web_search],
        provider=provider,
    )
    bot = Chatbot(agent=agent, tool_mode="chatbot")

    first = bot.ask_sync("search too much")
    assert first.final_text == "first answer"
    assert search_calls == ["q1", "q2"]
    first_tool_results = [m.content for m in first.messages if m.role == "tool"]
    assert first_tool_results[-1] is not None
    limit_result = json.loads(first_tool_results[-1])
    assert limit_result["type"] == "web_search_limit_reached"
    assert limit_result["limit"] == 2
    assert "answer from the web search results already available" in limit_result["message"].lower()

    second = bot.ask_sync("new request may search again")
    assert second.final_text == "second answer"
    assert search_calls == ["q1", "q2", "q4"]


@pytest.mark.parametrize("tool_mode", ["workspace", "all"])
def test_workspace_and_all_limit_web_search_to_three_per_request(tool_mode):
    search_calls: list[str] = []

    @tool(name="web_search", description="Search the web")
    def web_search(query: str) -> str:
        search_calls.append(query)
        return f"result for {query}"

    provider = MockProvider(
        responses=[
            {"tool_calls": [{"name": "web_search", "arguments": {"query": "q1"}}]},
            {"tool_calls": [{"name": "web_search", "arguments": {"query": "q2"}}]},
            {"tool_calls": [{"name": "web_search", "arguments": {"query": "q3"}}]},
            {"tool_calls": [{"name": "web_search", "arguments": {"query": "q4"}}]},
            "answer",
        ]
    )
    agent = Agent(
        config=CalciferConfig(api_key="mock", base_url="mock", model="mock"),
        tools=[web_search],
        provider=provider,
    )
    bot = Chatbot(agent=agent, tool_mode=tool_mode)

    result = bot.ask_sync("search too much")

    assert result.final_text == "answer"
    assert search_calls == ["q1", "q2", "q3"]
    limit_result = json.loads([m.content for m in result.messages if m.role == "tool"][-1])
    assert limit_result["limit"] == 3


def test_build_system_prompt_uses_mode_specific_rules():
    none_prompt = build_system_prompt("none")
    chatbot_prompt = build_system_prompt("chatbot")
    workspace_prompt = build_system_prompt("workspace")
    all_prompt = build_system_prompt("all")

    assert "Do not claim to have searched the web" in none_prompt
    assert "web_search" in chatbot_prompt
    assert "local workspace" not in chatbot_prompt
    assert "local workspace" in workspace_prompt
    assert "cite file paths" in workspace_prompt
    assert "shell commands" in all_prompt
    assert "file changes" in all_prompt
    assert "Only use file_write, file_edit, or bash" in all_prompt
    assert "explicitly asks you to write, edit, save, create files, or run commands" in all_prompt


def test_build_system_prompt_constrains_simple_web_search_loops():
    chatbot_prompt = build_system_prompt("chatbot")
    workspace_prompt = build_system_prompt("workspace")
    all_prompt = build_system_prompt("all")

    for prompt in (chatbot_prompt, workspace_prompt, all_prompt):
        assert "For simple external factual queries" in prompt
        assert "when web_search is appropriate" in prompt
        assert "simple or current factual queries" not in prompt
        assert "start with one targeted web_search" in prompt
        assert "3-5 results" in prompt
        assert "only search again" in prompt


def test_build_system_prompt_appends_rules_to_custom_prompt():
    prompt = build_system_prompt("workspace", base_prompt="You are Calcifer.")

    assert prompt.startswith("You are Calcifer.")
    assert "local workspace" in prompt
    assert "cite file paths" in prompt


def test_build_system_prompt_rejects_removed_readonly_mode():
    with pytest.raises(ValueError, match="Unknown tool mode"):
        build_system_prompt("readonly")  # type: ignore[arg-type]


def test_package_exports_build_system_prompt():
    assert exported_build_system_prompt("chatbot") == build_system_prompt("chatbot")


def test_resolve_provider_config_uses_deepseek_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    resolved = resolve_provider_config("deepseek")

    assert resolved.api_key == "deepseek-test-key"
    assert resolved.base_url == DEEPSEEK_BASE_URL
    assert resolved.model == DEEPSEEK_DEFAULT_MODEL


def test_resolve_provider_config_allows_deepseek_overrides(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")

    resolved = resolve_provider_config(
        "deepseek",
        api_key="explicit-key",
        base_url="https://example.test/v1",
        model="custom-model",
    )

    assert resolved.api_key == "explicit-key"
    assert resolved.base_url == "https://example.test/v1"
    assert resolved.model == "custom-model"


def test_build_chatbot_accepts_deepseek_provider(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    bot = build_chatbot(provider="deepseek", tools="none")

    assert bot.agent._config.api_key == "deepseek-test-key"
    assert bot.agent._config.base_url == DEEPSEEK_BASE_URL
    assert bot.agent._config.model == DEEPSEEK_DEFAULT_MODEL
    assert bot.agent._tools == []
