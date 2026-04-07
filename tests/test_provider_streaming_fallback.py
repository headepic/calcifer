"""Regression test for the empty-content non-streaming fallback in
LLMProvider.chat_completion.

Some OpenAI-compatible proxies (notably certain local gateways) return
`content: null` with no tool_calls in non-streaming mode despite reporting
nonzero completion_tokens — the visible text is only emitted via SSE.

LLMProvider detects that pattern, retries via streaming, accumulates the
deltas into a Message + Usage, and sets a sticky flag so subsequent calls
go straight to the streaming path.
"""

from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from calcifer.services.api.provider import LLMProvider
from calcifer.types.message import Message, StreamEvent, Usage


def _make_empty_response() -> httpx.Response:
    """Build an httpx.Response that mimics the buggy proxy: 200 OK,
    finish_reason=stop, completion_tokens>0, but content is null.
    """
    return httpx.Response(
        200,
        json={
            "id": "resp_test",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": None,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 12,
                "total_tokens": 17,
            },
        },
    )


async def _fake_stream(*_args, **_kwargs) -> AsyncIterator[StreamEvent]:
    """Fake chat_completion_stream that yields a couple of text deltas
    plus a usage event — what the proxy would have actually sent over SSE.
    """
    yield StreamEvent(type="text_delta", text="Hello")
    yield StreamEvent(type="text_delta", text=" world")
    yield StreamEvent(
        type="usage",
        usage=Usage(prompt_tokens=5, completion_tokens=12, total_tokens=17),
    )
    yield StreamEvent(type="finish", finish_reason="stop")


@pytest.mark.asyncio
async def test_empty_content_falls_back_to_streaming(monkeypatch):
    provider = LLMProvider(api_key="x", base_url="http://x", model="m")

    # Stub the underlying HTTP client to return the buggy non-streaming
    # response on every POST.
    provider._client = MagicMock()
    provider._client.post = AsyncMock(return_value=_make_empty_response())
    provider._client.aclose = AsyncMock()

    # Stub the streaming method on the same instance.
    monkeypatch.setattr(provider, "chat_completion_stream", _fake_stream)

    msg, usage = await provider.chat_completion(
        messages=[Message(role="user", content="hi")]
    )

    assert msg.role == "assistant"
    assert msg.content == "Hello world"
    assert usage.completion_tokens == 12
    assert provider._force_stream_for_chat_completion is True


@pytest.mark.asyncio
async def test_sticky_flag_skips_non_streaming_on_subsequent_calls(monkeypatch):
    provider = LLMProvider(api_key="x", base_url="http://x", model="m")
    provider._force_stream_for_chat_completion = True  # already detected

    # If chat_completion ever calls the http client, the test should fail.
    provider._client = MagicMock()
    provider._client.post = AsyncMock(
        side_effect=AssertionError("non-streaming should be skipped")
    )
    provider._client.aclose = AsyncMock()

    monkeypatch.setattr(provider, "chat_completion_stream", _fake_stream)

    msg, usage = await provider.chat_completion(
        messages=[Message(role="user", content="hi")]
    )

    assert msg.content == "Hello world"
    assert usage.completion_tokens == 12
    provider._client.post.assert_not_called()


@pytest.mark.asyncio
async def test_legitimate_empty_content_with_tool_calls_does_not_trigger_fallback():
    """If the response has tool_calls but no text content, that's a legitimate
    'pure tool call' assistant turn — we must NOT trigger the fallback.
    """
    provider = LLMProvider(api_key="x", base_url="http://x", model="m")

    response = httpx.Response(
        200,
        json={
            "id": "resp_test",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "do_thing",
                                    "arguments": '{"x": 1}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 8,
                "total_tokens": 13,
            },
        },
    )
    provider._client = MagicMock()
    provider._client.post = AsyncMock(return_value=response)
    provider._client.aclose = AsyncMock()

    msg, usage = await provider.chat_completion(
        messages=[Message(role="user", content="hi")]
    )

    assert msg.tool_calls and len(msg.tool_calls) == 1
    assert msg.tool_calls[0].function_name == "do_thing"
    assert provider._force_stream_for_chat_completion is False
