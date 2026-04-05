"""Token estimation: accurate token counting for messages and tools.

Mirrors Claude Code's services/tokenEstimation.ts:
- Message-level token estimation (with per-message overhead)
- Tool schema token counting
- Thinking budget estimation
- Strip tool_reference/caller fields before counting
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..types.message import Message, Usage

logger = logging.getLogger(__name__)

# Per-message overhead tokens (role, formatting, etc.)
MESSAGE_OVERHEAD = 4
# Per-tool-call overhead
TOOL_CALL_OVERHEAD = 10
# Minimum thinking budget for estimation
MIN_THINKING_BUDGET = 1024


def _get_encoder() -> Any:
    """Get tiktoken encoder if available."""
    try:
        import tiktoken
        return tiktoken.get_encoding("cl100k_base")
    except ImportError:
        return None


def count_tokens(text: str) -> int:
    """Count tokens in text. Uses tiktoken if available, else heuristic."""
    enc = _get_encoder()
    if enc:
        return len(enc.encode(text))
    # Heuristic: ~4 chars per token for English
    return len(text) // 4


    # Approximate token costs for non-text content blocks
TOKENS_PER_IMAGE = 1_600  # ~1600 tokens for a typical image
TOKENS_PER_DOCUMENT_PAGE = 1_500  # ~1500 tokens per PDF page


def count_message_tokens(message: Message) -> int:
    """Count tokens in a single message including overhead.

    Handles multimodal content (images, documents) in addition to text.
    """
    total = MESSAGE_OVERHEAD

    if message.content:
        if isinstance(message.content, str):
            total += count_tokens(message.content)
        elif isinstance(message.content, list):
            # Multimodal content blocks
            total += _count_content_blocks(message.content)
        else:
            total += count_tokens(str(message.content))

    for tc in message.tool_calls:
        total += TOOL_CALL_OVERHEAD
        total += count_tokens(tc.function_name)
        # Strip tool_reference and caller fields before counting
        try:
            args = json.loads(tc.arguments)
            args.pop("tool_reference", None)
            args.pop("caller", None)
            total += count_tokens(json.dumps(args))
        except (json.JSONDecodeError, TypeError):
            total += count_tokens(tc.arguments)

    return total


def _count_content_blocks(blocks: list[Any]) -> int:
    """Count tokens in multimodal content blocks."""
    total = 0
    for block in blocks:
        if not isinstance(block, dict):
            total += count_tokens(str(block))
            continue
        block_type = block.get("type", "text")
        if block_type == "text":
            total += count_tokens(block.get("text", ""))
        elif block_type == "image_url" or block_type == "image":
            total += TOKENS_PER_IMAGE
        elif block_type == "document":
            # Estimate based on page count if available
            pages = block.get("pages", 1)
            total += TOKENS_PER_DOCUMENT_PAGE * max(1, pages)
        else:
            # Unknown block type — estimate from string repr
            total += count_tokens(str(block))
    return total


def count_messages_tokens(messages: list[Message]) -> int:
    """Count total tokens across all messages."""
    return sum(count_message_tokens(m) for m in messages)


def count_tool_schema_tokens(tools: list[dict[str, Any]]) -> int:
    """Estimate tokens used by tool schemas in the prompt."""
    if not tools:
        return 0
    text = json.dumps(tools)
    return count_tokens(text)


def estimate_request_tokens(
    messages: list[Message],
    tools: list[dict[str, Any]] | None = None,
    system_prompt: str = "",
    thinking_budget: int = 0,
) -> int:
    """Estimate total tokens for an API request.

    Accounts for: system prompt + messages + tool schemas + thinking.
    """
    total = 0

    if system_prompt:
        total += count_tokens(system_prompt) + MESSAGE_OVERHEAD

    total += count_messages_tokens(messages)

    if tools:
        total += count_tool_schema_tokens(tools)

    # Thinking takes budget from max_tokens, but API also counts thinking tokens
    if thinking_budget > 0:
        total += MIN_THINKING_BUDGET

    return total


def token_count_with_usage(
    messages: list[Message],
    api_usage: Usage | None = None,
) -> int:
    """Get token count preferring API-reported over estimation.

    After an API call, the response includes exact usage. Use that
    if available; fall back to estimation otherwise.
    """
    if api_usage and api_usage.prompt_tokens > 0:
        return api_usage.prompt_tokens
    return count_messages_tokens(messages)
