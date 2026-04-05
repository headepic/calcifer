"""Tests for context management."""

import pytest

from calcifer import Message, Usage
from calcifer.services.compact.context import ContextManager, count_message_tokens, estimate_tokens


def test_estimate_tokens_heuristic():
    # Without tiktoken, uses ~4 chars per token heuristic
    text = "Hello world, this is a test."
    tokens = estimate_tokens(text)
    assert tokens > 0
    assert tokens < len(text)  # Should be fewer tokens than chars


def test_count_message_tokens():
    messages = [
        Message(role="system", content="You are a helpful assistant."),
        Message(role="user", content="Hello"),
    ]
    tokens = count_message_tokens(messages)
    assert tokens > 0


def test_context_manager_needs_compaction():
    mgr = ContextManager(max_context_tokens=100, compact_threshold=0.9)

    # Short conversation should not need compaction
    short = [Message(role="user", content="hi")]
    assert mgr.needs_compaction(short) is False

    # Long conversation should need compaction
    long_msg = Message(role="user", content="x " * 500)
    assert mgr.needs_compaction([long_msg]) is True


def test_context_manager_with_api_usage():
    mgr = ContextManager(max_context_tokens=1000, compact_threshold=0.9)
    mgr.update_usage(Usage(prompt_tokens=950, completion_tokens=0, total_tokens=950))

    # API-reported tokens exceed threshold
    assert mgr.needs_compaction([]) is True


def test_compact_messages_preserves_system():
    mgr = ContextManager(max_context_tokens=1000)

    messages = [
        Message(role="system", content="System prompt"),
        Message(role="user", content="msg1"),
        Message(role="assistant", content="reply1"),
        Message(role="user", content="msg2"),
        Message(role="assistant", content="reply2"),
    ]

    result = mgr.compact_messages(messages, "Summary of conversation")

    # System message should be preserved
    assert result[0].role == "system"
    assert result[0].content == "System prompt"

    # Summary should be present
    summaries = [m for m in result if "summary" in (m.content or "").lower()]
    assert len(summaries) > 0


def test_build_compact_prompt():
    mgr = ContextManager()
    messages = [
        Message(role="system", content="System"),
        Message(role="user", content="What is 2+2?"),
        Message(role="assistant", content="4"),
    ]

    prompt = mgr.build_compact_prompt(messages)
    assert len(prompt) == 2
    assert prompt[0].role == "system"
    assert "summarize" in prompt[0].content.lower()
    assert prompt[1].role == "user"
