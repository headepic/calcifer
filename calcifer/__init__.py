"""Calcifer: A Python agent runner with tool calling, MCP, skills, and memory."""

from .agent import Agent, AgentResult
from .config import CalciferConfig, MCPServerConfig
from .coordinator import Coordinator, CoordinatorConfig
from .services.compact import ContextManager
from .services.api.provider import LLMProvider, LLMProviderError
from .services.hooks import HookManager, HookConfig, HookEvent
from .services.tools import run_tools
from .types.message import APIErrorType, Message, StreamEvent, ToolCall, Usage
from .types.tools import (
    ToolContext, ToolResult, ValidationResult,
)
from .utils.cost_tracker import CostTracker
from .utils.settings import load_settings
from .tool import FunctionTool, Tool, tool, find_tool_by_name
from .tool_registry import (
    get_all_builtin_tools,
    get_tools,
    assemble_tool_pool,
)
from .telemetry.metrics import MetricsManager

__all__ = [
    # Core
    "Agent",
    "AgentResult",
    "CalciferConfig",
    "ContextManager",
    "Coordinator",
    "CoordinatorConfig",
    "FunctionTool",
    "MCPServerConfig",
    "Message",
    "StreamEvent",
    "Tool",
    "ToolCall",
    "ToolContext",
    "ToolResult",
    "Usage",
    "ValidationResult",
    # Provider
    "LLMProvider",
    "LLMProviderError",
    "APIErrorType",
    # Hooks
    "HookManager",
    "HookConfig",
    "HookEvent",
    # Observability
    "CostTracker",
    "MetricsManager",
    # Factories
    "find_tool_by_name",
    "load_settings",
    "run_tools",
    "tool",
    # Tool registry
    "get_all_builtin_tools",
    "get_tools",
    "assemble_tool_pool",
]
