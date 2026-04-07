"""Assertion helpers for tests that exercise calcifer agents.

These helpers walk an ``AgentResult`` and raise ``AssertionError``
with a helpful diff on failure. The failure messages are the UX of
this module — a test that fails should tell the developer exactly
what the agent DID do, not just what it didn't.
"""

from __future__ import annotations

import json
from typing import Any

from ..agent import AgentResult


def _parse_args(raw: str) -> dict[str, Any]:
    """Best-effort JSON-decode of ToolCall.arguments."""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _observed_tool_calls(result: AgentResult) -> list[tuple[str, dict[str, Any]]]:
    """Flatten every assistant-side tool call into (name, args) tuples."""
    observed: list[tuple[str, dict[str, Any]]] = []
    for msg in result.messages:
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            observed.append((tc.function_name, _parse_args(tc.arguments)))
    return observed


def _format_calls(calls: list[tuple[str, dict[str, Any]]]) -> str:
    if not calls:
        return "  (none)"
    lines = []
    for i, (name, args) in enumerate(calls, 1):
        lines.append(f"  {i}. {name}({args})")
    return "\n".join(lines)


def assert_tool_called(
    result: AgentResult,
    tool_name: str,
    *,
    args_contains: dict[str, Any] | None = None,
) -> None:
    """Assert the agent called ``tool_name`` at least once.

    Args:
        result: the :class:`AgentResult` returned by ``Agent.run``.
        tool_name: the tool name to look for (exact, case-sensitive).
        args_contains: optional subset of key/value pairs that must
            appear in the tool call's parsed JSON arguments. All
            specified keys must match; extra keys on the actual call
            are ignored.

    Raises:
        AssertionError: if no matching call is found. The error
            message lists every tool call that WAS observed so the
            developer can fix their test without re-running.
    """
    observed = _observed_tool_calls(result)
    matching = [(n, a) for (n, a) in observed if n == tool_name]

    if not matching:
        raise AssertionError(
            f"expected tool call {tool_name!r} not found in AgentResult.\n"
            f"Tool calls observed:\n{_format_calls(observed)}"
        )

    if args_contains is None:
        return

    for _, actual_args in matching:
        if all(actual_args.get(k) == v for k, v in args_contains.items()):
            return

    raise AssertionError(
        f"tool {tool_name!r} was called but no call had args "
        f"containing {args_contains}.\n"
        f"Observed call args:\n"
        + "\n".join(
            f"  {i}. {args}" for i, (_, args) in enumerate(matching, 1)
        )
    )


def assert_message_count(
    result: AgentResult,
    *,
    count: int,
    role: str | None = None,
) -> None:
    """Assert ``result.messages`` contains exactly ``count`` entries.

    Args:
        result: the :class:`AgentResult` to inspect.
        count: the expected number of messages.
        role: optional filter — if provided, only messages whose
            ``role`` equals this value are counted.

    Raises:
        AssertionError: if the count doesn't match, with a readable
            breakdown of the messages present.
    """
    if role is None:
        actual = result.messages
        label = "messages"
    else:
        actual = [m for m in result.messages if m.role == role]
        label = f"messages with role={role!r}"

    if len(actual) == count:
        return

    role_breakdown = {}
    for m in result.messages:
        role_breakdown[m.role] = role_breakdown.get(m.role, 0) + 1

    raise AssertionError(
        f"expected {count} {label}, got {len(actual)}.\n"
        f"AgentResult has {len(result.messages)} total message(s); "
        f"by role: {role_breakdown}"
    )
