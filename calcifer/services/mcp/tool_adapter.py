"""MCP Tool Adapter: wraps MCP tools as internal Tool instances.

Like Claude Code's MCPTool — passes JSON Schema directly through
(no Zod/Pydantic conversion), bridges call() to MCP tools/call.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, create_model

from ...tool import Tool
from ...types.tools import ToolContext, ToolResult
from .client import MCPClient, MCPToolSchema


def _build_pydantic_model(
    name: str, json_schema: dict[str, Any]
) -> type[BaseModel]:
    """Build a Pydantic model from JSON Schema.

    Creates a permissive model that accepts any fields defined in the schema.
    This avoids the complexity of full JSON Schema → Pydantic conversion.
    """
    properties = json_schema.get("properties", {})
    required = set(json_schema.get("required", []))

    fields: dict[str, Any] = {}
    for field_name, field_schema in properties.items():
        field_type = _json_type_to_python(field_schema.get("type", "string"))
        if field_name in required:
            fields[field_name] = (field_type, ...)
        else:
            fields[field_name] = (field_type, field_schema.get("default"))

    if not fields:
        # Empty schema — accept no params
        return create_model(name)

    return create_model(name, **fields)


def _json_type_to_python(json_type: str | list[str]) -> type:
    """Map JSON Schema type to Python type."""
    if isinstance(json_type, list):
        return Any  # type: ignore[return-value]
    mapping: dict[str, type] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    return mapping.get(json_type, Any)  # type: ignore[return-value]


class MCPToolAdapter(Tool):
    """Wraps an MCP tool as an internal Tool.

    Schema is passed through directly (like Claude Code's MCPTool).
    call() bridges to MCP tools/call.
    """

    def __init__(self, schema: MCPToolSchema, client: MCPClient):
        self.name = f"mcp__{schema.server_name}__{schema.name}"
        self.description = schema.description
        self.parameters = _build_pydantic_model(
            f"{self.name}_params", schema.input_schema
        )
        self._mcp_tool_name = schema.name
        self._client = client
        self._raw_schema = schema.input_schema

        # Apply annotations if available (like Claude Code's annotations → behavior flags)
        annotations = schema.annotations
        self.is_read_only = annotations.get("readOnlyHint", True)
        self.is_concurrency_safe = annotations.get("readOnlyHint", True)
        self.is_destructive = annotations.get("destructiveHint", False)
        self.max_result_size = 100_000
        self.is_mcp = True
        self.mcp_info = {"server_name": schema.server_name, "tool_name": schema.name}

        # Search hint from _meta
        meta = annotations.get("_meta", {})
        if isinstance(meta, dict):
            self.search_hint = meta.get("anthropic/searchHint", "")

    def to_openai_schema(self) -> dict[str, Any]:
        """Pass through the MCP JSON Schema directly."""
        schema = dict(self._raw_schema)
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }

    # Max content size from MCP results (prevents context blowup)
    MCP_CONTENT_MAX_CHARS = 200_000

    async def call(self, args: BaseModel, context: ToolContext, **kwargs) -> ToolResult:
        try:
            result = await self._client.call_tool(
                self._mcp_tool_name, args.model_dump()
            )
            # MCP result can be {"content": [{"type": "text", "text": "..."}]}
            if isinstance(result, dict):
                content_list = result.get("content", [])
                if isinstance(content_list, list):
                    texts = [
                        c.get("text", str(c))
                        for c in content_list
                        if isinstance(c, dict)
                    ]
                    content = "\n".join(texts) if texts else json.dumps(result)
                else:
                    content = json.dumps(result)
            else:
                content = str(result)

            # Limit content size to prevent context blowup
            if len(content) > self.MCP_CONTENT_MAX_CHARS:
                half = self.MCP_CONTENT_MAX_CHARS // 2
                content = (
                    content[:half]
                    + f"\n\n... [MCP result truncated: {len(content) - self.MCP_CONTENT_MAX_CHARS:,} chars omitted] ...\n\n"
                    + content[-half:]
                )

            return ToolResult(content=content)
        except Exception as e:
            return ToolResult(content=f"MCP tool error: {e}", is_error=True)


def create_mcp_tools(
    schemas: list[MCPToolSchema], client: MCPClient
) -> list[MCPToolAdapter]:
    """Create Tool instances for all discovered MCP tools."""
    return [MCPToolAdapter(schema, client) for schema in schemas]
