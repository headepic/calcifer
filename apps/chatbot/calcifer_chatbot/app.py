"""Reusable chatbot backend built on Calcifer.

The reusable part is the `Chatbot` class: it owns conversation state and
delegates all model/tool behavior to `calcifer.Agent`.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Iterator, Literal

from calcifer import Agent, AgentResult, CalciferConfig, Message, StreamEvent
from calcifer.tool import Tool
from calcifer.tool_registry import get_all_builtin_tools


DEFAULT_SYSTEM_PROMPT = "You are a concise, helpful chatbot powered by Calcifer."

ToolMode = Literal["none", "chatbot", "workspace", "all"]
ProviderMode = Literal["deepseek", "openai"]
WEB_TOOL_NAMES = {"web_search"}
WORKSPACE_TOOL_NAMES = {"file_read", "glob", "grep", "web_search"}
MUTATING_TOOL_NAMES = {"bash", "file_write", "file_edit"}
MUTATION_INTENT_MARKERS = (
    "write",
    "edit",
    "save",
    "create file",
    "create a file",
    "modify",
    "update file",
    "run command",
    "execute",
    "写",
    "写入",
    "保存",
    "创建",
    "新建",
    "修改",
    "编辑",
    "执行",
    "运行",
)
WEB_SEARCH_LOOP_RULE = (
    "For simple external factual queries, when web_search is appropriate, start "
    "with one targeted web_search using 3-5 results; only search again when the "
    "first results are insufficient, conflict with each other, or the user asks "
    "for deeper research."
)
MODE_PROMPT_RULES: dict[str, str] = {
    "none": (
        "Mode: none. Answer from the conversation and your general knowledge only. "
        "Do not claim to have searched the web, inspected files, or used tools."
    ),
    "chatbot": (
        "Mode: chatbot. Use web_search only when fresh, external, or source-backed "
        "information materially improves the answer. Cite web sources when you use "
        f"web_search. {WEB_SEARCH_LOOP_RULE} Keep ordinary conversation concise and natural."
    ),
    "workspace": (
        "Mode: workspace. You may use web_search plus read-only local workspace "
        "tools to inspect project files. Use local workspace context when the user "
        "asks about this repository, and cite file paths for claims based on files. "
        f"Cite web sources for claims based on web_search. {WEB_SEARCH_LOOP_RULE}"
    ),
    "all": (
        "Mode: all. You may use all configured tools, including shell commands and "
        "file changes. Only use file_write, file_edit, or bash when the user "
        "explicitly asks you to write, edit, save, create files, or run commands. "
        "For requests to summarize, organize, draft, or answer in chat, return the "
        "content in the chat without modifying files. Summarize commands or file "
        "changes you made, and cite file paths or web sources for claims that "
        f"depend on tool results. {WEB_SEARCH_LOOP_RULE}"
    ),
}
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


def build_system_prompt(mode: ToolMode = "chatbot", *, base_prompt: str | None = None) -> str:
    """Build the system prompt from the selected chatbot mode."""
    try:
        mode_rules = MODE_PROMPT_RULES[mode]
    except KeyError as exc:
        raise ValueError(f"Unknown tool mode: {mode}") from exc
    base = (base_prompt or DEFAULT_SYSTEM_PROMPT).strip()
    return f"{base}\n\n{mode_rules}"


def select_tools(mode: ToolMode = "chatbot") -> list[Tool]:
    """Return the built-in tool set for a chatbot mode."""
    if mode == "none":
        return []
    tools = get_all_builtin_tools()
    if mode == "chatbot":
        return [tool for tool in tools if tool.name in WEB_TOOL_NAMES]
    if mode == "workspace":
        return [tool for tool in tools if tool.name in WORKSPACE_TOOL_NAMES]
    if mode == "all":
        return tools
    raise ValueError(f"Unknown tool mode: {mode}")


def has_mutation_intent(prompt: str) -> bool:
    """Return whether a user explicitly asks to mutate local state."""
    normalized = prompt.lower()
    return any(marker in normalized for marker in MUTATION_INTENT_MARKERS)


def infer_tool_mode(tools: list[Tool]) -> ToolMode:
    """Infer a known chatbot mode from a concrete tool list."""
    names = {tool.name for tool in tools}
    if not names:
        return "none"
    if names == {tool.name for tool in select_tools("chatbot")}:
        return "chatbot"
    if names == {tool.name for tool in select_tools("workspace")}:
        return "workspace"
    if names == {tool.name for tool in select_tools("all")}:
        return "all"
    return "chatbot"


@dataclass
class Chatbot:
    """Stateful chatbot session around a Calcifer Agent."""

    agent: Agent
    tool_mode: ToolMode | None = None
    conversation: list[Message] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.tool_mode is None:
            self.tool_mode = infer_tool_mode(self.agent._tools)

    @contextmanager
    def _request_tools(self, prompt: str) -> Iterator[None]:
        """Temporarily hide mutating tools unless all-mode intent is explicit."""
        if self.tool_mode != "all" or has_mutation_intent(prompt):
            yield
            return

        original_tools = self.agent._tools
        original_by_name = self.agent._tools_by_name
        filtered_tools = [tool for tool in original_tools if tool.name not in MUTATING_TOOL_NAMES]
        self.agent._tools = filtered_tools
        self.agent._tools_by_name = {tool.name: tool for tool in filtered_tools}
        try:
            yield
        finally:
            self.agent._tools = original_tools
            self.agent._tools_by_name = original_by_name

    async def ask(self, prompt: str) -> AgentResult:
        """Run one non-streaming chatbot turn and preserve history."""
        with self._request_tools(prompt):
            result = await self.agent.run(
                prompt,
                messages=self.conversation if self.conversation else None,
            )
        self.conversation = result.messages
        return result

    def ask_sync(self, prompt: str) -> AgentResult:
        """Synchronous wrapper for scripts and tests."""
        with self._request_tools(prompt):
            result = self.agent.run_sync(
                prompt,
                messages=self.conversation if self.conversation else None,
            )
        self.conversation = result.messages
        return result

    async def stream(self, prompt: str) -> AsyncIterator[StreamEvent]:
        """Stream one chatbot turn and preserve history on completion."""
        with self._request_tools(prompt):
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
    system_prompt: str | None = None,
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
        system_prompt=build_system_prompt(tools, base_prompt=system_prompt),
    )
    agent = Agent(config=config, tools=select_tools(tools))
    return Chatbot(agent=agent, tool_mode=tools)
