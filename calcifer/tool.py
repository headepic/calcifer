"""Tool system: base class, decorator, schema conversion, and tool matching.

Mirrors Claude Code's Tool.ts + buildTool() pattern with full interface:
- Fail-closed defaults (isConcurrencySafe=False, isReadOnly=False)
- Deferred loading (shouldDefer + searchHint for ToolSearch)
- Permission checking (checkPermissions + preparePermissionMatcher)
- Input validation (validateInput)
- Result size limiting and disk persistence
- Auto-classifier input generation
"""

from __future__ import annotations

import inspect
import json
from abc import ABC, abstractmethod
from typing import Any, Callable, get_type_hints

from pydantic import BaseModel, create_model

from .types.tools import (
    ToolContext,
    ToolProgress,
    ToolResult,
    ValidationResult,
)


class Tool(ABC):
    """Base class for all tools.

    Mirrors Claude Code's Tool interface. buildTool() defaults are applied
    via class-level attributes (fail-closed where it matters).
    """

    name: str
    description: str
    parameters: type[BaseModel]

    # -- Aliases (for backward compat when tool is renamed) --
    aliases: list[str] = []

    # -- Behavior flags (fail-closed defaults like Claude Code's buildTool) --
    is_concurrency_safe: bool = False
    is_read_only: bool = False
    is_destructive: bool = False
    is_compactable: bool = False  # True = results can be aggressively cleared after use
    max_result_size: int = 30_000

    # -- Deferred loading (for ToolSearch) --
    should_defer: bool = False
    always_load: bool = False
    search_hint: str = ""  # 3-10 word capability phrase for keyword matching

    # -- MCP metadata --
    is_mcp: bool = False
    mcp_info: dict[str, str] | None = None  # {"server_name": ..., "tool_name": ...}

    # -- Strict mode (structured output enforcement) --
    strict: bool = False  # When True, API enforces strict adherence to schema

    # -- Interrupt behavior --
    def interrupt_behavior(self) -> str:
        """What happens when user interrupts while this tool runs.
        'cancel' = stop and discard, 'block' = finish before stopping.
        Read-only tools default to 'cancel'; mutating tools to 'block'.
        """
        return "cancel" if self.is_read_only else "block"

    def backfill_observable_input(self, input: dict[str, Any]) -> dict[str, Any]:
        """Normalize input before it's observed by hooks/SDK/telemetry.

        Mutate to add legacy/derived fields. Must be idempotent.
        The original API-bound input is never mutated.
        Override for tools that need input normalization.
        """
        return input

    @abstractmethod
    async def call(
        self,
        args: BaseModel,
        context: ToolContext,
        on_progress: Callable[[ToolProgress], None] | None = None,
    ) -> ToolResult:
        """Execute the tool with validated arguments."""
        ...

    def to_openai_schema(self) -> dict[str, Any]:
        """Convert to OpenAI function calling format.

        Pydantic model_json_schema() -> JSON Schema -> OpenAI function tool.
        Same role as Claude Code's zodToJsonSchema() -> toolToAPISchema().
        """
        schema = self.parameters.model_json_schema()
        schema.pop("title", None)
        schema.pop("$defs", None)
        func: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "parameters": schema,
        }
        if self.strict:
            func["strict"] = True
        return {"type": "function", "function": func}

    def validate_input(self, raw_args: dict[str, Any]) -> BaseModel:
        """Parse and validate raw arguments against the schema."""
        return self.parameters.model_validate(raw_args)

    async def check_input(
        self, args: dict[str, Any], context: ToolContext
    ) -> ValidationResult:
        """Validate input beyond schema (e.g., file exists, path safe).

        Override for tool-specific validation. Default: always valid.
        """
        return ValidationResult(valid=True)

    def is_enabled(self) -> bool:
        """Whether this tool is currently enabled. Default: True."""
        return True

    def get_path(self, args: dict[str, Any]) -> str | None:
        """Return the file path this tool operates on, if applicable."""
        return None

    def is_search_or_read(self, args: dict[str, Any]) -> dict[str, bool]:
        """Classify whether this is a search/read/list operation for UI collapsing."""
        return {"is_search": False, "is_read": False, "is_list": False}

    def to_auto_classifier_input(self, args: dict[str, Any]) -> str:
        """Compact representation for the auto-mode security classifier.

        Return '' to skip this tool in the classifier.
        Override for security-relevant tools (e.g., Bash returns the command).
        """
        return ""

    def user_facing_name(self, args: dict[str, Any] | None = None) -> str:
        """Human-readable name for UI display."""
        return self.name

    def get_activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        """Present-tense activity for spinner display. E.g. 'Reading src/foo.py'."""
        return None

    def truncate_result(self, content: str) -> str:
        """Truncate result if it exceeds max_result_size."""
        if len(content) <= self.max_result_size:
            return content
        half = self.max_result_size // 2
        return (
            content[:half]
            + f"\n\n... [truncated {len(content) - self.max_result_size} chars] ...\n\n"
            + content[-half:]
        )

    def matches_name(self, name: str) -> bool:
        """Check if this tool matches a given name (primary or alias)."""
        return self.name == name or name in self.aliases


