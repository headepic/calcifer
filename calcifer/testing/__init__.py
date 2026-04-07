"""Testing utilities for downstream users of calcifer.

This submodule is a **public** part of the calcifer API but is
intentionally NOT re-exported from the top-level `calcifer.__all__`.
Import directly:

    from calcifer.testing import MockProvider, assert_tool_called

Contents:

- `MockProvider` — a drop-in replacement for `LLMProvider` that
  returns canned responses in order. Inject it via
  `Agent(config=..., provider=MockProvider([...]))`.
- `assert_tool_called` — assertion helper that walks an
  `AgentResult` looking for a specific tool invocation.
- `assert_message_count` — assertion helper that counts messages
  in an `AgentResult`, optionally filtered by role.

The goal is to let SDK users test their agents without hitting a
real LLM API. See `docs/testing.md` for usage examples.
"""

from __future__ import annotations

from .assertions import assert_message_count, assert_tool_called
from .mock_provider import MockProvider

__all__ = [
    "MockProvider",
    "assert_tool_called",
    "assert_message_count",
]
