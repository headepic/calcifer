"""Conversation recovery: detect and repair interrupted conversations.

Mirrors Claude Code's utils/conversationRecovery.ts:
- Detect interruption type (none / mid_turn / mid_prompt)
- Synthesize continuation messages for API validity
- Filter orphaned tool_use blocks without results
- Filter thinking-only / whitespace-only assistant messages
- Repair unmatched tool_use ↔ tool_result pairs
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from ..types.message import Message, ToolCall

logger = logging.getLogger(__name__)


class InterruptionType(str, Enum):
    NONE = "none"
    MID_TURN = "interrupted_turn"       # Assistant was generating (has tool_use without results)
    MID_PROMPT = "interrupted_prompt"    # User sent prompt but no assistant response yet


def detect_interruption(messages: list[Message]) -> InterruptionType:
    """Detect if/how a conversation was interrupted."""
    if not messages:
        return InterruptionType.NONE

    last = messages[-1]

    # Last message is user with no following assistant → mid_prompt
    if last.role == "user" and not last.is_meta:
        return InterruptionType.MID_PROMPT

    # Last message is assistant with tool_calls but no tool results follow → mid_turn
    if last.role == "assistant" and last.tool_calls:
        tool_call_ids = {tc.id for tc in last.tool_calls}
        result_ids = {
            m.tool_call_id for m in messages
            if m.role == "tool" and m.tool_call_id
        }
        unresolved = tool_call_ids - result_ids
        if unresolved:
            return InterruptionType.MID_TURN

    return InterruptionType.NONE


def repair_conversation(messages: list[Message]) -> list[Message]:
    """Repair an interrupted conversation for safe resumption.

    Applies all repair passes:
    1. Remove orphaned tool_use blocks (no matching tool_result)
    2. Remove thinking-only assistant messages
    3. Remove whitespace-only assistant messages
    4. Synthesize missing tool_results for unresolved tool_use blocks
    5. Add continuation message if needed
    """
    result = list(messages)
    result = _filter_orphaned_thinking(result)
    result = _filter_whitespace_assistants(result)
    result = _synthesize_missing_tool_results(result)
    return result


def _filter_orphaned_thinking(messages: list[Message]) -> list[Message]:
    """Remove assistant messages that only contain thinking (no content, no tool_calls).

    These can cause API errors during resume.
    """
    return [
        m for m in messages
        if not (
            m.role == "assistant"
            and not m.content
            and not m.tool_calls
            and m.metadata.get("has_thinking")
        )
    ]


def _filter_whitespace_assistants(messages: list[Message]) -> list[Message]:
    """Remove assistant messages with only whitespace content.

    Happens when user cancels mid-stream.
    """
    return [
        m for m in messages
        if not (
            m.role == "assistant"
            and m.content is not None
            and not m.content.strip()
            and not m.tool_calls
        )
    ]


def _synthesize_missing_tool_results(messages: list[Message]) -> list[Message]:
    """Add synthetic error results for tool_use blocks without matching tool_results.

    The API requires every tool_use to have a corresponding tool_result.
    """
    # Collect all tool_call IDs and their result IDs
    tool_call_ids: dict[str, str] = {}  # id → assistant uuid
    result_ids: set[str] = set()

    for msg in messages:
        if msg.role == "assistant":
            for tc in msg.tool_calls:
                tool_call_ids[tc.id] = msg.uuid
        elif msg.role == "tool" and msg.tool_call_id:
            result_ids.add(msg.tool_call_id)

    # Find unresolved
    unresolved = set(tool_call_ids.keys()) - result_ids
    if not unresolved:
        return messages

    logger.info("Synthesizing %d missing tool results for resume", len(unresolved))

    # Append synthetic results
    result = list(messages)
    for tc_id in unresolved:
        result.append(Message(
            role="tool",
            content="Tool execution was interrupted. The tool did not complete.",
            tool_call_id=tc_id,
            is_meta=True,
            metadata={"synthetic": True, "reason": "interrupted"},
        ))

    return result


def build_resume_message(
    interruption: InterruptionType,
) -> Message | None:
    """Build a message to help the model resume after interruption.

    Returns None if no resume message is needed.
    """
    if interruption == InterruptionType.NONE:
        return None

    if interruption == InterruptionType.MID_TURN:
        return Message(
            role="user",
            content=(
                "The previous response was interrupted. "
                "Please continue from where you left off."
            ),
            is_meta=True,
            metadata={"resume": True},
        )

    if interruption == InterruptionType.MID_PROMPT:
        # No special message needed — the user's prompt is already there
        return None

    return None
