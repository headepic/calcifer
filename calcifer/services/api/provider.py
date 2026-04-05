"""LLM Provider: OpenAI-compatible chat completion via httpx.

Mirrors Claude Code's services/api/:
- Streaming + non-streaming dual path
- Exponential backoff with jitter for retryable errors
- 529 overload detection with max retry limit
- Model fallback on persistent overload
- Extended thinking (thinking_delta) support
- Token counting from API response
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, AsyncIterator

import httpx

from ...types.message import APIErrorType, Message, StreamEvent, ToolCall, Usage

logger = logging.getLogger(__name__)

# Retry config
MAX_RETRIES = 10
MAX_529_RETRIES = 3
RETRY_STATUS_CODES = {429, 500, 502, 503, 529}
BASE_DELAY_S = 0.5
MAX_DELAY_S = 30.0


class LLMProviderError(Exception):
    """Error from the LLM provider."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        error_type: APIErrorType = APIErrorType.UNKNOWN,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


def classify_api_error(status_code: int | None, body: str) -> APIErrorType:
    """Classify an API error for recovery logic."""
    if status_code == 429:
        return APIErrorType.RATE_LIMITED
    if status_code == 529:
        return APIErrorType.OVERLOADED
    if status_code in (500, 502, 503):
        return APIErrorType.OVERLOADED
    if status_code == 401 or status_code == 403:
        return APIErrorType.AUTH_ERROR
    if status_code == 400:
        body_lower = body.lower()
        if "prompt" in body_lower and "long" in body_lower:
            return APIErrorType.PROMPT_TOO_LONG
        if "max_tokens" in body_lower or "max_output" in body_lower:
            return APIErrorType.MAX_OUTPUT_TOKENS
        return APIErrorType.INVALID_REQUEST
    return APIErrorType.UNKNOWN