# -- FunctionTool (from @tool decorator) --

class FunctionTool(Tool):
    """A tool backed by a plain function. Created by the @tool decorator."""

    def __init__(
        self,
        func: Callable[..., Any],
        *,
        name: str,
        description: str,
        parameters: type[BaseModel],
        is_concurrency_safe: bool = False,
        is_read_only: bool = False,
        is_destructive: bool = False,
        max_result_size: int = 30_000,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.is_concurrency_safe = is_concurrency_safe
        self.is_read_only = is_read_only
        self.is_destructive = is_destructive
        self.max_result_size = max_result_size
        self._func = func

    async def call(
        self,
        args: BaseModel,
        context: ToolContext,
        on_progress: Callable[[ToolProgress], None] | None = None,
    ) -> ToolResult:
        kwargs = args.model_dump()
        try:
            if inspect.iscoroutinefunction(self._func):
                result = await self._func(**kwargs)
            else:
                result = self._func(**kwargs)
            content = result if isinstance(result, str) else json.dumps(result)
            return ToolResult(content=self.truncate_result(content))
        except Exception as e:
            return ToolResult(content=str(e), is_error=True)


# -- Helper: build Pydantic model from function signature --

def _build_parameters_model(
    func: Callable[..., Any], name: str
) -> type[BaseModel]:
    """Build a Pydantic model from function signature."""
    hints = get_type_hints(func)
    hints.pop("return", None)

    sig = inspect.signature(func)
    fields: dict[str, Any] = {}

    for param_name, param in sig.parameters.items():
        annotation = hints.get(param_name, str)
        if param.default is inspect.Parameter.empty:
            fields[param_name] = (annotation, ...)
        else:
            fields[param_name] = (annotation, param.default)

    model = create_model(f"{name}_params", **fields)
    return model


# -- Decorator --

def tool(
    name: str | None = None,
    description: str = "",
    *,
    is_concurrency_safe: bool = False,
    is_read_only: bool = False,
    is_destructive: bool = False,
    max_result_size: int = 30_000,
) -> Callable[[Callable[..., Any]], FunctionTool]:
    """Decorator to create a Tool from a plain function.

    Usage:
        @tool(name="add", description="Add two numbers")
        def add(a: int, b: int) -> str:
            return str(a + b)
    """

    def decorator(func: Callable[..., Any]) -> FunctionTool:
        tool_name = name or func.__name__
        tool_desc = description or func.__doc__ or ""
        params_model = _build_parameters_model(func, tool_name)

        return FunctionTool(
            func,
            name=tool_name,
            description=tool_desc,
            parameters=params_model,
            is_concurrency_safe=is_concurrency_safe,
            is_read_only=is_read_only,
            is_destructive=is_destructive,
            max_result_size=max_result_size,
        )

    return decorator


# -- Tool lookup utilities (like Claude Code's findToolByName / toolMatchesName) --

def find_tool_by_name(tools: list[Tool], name: str) -> Tool | None:
    """Find a tool by name or alias."""
    for t in tools:
        if t.matches_name(name):
            return t
    return None
