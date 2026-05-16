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
from calcifer import Agent, CalciferConfig, tool
from calcifer.types.message import Message, StreamEvent, ToolCall, Usage


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


async def _fake_reasoning_tool_stream(*_args, **_kwargs) -> AsyncIterator[StreamEvent]:
    yield StreamEvent(type="thinking_delta", thinking="Need source lookup.")
    yield StreamEvent(
        type="tool_call_delta",
        tool_call_index=0,
        tool_call_id="tc_lookup",
        tool_call_name="lookup",
        tool_call_arguments='{"query":"docs"}',
    )
    yield StreamEvent(
        type="usage",
        usage=Usage(prompt_tokens=5, completion_tokens=12, total_tokens=17),
    )
    yield StreamEvent(type="finish", finish_reason="tool_calls")


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self.status_code = 200
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


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


def test_message_to_openai_round_trips_reasoning_content_for_tool_turns():
    msg = Message(
        role="assistant",
        content=None,
        tool_calls=[
            ToolCall(id="tc_1", function_name="lookup", arguments='{"query":"docs"}')
        ],
        reasoning_content="Need source lookup.",
    )

    payload = msg.to_openai()

    assert payload["reasoning_content"] == "Need source lookup."


def test_parse_response_preserves_reasoning_content():
    provider = LLMProvider(api_key="x", base_url="http://x", model="m")
    data = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "Need source lookup.",
                    "tool_calls": [
                        {
                            "id": "tc_1",
                            "type": "function",
                            "function": {
                                "name": "lookup",
                                "arguments": '{"query":"docs"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 12, "total_tokens": 17},
    }

    msg, _usage = provider._parse_response(data)

    assert msg.reasoning_content == "Need source lookup."


@pytest.mark.asyncio
async def test_stream_parser_emits_deepseek_reasoning_content_as_thinking_delta():
    provider = LLMProvider(api_key="x", base_url="http://x", model="m")
    provider._client = MagicMock()
    provider._client.stream = MagicMock(
        return_value=_FakeStreamResponse(
            [
                'data: {"choices":[{"delta":{"reasoning_content":"Need "},"finish_reason":null}]}',
                'data: {"choices":[{"delta":{"reasoning_content":"source lookup."},"finish_reason":null}]}',
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            ]
        )
    )
    provider._client.aclose = AsyncMock()

    events = [
        event
        async for event in provider.chat_completion_stream(
            messages=[Message(role="user", content="hi")]
        )
    ]

    assert [event.thinking for event in events if event.type == "thinking_delta"] == [
        "Need ",
        "source lookup.",
    ]


@pytest.mark.asyncio
async def test_streaming_accumulator_preserves_reasoning_content_for_tool_calls(monkeypatch):
    provider = LLMProvider(api_key="x", base_url="http://x", model="m")
    monkeypatch.setattr(provider, "chat_completion_stream", _fake_reasoning_tool_stream)

    msg, _usage = await provider._chat_completion_via_stream(
        messages=[Message(role="user", content="hi")]
    )

    assert msg.reasoning_content == "Need source lookup."


@tool(name="lookup", description="Look up a source")
def lookup_tool(query: str) -> str:
    return f"result for {query}"


class _ReasoningToolProvider:
    def __init__(self) -> None:
        self.calls: list[list[Message]] = []

    async def chat_completion(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        model_override: str | None = None,
        max_tokens_override: int | None = None,
    ) -> tuple[Message, Usage]:
        return Message(role="assistant", content="compact summary"), Usage()

    async def chat_completion_stream(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        model_override: str | None = None,
        max_tokens_override: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(list(messages))
        if len(self.calls) == 1:
            yield StreamEvent(type="thinking_delta", thinking="Need source lookup.")
            yield StreamEvent(
                type="tool_call_delta",
                tool_call_index=0,
                tool_call_id="tc_lookup",
                tool_call_name="lookup",
                tool_call_arguments='{"query":"docs"}',
            )
            yield StreamEvent(type="finish", finish_reason="tool_calls")
            yield StreamEvent(
                type="usage",
                usage=Usage(prompt_tokens=5, completion_tokens=12, total_tokens=17),
            )
            return

        tool_turn = next(msg for msg in messages if msg.role == "assistant" and msg.tool_calls)
        assert tool_turn.reasoning_content == "Need source lookup."
        yield StreamEvent(type="text_delta", text="final answer")
        yield StreamEvent(type="finish", finish_reason="stop")
        yield StreamEvent(
            type="usage",
            usage=Usage(prompt_tokens=5, completion_tokens=3, total_tokens=8),
        )


@pytest.mark.asyncio
async def test_agent_round_trips_reasoning_content_after_streamed_tool_call():
    provider = _ReasoningToolProvider()
    agent = Agent(
        config=CalciferConfig(api_key="mock", base_url="mock", model="mock"),
        provider=provider,
        tools=[lookup_tool],
    )

    result = None
    async for event in agent.run_stream("find docs"):
        if event.type == "run_complete":
            result = event.result

    assert result is not None
    assert result.final_text == "final answer"
    assert len(provider.calls) == 2
