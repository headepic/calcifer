"""Reusable chatbot backend built on Calcifer.

The reusable part is the `Chatbot` class: it owns conversation state and
delegates all model/tool behavior to `calcifer.Agent`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal

from calcifer import Agent, AgentResult, CalciferConfig, Message, StreamEvent
from calcifer.tool import Tool
from calcifer.tool_registry import get_all_builtin_tools


DEFAULT_SYSTEM_PROMPT = """You are a concise, helpful chatbot powered by Calcifer.
Use tools only when they materially improve the answer. Cite web sources when
you use web search. If workspace tools are enabled and you use local files, cite
the relevant paths in your response."""

ToolMode = Literal["none", "chatbot", "web", "workspace", "readonly", "all"]
ProviderMode = Literal["deepseek", "openai"]
WEB_TOOL_NAMES = {"web_search"}
WORKSPACE_TOOL_NAMES = {"file_read", "glob", "grep", "web_search"}
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"


@dataclass(frozen=True)
class ProviderConfig:
    """Resolved OpenAI-compatible provider settings."""

    api_key: str
    base_url: str
    model: str


def resolve_provider_config(
    provider: ProviderMode = "deepseek",
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> ProviderConfig:
    """Resolve provider settings from explicit args and environment.

    DeepSeek is OpenAI-compatible, so only the API key, base URL, and default
    model differ from the OpenAI preset.
    """
    if provider == "deepseek":
        resolved_api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not resolved_api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set")
        return ProviderConfig(
            api_key=resolved_api_key,
            base_url=base_url or os.environ.get("DEEPSEEK_BASE_URL", DEEPSEEK_BASE_URL),
            model=model or os.environ.get("DEEPSEEK_MODEL", DEEPSEEK_DEFAULT_MODEL),
        )

    if provider == "openai":
        resolved_api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not resolved_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        return ProviderConfig(
            api_key=resolved_api_key,
            base_url=base_url or os.environ.get("OPENAI_BASE_URL", OPENAI_DEFAULT_BASE_URL),
            model=model or os.environ.get("OPENAI_MODEL", OPENAI_DEFAULT_MODEL),
        )

    raise ValueError(f"Unknown provider: {provider}")


def select_tools(mode: ToolMode = "chatbot") -> list[Tool]:
    """Return the built-in tool set for a chatbot mode."""
    if mode == "none":
        return []
    tools = get_all_builtin_tools()
    if mode in {"chatbot", "web"}:
        return [tool for tool in tools if tool.name in WEB_TOOL_NAMES]
    if mode in {"workspace", "readonly"}:
        return [tool for tool in tools if tool.name in WORKSPACE_TOOL_NAMES]
    if mode == "all":
        return tools
    raise ValueError(f"Unknown tool mode: {mode}")


@dataclass
class Chatbot:
    """Stateful chatbot session around a Calcifer Agent."""

    agent: Agent
    conversation: list[Message] = field(default_factory=list)

    async def ask(self, prompt: str) -> AgentResult:
        """Run one non-streaming chatbot turn and preserve history."""
        result = await self.agent.run(
            prompt,
            messages=self.conversation if self.conversation else None,
        )
        self.conversation = result.messages
        return result

    def ask_sync(self, prompt: str) -> AgentResult:
        """Synchronous wrapper for scripts and tests."""
        result = self.agent.run_sync(
            prompt,
            messages=self.conversation if self.conversation else None,
        )
        self.conversation = result.messages
        return result

    async def stream(self, prompt: str) -> AsyncIterator[StreamEvent]:
        """Stream one chatbot turn and preserve history on completion."""
        async for event in self.agent.run_stream(
            prompt,
            messages=self.conversation if self.conversation else None,
        ):
            if event.type == "run_complete" and event.result:
                self.conversation = event.result.messages
            yield event

    def reset(self) -> None:
        """Clear conversation history for this chatbot session."""
        self.conversation = []


def build_chatbot(
    *,
    provider: ProviderMode = "deepseek",
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    tools: ToolMode = "chatbot",
) -> Chatbot:
    """Build a Chatbot from environment-compatible configuration."""
    provider_config = resolve_provider_config(
        provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )

    config = CalciferConfig(
        api_key=provider_config.api_key,
        base_url=provider_config.base_url,
        model=provider_config.model,
        system_prompt=system_prompt,
    )
    agent = Agent(config=config, tools=select_tools(tools))
    return Chatbot(agent=agent)
