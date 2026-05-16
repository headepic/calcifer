"""Unit tests for the reusable chatbot consumer app."""

from __future__ import annotations

import pytest

from calcifer import Agent, CalciferConfig
from calcifer.testing import MockProvider

from calcifer_chatbot.app import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_DEFAULT_MODEL,
    Chatbot,
    build_chatbot,
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


def test_select_tools_readonly_mode_excludes_mutating_tools():
    tools = select_tools("readonly")
    names = {tool.name for tool in tools}

    assert {"file_read", "glob", "grep", "web_search"} <= names
    assert "file_write" not in names
    assert "file_edit" not in names
    assert "bash" not in names


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
