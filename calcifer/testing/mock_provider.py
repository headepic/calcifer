"""MockProvider: LLMProvider-compatible fake for tests.

`MockProvider` is duck-typed against `calcifer.LLMProvider`. It does
NOT subclass it — subclassing would pull in the real provider's
httpx client machinery and force the test environment to construct
a network-capable object. The only contract is "has `chat_completion`
and `chat_completion_stream` methods with matching signatures."

Usage:

    from calcifer import Agent, CalciferConfig
    from calcifer.testing import MockProvider

    provider = MockProvider([
        {"tool_calls": [{"name": "add", "arguments": {"a": 1, "b": 2}}]},
        "The answer is 3.",
    ])
    agent = Agent(config=CalciferConfig(api_key="test"), provider=provider)
    result = await agent.run("what is 1+2?")
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Literal

from ..types.message import Message, StreamEvent, ToolCall, Usage


# A queued response can be any of these shapes — MockProvider
# normalizes to Message on demand.
ResponseSpec = str | dict[str, Any] | Message


def _next_tool_call_id(index: int) -> str:
    return f"mock_tc_{index}"


def _normalize(response: ResponseSpec, index: int) -> Message:
    """Turn a queued response into a Message."""
    if isinstance(response, Message):
        return response

    if isinstance(response, str):
        return Message(role="assistant", content=response)

    if isinstance(response, dict):
        text = response.get("text")
        raw_tool_calls = response.get("tool_calls") or []
        tool_calls: list[ToolCall] = []
        for i, tc in enumerate(raw_tool_calls):
            args = tc.get("arguments", {})
            # Accept dict (canonical) or string (pre-encoded JSON).
            if isinstance(args, dict):
                args_str = json.dumps(args)
            else:
                args_str = str(args)
            tool_calls.append(
                ToolCall(
                    id=tc.get("id") or _next_tool_call_id(index * 100 + i),
                    function_name=tc["name"],
                    arguments=args_str,
                )
            )
        return Message(
            role="assistant",
            content=text,
            tool_calls=tool_calls or None,
        )

    raise TypeError(
        f"MockProvider response must be str, dict, or Message; "
        f"got {type(response).__name__}"
    )


class MockProvider:
    """LLMProvider-compatible fake backed by a fixed list of responses.

    Args:
        responses: a list of queued responses. Each entry is a str
            (plain assistant text), a dict (``{"text": ..., "tool_calls": [...]}``),
            or a fully-built :class:`Message`.
        exhausted: behavior when the queue is exhausted — ``"raise"``
            (default) raises RuntimeError, ``"repeat"`` keeps returning
            the last response forever.

    Attributes:
        calls: list of dicts, one per `chat_completion` or
            `chat_completion_stream` invocation, recording the messages
            and tools the agent passed in. Useful for spies.
    """

    def __init__(
        self,
        responses: list[ResponseSpec],
        *,
        exhausted: Literal["raise", "repeat"] = "raise",
    ) -> None:
        self._responses: list[ResponseSpec] = list(responses)
        self._exhausted_policy = exhausted
        self._cursor = 0
        self.calls: list[dict[str, Any]] = []

    def _next_response(self) -> Message:
        if self._cursor < len(self._responses):
            raw = self._responses[self._cursor]
            self._cursor += 1
            return _normalize(raw, self._cursor - 1)

        # Queue exhausted
        if self._exhausted_policy == "repeat":
            if not self._responses:
                raise RuntimeError(
                    "MockProvider(exhausted='repeat') has no responses "
                    "to repeat — the queue was empty at construction"
                )
            last_index = len(self._responses) - 1
            return _normalize(self._responses[last_index], last_index)

        raise RuntimeError(
            f"MockProvider exhausted after {len(self._responses)} "
            f"response(s); queue empty at call #{self._cursor + 1}"
        )

    async def chat_completion(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        model_override: str | None = None,
        max_tokens_override: int | None = None,
    ) -> tuple[Message, Usage]:
        self.calls.append(
            {
                "method": "chat_completion",
                "messages": list(messages),
                "tools": tools,
                "model_override": model_override,
                "max_tokens_override": max_tokens_override,
            }
        )
        msg = self._next_response()
        usage = Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        return msg, usage

    async def chat_completion_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        model_override: str | None = None,
        max_tokens_override: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Yield a minimal stream event sequence.

        For text responses: one `text_delta` with the full text, then
        a `finish` event with `finish_reason="stop"`, then a `usage`
        event.

        For tool-call responses: one `tool_call_delta` per ToolCall
        followed by `finish(finish_reason="tool_calls")`, then `usage`.

        This is enough to let `Agent.run_stream` terminate cleanly.
        It is not a faithful reproduction of real SSE chunking —
        users who care about delta-by-delta behavior should patch
        directly.
        """
        self.calls.append(
            {
                "method": "chat_completion_stream",
                "messages": list(messages),
                "tools": tools,
                "model_override": model_override,
                "max_tokens_override": max_tokens_override,
            }
        )
        msg = self._next_response()

        if msg.tool_calls:
            for i, tc in enumerate(msg.tool_calls):
                yield StreamEvent(
                    type="tool_call_delta",
                    tool_call_index=i,
                    tool_call_id=tc.id,
                    tool_call_name=tc.function_name,
                    tool_call_arguments=tc.arguments,
                )
            yield StreamEvent(type="finish", finish_reason="tool_calls")
        else:
            yield StreamEvent(type="text_delta", text=msg.content or "")
            yield StreamEvent(type="finish", finish_reason="stop")

        yield StreamEvent(
            type="usage",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )
