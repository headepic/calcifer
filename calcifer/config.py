"""Configuration for Calcifer agent runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server connection."""

    name: str
    transport: str  # "stdio" | "sse"
    command: str | None = None  # stdio: command to run
    args: list[str] = field(default_factory=list)  # stdio: command args
    env: dict[str, str] = field(default_factory=dict)  # stdio: env vars
    url: str | None = None  # sse: server URL


@dataclass
class CalciferConfig:
    """Top-level configuration for the agent runner."""

    # LLM provider
    api_key: str = ""
    # base_url default is None (not a hardcoded URL). The Agent constructor
    # resolves it via os.environ["OPENAI_BASE_URL"] then falls back to the
    # canonical "https://api.openai.com/v1", and writes the resolved value
    # back here before LLMProvider is constructed.
    base_url: str | None = None
    model: str = "gpt-4o"
    max_tokens: int = 8192
    temperature: float = 0.0

    # Agent behavior
    max_turns: int = 100
    system_prompt: str = ""

    # Tool orchestration
    max_tool_concurrency: int = 10

    # Context management
    max_context_tokens: int = 128_000
    compact_threshold: float = 0.9  # trigger compaction at 90% of context

    # MCP servers
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)

    # Paths
    skills_dirs: list[str] = field(default_factory=list)
    memory_dir: str | None = None

    # Structured output (JSON schema)
    json_schema: dict[str, Any] | None = None

    # Task budget (API-level agentic turn budget, distinct from token budget)
    task_budget: int | None = None

    # Fallback model (used when primary is overloaded)
    fallback_model: str | None = None

    # Thinking config
    thinking_mode: str = "disabled"  # "disabled", "adaptive", "enabled"
    thinking_budget_tokens: int = 10_000

    # Extra kwargs passed to the LLM API
    extra_api_params: dict[str, Any] = field(default_factory=dict)
