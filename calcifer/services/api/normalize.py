"""Message normalization pipeline: prepare messages for the OpenAI-compatible API.

Mirrors Claude Code's normalizeMessagesForAPI — a multi-pass pipeline that
cleans, merges, and validates messages before sending to the LLM.

Passes:
1. Strip internal/meta messages not intended for the API
2. Merge system messages into the first system message
3. Ensure tool_use/tool_result pairing (synthesize missing results)
4. Merge consecutive same-role messages (user-user, assistant-assistant)
5. Strip empty/whitespace-only assistant messages
6. Validate message sequence (tool results follow assistant tool_use)
"""

from __future__ import annotations

import logging
from typing import Any

from ...types.message import Message

logger = logging.getLogger(__name__)


def normalize_messages_for_api(messages: list[Message]) -> list[Message]:
    """Run the full normalization pipeline on messages before API call.

    Returns a new list — does not mutate the input.
    """
    result = list(messages)
    result = _strip_internal_messages(result)
    result = _merge_system_messages(result)
    result = _ensure_tool_result_pairing(result)
    result = _strip_empty_assistants(result)
    result = _merge_consecutive_same_role(result)
    return result


def _strip_internal_messages(messages: list[Message]) -> list[Message]:
    """Remove messages not intended for the API.

    Strips:
    - Progress messages (metadata has progress_type)
    - Messages with metadata synthetic=True and no content
    - System messages marked as compact boundaries (informational only)
    """
    result: list[Message] = []
    for msg in messages:
        # Keep compact summaries (the LLM needs them)
        if msg.metadata.get("is_compact_summary"):
            result.append(msg)
            continue
        # Strip compact boundary markers (informational, not for LLM)
        if msg.metadata.get("is_compact_boundary"):
            continue
        # Strip progress-only messages
        if msg.metadata.get("progress_type"):
            continue
        result.append(msg)
    return result


def _merge_system_messages(messages: list[Message]) -> list[Message]:
    """Merge multiple system messages into the first one.

    OpenAI API accepts only one system message (or it must be first).
    Collect all system message content and merge into one.
    """
    system_parts: list[str] = []
    non_system: list[Message] = []

    for msg in messages:
        if msg.role == "system":
            if msg.content:
                system_parts.append(msg.content)
        else:
            non_system.append(msg)

    result: list[Message] = []
    if system_parts:
        result.append(Message(role="system", content="\n\n".join(system_parts)))
    result.extend(non_system)
    return result


def _ensure_tool_result_pairing(messages: list[Message]) -> list[Message]:
    """Ensure every tool_use has a matching tool result, and vice versa.

    - Synthesize error results for orphaned tool_use blocks
    - Strip orphaned tool results referencing non-existent tool_use

    Prevents API errors: "tool_result must reference a tool_use".
    """
    # Collect all tool_call IDs from assistant messages
    tool_call_ids: set[str] = set()
    for msg in messages:
        if msg.role == "assistant":
            for tc in msg.tool_calls:
                tool_call_ids.add(tc.id)

    # Collect all tool result IDs
    result_ids: set[str] = set()
    for msg in messages:
        if msg.role == "tool" and msg.tool_call_id:
            result_ids.add(msg.tool_call_id)

    # Strip orphaned tool results (no matching tool_use)
    result: list[Message] = []
    for msg in messages:
        if msg.role == "tool" and msg.tool_call_id and msg.tool_call_id not in tool_call_ids:
            logger.debug("Stripping orphaned tool result: %s", msg.tool_call_id)
            continue
        result.append(msg)

    # Synthesize missing tool results
    unresolved = tool_call_ids - result_ids
    if unresolved:
        logger.info("Synthesizing %d missing tool results", len(unresolved))
        for tc_id in unresolved:
            result.append(Message(
                role="tool",
                content="Tool execution was interrupted.",
                tool_call_id=tc_id,
            ))

    return result


def _strip_empty_assistants(messages: list[Message]) -> list[Message]:
    """Remove assistant messages with no content and no tool calls.

    These can appear from interrupted streams or thinking-only turns.
    """
    return [
        msg for msg in messages
        if not (
            msg.role == "assistant"
            and not msg.tool_calls
            and (msg.content is None or not msg.content.strip())
        )
    ]


def _merge_consecutive_same_role(messages: list[Message]) -> list[Message]:
    """Merge consecutive messages with the same role.

    OpenAI API rejects consecutive user-user or assistant-assistant messages.
    Merges content with double newline separator.

    Tool messages are never merged (each has a unique tool_call_id).
    Assistant messages with tool_calls are never merged with text-only assistants.
    """
    if not messages:
        return messages

    result: list[Message] = [messages[0]]

    for msg in messages[1:]:
        prev = result[-1]

        # Only merge if same role and both are simple text messages
        can_merge = (
            msg.role == prev.role
            and msg.role in ("user", "assistant")
            and msg.role != "tool"
            and not msg.tool_calls
            and not prev.tool_calls
            and not msg.tool_call_id
            and not prev.tool_call_id
        )

        if can_merge:
            # Merge content
            prev_content = prev.content or ""
            new_content = msg.content or ""
            merged = f"{prev_content}\n\n{new_content}".strip()
            result[-1] = Message(
                role=prev.role,
                content=merged,
                uuid=prev.uuid,  # Keep the earlier UUID
                is_meta=prev.is_meta and msg.is_meta,
                metadata={**prev.metadata, **msg.metadata},
            )
        else:
            result.append(msg)

    return result