class LLMProvider:
    """OpenAI-compatible chat completion provider."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "http://127.0.0.1:8317/v1",
        model: str = "gpt-4o",
        max_tokens: int = 8192,
        temperature: float = 0.0,
        extra_params: dict[str, Any] | None = None,
        timeout: float = 300.0,
        fallback_model: str | None = None,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.extra_params = extra_params or {}
        self.fallback_model = fallback_model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        self._consecutive_529s = 0

    async def close(self) -> None:
        await self._client.aclose()

    def _build_request_body(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        model_override: str | None = None,
        max_tokens_override: int | None = None,
    ) -> dict[str, Any]:
        from .normalize import normalize_messages_for_api
        normalized = normalize_messages_for_api(messages)
        body: dict[str, Any] = {
            "model": model_override or self.model,
            "messages": [m.to_openai() for m in normalized],
            "max_tokens": max_tokens_override or self.max_tokens,
            "temperature": self.temperature,
            "stream": stream,
            **self.extra_params,
        }
        if tools:
            body["tools"] = tools
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with jitter."""
        delay = min(BASE_DELAY_S * (2 ** attempt), MAX_DELAY_S)
        jitter = random.uniform(0, delay * 0.1)
        return delay + jitter

    @staticmethod
    def _parse_retry_after(resp: httpx.Response) -> float | None:
        """Parse retry-after header. Returns seconds or None."""
        raw = resp.headers.get("retry-after")
        if not raw:
            return None
        try:
            return min(float(raw), MAX_DELAY_S)
        except (ValueError, TypeError):
            return None

    async def chat_completion(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        model_override: str | None = None,
        max_tokens_override: int | None = None,
    ) -> tuple[Message, Usage]:
        """Non-streaming chat completion with retry and fallback.

        Returns (assistant_message, usage).
        """
        body = self._build_request_body(
            messages, tools, stream=False,
            model_override=model_override,
            max_tokens_override=max_tokens_override,
        )

        last_error: LLMProviderError | None = None
        consecutive_529s = 0

        for attempt in range(MAX_RETRIES):
            try:
                resp = await self._client.post("/chat/completions", json=body)

                if resp.status_code in RETRY_STATUS_CODES:
                    error_body = resp.text
                    error_type = classify_api_error(resp.status_code, error_body)
                    last_error = LLMProviderError(
                        error_body, status_code=resp.status_code, error_type=error_type
                    )

                    # Track 529s for fallback
                    if resp.status_code == 529:
                        consecutive_529s += 1
                        if consecutive_529s >= MAX_529_RETRIES and self.fallback_model:
                            logger.warning(
                                "Falling back to %s after %d consecutive 529s",
                                self.fallback_model, consecutive_529s,
                            )
                            body["model"] = self.fallback_model
                            consecutive_529s = 0
                    else:
                        consecutive_529s = 0

                    delay = self._parse_retry_after(resp) or self._backoff_delay(attempt)
                    logger.warning(
                        "Retryable error %d (%s, attempt %d/%d), waiting %.1fs",
                        resp.status_code, error_type.value,
                        attempt + 1, MAX_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code != 200:
                    error_body = resp.text
                    error_type = classify_api_error(resp.status_code, error_body)
                    raise LLMProviderError(
                        error_body, status_code=resp.status_code, error_type=error_type
                    )

                break

            except httpx.HTTPStatusError as e:
                raise LLMProviderError(
                    str(e), status_code=e.response.status_code
                ) from e
            except httpx.TimeoutException as e:
                last_error = LLMProviderError(
                    f"Request timed out: {e}", error_type=APIErrorType.NETWORK_ERROR
                )
                if attempt < MAX_RETRIES - 1:
                    delay = self._backoff_delay(attempt)
                    logger.warning("Timeout (attempt %d/%d), waiting %.1fs", attempt + 1, MAX_RETRIES, delay)
                    await asyncio.sleep(delay)
                    continue
                raise last_error from e
            except httpx.ConnectError as e:
                last_error = LLMProviderError(
                    f"Connection failed: {e}", error_type=APIErrorType.NETWORK_ERROR
                )
                if attempt < MAX_RETRIES - 1:
                    delay = self._backoff_delay(attempt)
                    await asyncio.sleep(delay)
                    continue
                raise last_error from e
        else:
            raise last_error or LLMProviderError("Max retries exceeded")

        data = resp.json()
        return self._parse_response(data)

    def _parse_response(self, data: dict[str, Any]) -> tuple[Message, Usage]:
        """Parse a chat completion response into Message + Usage."""
        choice = data["choices"][0]["message"]

        tool_calls: list[ToolCall] = []
        for tc in choice.get("tool_calls") or []:
            tool_calls.append(
                ToolCall(
                    id=tc["id"],
                    function_name=tc["function"]["name"],
                    arguments=tc["function"]["arguments"],
                )
            )

        msg = Message(
            role="assistant",
            content=choice.get("content"),
            tool_calls=tool_calls,
        )

        raw_usage = data.get("usage", {})
        usage = Usage(
            prompt_tokens=raw_usage.get("prompt_tokens", 0),
            completion_tokens=raw_usage.get("completion_tokens", 0),
            total_tokens=raw_usage.get("total_tokens", 0),
            cache_read_input_tokens=raw_usage.get("cache_read_input_tokens", 0),
            cache_creation_input_tokens=raw_usage.get("cache_creation_input_tokens", 0),
        )

        # Check for max_output_tokens stop reason
        finish_reason = data["choices"][0].get("finish_reason")
        if finish_reason == "length":
            msg.metadata["api_error"] = "max_output_tokens"

        return msg, usage

    async def chat_completion_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        model_override: str | None = None,
        max_tokens_override: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Streaming chat completion. Yields StreamEvents.

        Handles:
        - Text deltas
        - Tool call deltas (accumulated by index)
        - Thinking deltas (extended thinking)
        - Usage (final chunk)
        - Finish reason
        - Error detection (prompt_too_long, max_output_tokens via finish_reason)
        - Retry with backoff on transient errors (429/500/502/503/529)
        """
        body = self._build_request_body(
            messages, tools, stream=True,
            model_override=model_override,
            max_tokens_override=max_tokens_override,
        )

        for attempt in range(MAX_RETRIES):
            try:
                async with self._client.stream(
                    "POST", "/chat/completions", json=body
                ) as resp:
                    if resp.status_code in RETRY_STATUS_CODES:
                        text = await resp.aread()
                        error_body = text.decode()
                        error_type = classify_api_error(resp.status_code, error_body)
                        if attempt < MAX_RETRIES - 1:
                            delay = self._parse_retry_after(resp) or self._backoff_delay(attempt)
                            logger.warning(
                                "Stream retryable error %d (%s, attempt %d/%d), waiting %.1fs",
                                resp.status_code, error_type.value,
                                attempt + 1, MAX_RETRIES, delay,
                            )
                            await asyncio.sleep(delay)
                            continue
                        # Last attempt — yield error
                        yield StreamEvent(
                            type="error",
                            error=error_body,
                            error_code=resp.status_code,
                        )
                        return

                    if resp.status_code != 200:
                        text = await resp.aread()
                        error_body = text.decode()
                        yield StreamEvent(
                            type="error",
                            error=error_body,
                            error_code=resp.status_code,
                        )
                        return

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:]
                        if payload.strip() == "[DONE]":
                            return

                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue

                        # Parse usage (final chunk)
                        if "usage" in chunk and chunk["usage"]:
                            raw = chunk["usage"]
                            yield StreamEvent(
                                type="usage",
                                usage=Usage(
                                    prompt_tokens=raw.get("prompt_tokens", 0),
                                    completion_tokens=raw.get("completion_tokens", 0),
                                    total_tokens=raw.get("total_tokens", 0),
                                    cache_read_input_tokens=raw.get("cache_read_input_tokens", 0),
                                    cache_creation_input_tokens=raw.get("cache_creation_input_tokens", 0),
                                ),
                            )

                        choices = chunk.get("choices", [])
                        if not choices:
                            continue

                        delta = choices[0].get("delta", {})
                        finish_reason = choices[0].get("finish_reason")

                        # Text delta
                        if delta.get("content"):
                            yield StreamEvent(type="text_delta", text=delta["content"])

                        # Thinking delta (extended thinking / chain of thought)
                        if delta.get("thinking"):
                            yield StreamEvent(type="thinking_delta", thinking=delta["thinking"])

                        # Tool call deltas
                        for tc_delta in delta.get("tool_calls") or []:
                            yield StreamEvent(
                                type="tool_call_delta",
                                tool_call_index=tc_delta.get("index"),
                                tool_call_id=tc_delta.get("id"),
                                tool_call_name=tc_delta.get("function", {}).get("name"),
                                tool_call_arguments=tc_delta.get("function", {}).get("arguments"),
                            )

                        # Finish
                        if finish_reason:
                            yield StreamEvent(
                                type="finish", finish_reason=finish_reason
                            )
                    # Stream completed successfully
                    return

            except httpx.TimeoutException:
                if attempt < MAX_RETRIES - 1:
                    delay = self._backoff_delay(attempt)
                    logger.warning("Stream timeout (attempt %d/%d), waiting %.1fs", attempt + 1, MAX_RETRIES, delay)
                    await asyncio.sleep(delay)
                    continue
                yield StreamEvent(type="error", error="Stream timed out", error_code=0)
                return
            except httpx.ConnectError:
                if attempt < MAX_RETRIES - 1:
                    delay = self._backoff_delay(attempt)
                    await asyncio.sleep(delay)
                    continue
                yield StreamEvent(type="error", error="Connection failed", error_code=0)
                return
