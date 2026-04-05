"""Hook system: event-driven tool execution interception.

Mirrors Claude Code's utils/hooks.ts:
- PreToolUse / PostToolUse hooks
- JSON protocol (stdin input, stdout response)
- Permission decision (allow/deny/ask)
- Input rewriting (updatedInput)
- Shell command hooks + Python callable hooks
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

HOOK_TIMEOUT_S = 30.0


class HookEvent(str, Enum):
    """Hook event types."""
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"


@dataclass
class HookInput:
    """Input passed to a hook."""
    hook_event_name: str
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    cwd: str = "."


@dataclass
class HookResult:
    """Result from a hook execution."""
    # continue=False blocks the tool execution
    should_continue: bool = True
    stop_reason: str = ""
    # Permission decision override
    permission_decision: str = ""  # "allow", "deny", "ask", ""
    # Rewrite tool input
    updated_input: dict[str, Any] | None = None
    # Additional context for the model
    additional_context: str = ""


@dataclass
class HookConfig:
    """Configuration for a single hook."""
    event: HookEvent
    # Shell command hook
    command: str = ""
    # Python callable hook
    callback: Callable[[HookInput], Awaitable[HookResult] | HookResult] | None = None
    # Matcher: only run for matching tool names / patterns
    tool_pattern: str = ""  # e.g. "Bash", "mcp__*", "Bash(git *)"
    timeout: float = HOOK_TIMEOUT_S


class HookManager:
    """Manages hook registration and execution."""

    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[HookConfig]] = {e: [] for e in HookEvent}

    def register(self, config: HookConfig) -> None:
        """Register a hook."""
        self._hooks[config.event].append(config)

    def register_callback(
        self,
        event: HookEvent,
        callback: Callable[[HookInput], Awaitable[HookResult] | HookResult],
        tool_pattern: str = "",
    ) -> None:
        """Register a Python callable hook."""
        self.register(HookConfig(
            event=event,
            callback=callback,
            tool_pattern=tool_pattern,
        ))

    def register_command(
        self,
        event: HookEvent,
        command: str,
        tool_pattern: str = "",
    ) -> None:
        """Register a shell command hook."""
        self.register(HookConfig(
            event=event,
            command=command,
            tool_pattern=tool_pattern,
        ))

    def _matches_tool(self, pattern: str, tool_name: str, tool_input: dict[str, Any]) -> bool:
        """Check if a hook pattern matches a tool call."""
        if not pattern:
            return True  # No pattern = match all

        import fnmatch

        # Pattern with content: "Bash(git *)"
        if "(" in pattern and pattern.endswith(")"):
            tool_pat, content_pat = pattern[:-1].split("(", 1)
            if not fnmatch.fnmatch(tool_name, tool_pat):
                return False
            # Match against command content for Bash
            command = tool_input.get("command", "")
            return fnmatch.fnmatch(command, content_pat)

        # Simple tool name pattern: "Bash", "mcp__*"
        return fnmatch.fnmatch(tool_name, pattern)

    async def run_hooks(
        self, event: HookEvent, hook_input: HookInput
    ) -> HookResult:
        """Run all hooks for an event. Returns merged result.

        Priority: deny > ask > allow > passthrough (like Claude Code).
        """
        hooks = self._hooks.get(event, [])
        if not hooks:
            return HookResult()

        merged = HookResult()

        for config in hooks:
            if not self._matches_tool(config.tool_pattern, hook_input.tool_name, hook_input.tool_input):
                continue

            try:
                if config.callback:
                    result = config.callback(hook_input)
                    if asyncio.iscoroutine(result):
                        result = await asyncio.wait_for(result, timeout=config.timeout)
                elif config.command:
                    result = await self._run_command_hook(config.command, hook_input, config.timeout)
                else:
                    continue

                # Merge results (deny > ask > allow)
                if not result.should_continue:
                    merged.should_continue = False
                    merged.stop_reason = result.stop_reason
                if result.permission_decision == "deny":
                    merged.permission_decision = "deny"
                elif result.permission_decision == "ask" and merged.permission_decision != "deny":
                    merged.permission_decision = "ask"
                elif result.permission_decision == "allow" and not merged.permission_decision:
                    merged.permission_decision = "allow"
                if result.updated_input is not None:
                    merged.updated_input = result.updated_input
                if result.additional_context:
                    merged.additional_context += ("\n" if merged.additional_context else "") + result.additional_context

            except asyncio.TimeoutError:
                logger.warning("Hook timed out: %s", config.command or "callback")
            except Exception as e:
                logger.error("Hook failed: %s", e)

        return merged

    async def _run_command_hook(
        self, command: str, hook_input: HookInput, timeout: float
    ) -> HookResult:
        """Execute a shell command hook with JSON stdin/stdout protocol."""
        input_json = json.dumps({
            "hook_event_name": hook_input.hook_event_name,
            "tool_name": hook_input.tool_name,
            "tool_input": hook_input.tool_input,
            "session_id": hook_input.session_id,
            "cwd": hook_input.cwd,
        })

        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input_json.encode()), timeout=timeout
        )

        if proc.returncode != 0:
            logger.warning("Hook command exited %d: %s", proc.returncode, stderr.decode()[:200])
            return HookResult()

        if not stdout.strip():
            return HookResult()

        try:
            data = json.loads(stdout.decode())
        except json.JSONDecodeError:
            return HookResult()

        specific = data.get("hookSpecificOutput", {})
        return HookResult(
            should_continue=data.get("continue", True),
            stop_reason=data.get("stopReason", ""),
            permission_decision=specific.get("permissionDecision", ""),
            updated_input=specific.get("updatedInput"),
            additional_context=specific.get("additionalContext", ""),
        )
