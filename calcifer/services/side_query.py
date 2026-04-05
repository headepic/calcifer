"""Side query: lightweight LLM calls outside the main agent loop.

Mirrors Claude Code's utils/sideQuery.ts — used for:
- Compaction summarization
- Memory retrieval (LLM-based selection)
- Security classification
- Structured output extraction
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..types.message import Message, Usage

logger = logging.getLogger(__name__)


async def side_query(
    provider: Any,  # LLMProvider
    prompt: str,
    *,
    system_prompt: str = "",
    model_override: str | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.0,
    json_schema: dict[str, Any] | None = None,
) -> tuple[str, Usage]:
    """Run a lightweight LLM call outside the main agent loop.

    Args:
        provider: LLMProvider instance.
        prompt: User message.
        system_prompt: Optional system message.
        model_override: Use a different model (e.g., haiku for classification).
        max_tokens: Lower than main loop (default 2048).
        json_schema: If provided, request structured JSON output.

    Returns:
        (response_text, usage)
    """
    messages: list[Message] = []
    if system_prompt:
        messages.append(Message(role="system", content=system_prompt))
    messages.append(Message(role="user", content=prompt))

    msg, usage = await provider.chat_completion(
        messages=messages,
        model_override=model_override,
        max_tokens_override=max_tokens,
    )

    text = msg.content or ""

    # If JSON schema requested, try to parse and validate
    if json_schema and text:
        try:
            parsed = json.loads(text)
            return json.dumps(parsed), usage
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code block
            if "```json" in text:
                start = text.index("```json") + 7
                end = text.index("```", start)
                text = text[start:end].strip()

    return text, usage


async def classify(
    provider: Any,
    text: str,
    categories: list[str],
    *,
    model_override: str | None = None,
) -> str:
    """Classify text into one of the given categories.

    Returns the category name.
    """
    cat_list = "\n".join(f"- {c}" for c in categories)
    prompt = (
        f"Classify the following text into exactly one of these categories:\n"
        f"{cat_list}\n\n"
        f"Text: {text}\n\n"
        f"Respond with only the category name, nothing else."
    )

    result, _ = await side_query(
        provider, prompt,
        model_override=model_override,
        max_tokens=50,
    )
    return result.strip()
