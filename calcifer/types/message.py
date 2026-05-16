"""Message types for the OpenAI-compatible chat completion protocol.

Mirrors Claude Code's types/message.ts — full message taxonomy with
UUID tracking, progress messages, attachments, compact boundaries, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


# -- Tool Call --

@dataclass
class ToolCall:
    """A tool call requested by the assistant."""

    id: str
    function_name: str
    arguments: str  # JSON string

    def to_openai(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.function_name,
                "arguments": self.arguments,
            },
        }


# -- Message types --

class MessageType(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    """A single message in the conversation.

    Extends basic OpenAI format with:
    - uuid: unique ID for deduplication and tracking
    - tool_use_result: human-readable summary of tool result
    - source_tool_assistant_uuid: links tool result back to assistant message
    - is_meta: synthetic/internal messages not shown to user
    """

    role: str
    content: str | None = None

    # Assistant messages may contain tool calls
    tool_calls: list[ToolCall] = field(default_factory=list)

    # Tool result messages reference the tool_call they respond to
    tool_call_id: str | None = None

    # Unique message ID for tracking
    uuid: str = field(default_factory=lambda: uuid4().hex)

    # Tool result metadata
    tool_use_result: str | None = None
    source_tool_assistant_uuid: str | None = None

    # Flags
    is_meta: bool = False  # Synthetic messages (caveats, task notifications)

    # Optional metadata (not sent to API)
    metadata: dict[str, Any] = field(default_factory=dict)

    # OpenAI-compatible reasoning payloads, e.g. DeepSeek thinking-mode turns.
    reasoning_content: str | None = None

    def to_openai(self) -> dict[str, Any]:
        """Convert to OpenAI API message format."""
        msg: dict[str, Any] = {"role": self.role}

        if self.content is not None:
            msg["content"] = self.content

        if self.tool_calls:
            msg["tool_calls"] = [tc.to_openai() for tc in self.tool_calls]

        if self.tool_call_id is not None:
            msg["tool_call_id"] = self.tool_call_id

        if self.reasoning_content is not None:
            msg["reasoning_content"] = self.reasoning_content

        return msg


# -- Specialized message constructors --

def create_user_message(
    content: str | list[dict[str, Any]],
    *,
    tool_use_result: str | None = None,
    source_tool_assistant_uuid: str | None = None,
    is_meta: bool = False,
) -> Message:
    """Create a user message (text or tool result)."""
    if isinstance(content, list):
        # Tool result blocks
        tool_call_id = None
        text_content = tool_use_result or ""
        for block in content:
            if block.get("type") == "tool_result":
                tool_call_id = block.get("tool_use_id")
                text_content = block.get("content", text_content)
        return Message(
            role="user",
            content=text_content if isinstance(text_content, str) else str(text_content),
            tool_call_id=tool_call_id,
            tool_use_result=tool_use_result,
            source_tool_assistant_uuid=source_tool_assistant_uuid,
            is_meta=is_meta,
        )
    return Message(role="user", content=content, is_meta=is_meta)


def create_system_message(content: str, **metadata: Any) -> Message:
    """Create a system message."""
    return Message(role="system", content=content, is_meta=True, metadata=metadata)


def create_assistant_message(
    content: str | None = None,
    tool_calls: list[ToolCall] | None = None,
) -> Message:
    """Create an assistant message."""
    return Message(
        role="assistant",
        content=content,
        tool_calls=tool_calls or [],
    )


# -- Progress messages --

@dataclass
class ProgressMessage:
    """Progress update from a running tool."""

    tool_use_id: str
    type: str  # "bash_progress", "hook_progress", etc.
    data: dict[str, Any] = field(default_factory=dict)


# -- Compact boundary --

@dataclass
class CompactBoundaryMessage:
    """Marks the boundary after which messages are post-compaction."""

    summary: str
    pre_compact_token_count: int = 0
    post_compact_token_count: int = 0
    uuid: str = field(default_factory=lambda: uuid4().hex)


# -- Attachment --

@dataclass
class AttachmentMessage:
    """An attachment injected into the conversation (memory, skill, etc.)."""

    role: str = "user"
    content: str = ""
    attachment_type: str = ""  # "memory", "skill", "task_notification"
    source: str = ""
    uuid: str = field(default_factory=lambda: uuid4().hex)
    is_meta: bool = True

    def to_openai(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}


# -- Stream events --

@dataclass
class Usage:
    """Token usage from an API response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __iadd__(self, other: Usage) -> Usage:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        return self

    @property
    def effective_input_tokens(self) -> int:
        """Actual billable input tokens (cache reads are cheaper)."""
        return self.prompt_tokens + self.cache_creation_input_tokens


@dataclass
class StreamEvent:
    """A single event from an SSE stream.

    Event types:
    - "text_delta": partial text content
    - "tool_call_delta": partial tool call
    - "thinking_delta": extended thinking content
    - "finish": stream ended with finish_reason
    - "llm_input": full request sent to the model for a turn
    - "llm_output": full response received from the model for a turn
    - "usage": token usage report
    - "error": API error
    - "turn_start": new agent turn beginning
    - "turn_end": agent turn completed
    - "tool_call_start": tool execution starting
    - "tool_call_result": tool execution completed
    - "run_complete": entire run finished with aggregated result
    """

    type: str
    # text_delta
    text: str | None = None
    # tool_call_delta
    tool_call_index: int | None = None
    tool_call_id: str | None = None
    tool_call_name: str | None = None
    tool_call_arguments: str | None = None
    # thinking_delta
    thinking: str | None = None
    # finish
    finish_reason: str | None = None
    # llm_input / llm_output
    llm_messages: list[dict[str, Any]] | None = None
    llm_tools: list[dict[str, Any]] | None = None
    llm_model: str | None = None
    llm_max_tokens: int | None = None
    llm_response: dict[str, Any] | None = None
    # usage
    usage: Usage | None = None
    # error
    error: str | None = None
    error_code: int | None = None
    # turn_start / turn_end / tool_call_start
    turn: int | None = None
    # tool_call_result
    tool_result_content: str | None = None
    tool_is_error: bool = False
    # tool_progress
    tool_progress_type: str | None = None
    tool_progress_data: dict[str, Any] | None = None
    tool_progress_message: str | None = None
    # run_complete
    result: Any | None = None


# -- API Error classification --

class APIErrorType(str, Enum):
    """Classified API error types for recovery logic."""

    PROMPT_TOO_LONG = "prompt_too_long"
    MAX_OUTPUT_TOKENS = "max_output_tokens"
    OVERLOADED = "overloaded"
    RATE_LIMITED = "rate_limited"
    INVALID_REQUEST = "invalid_request"
    AUTH_ERROR = "auth_error"
    NETWORK_ERROR = "network_error"
    UNKNOWN = "unknown"
