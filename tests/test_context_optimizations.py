"""Tests for the 4 context management optimizations.

1. Structured compact prompt + extract_summary
2. Tool-type-aware microcompact (is_compactable + keep recent N)
3. Autocompact circuit breaker (3 consecutive failures)
4. Single tool call fast path in run_tools
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from calcifer import Agent, CalciferConfig, Message, ToolCall, Usage, tool
from calcifer.services.compact.context import (
    ContextManager,
    COMPACT_SYSTEM_PROMPT,
    COMPACTABLE_TOOLS,
    MICROCOMPACT_KEEP_RECENT,
    MICROCOMPACT_THRESHOLD,
)
from calcifer.services.tools.orchestrator import run_tools, execute_tool_call
from calcifer.types.tools import ToolContext


@tool(name="add", description="Add two numbers")
def add_tool(a: int, b: int) -> str:
    return str(a + b)


# ===================================================================
# 1. Structured Compact Prompt + extract_summary
# ===================================================================

class TestStructuredCompactPrompt:

    def test_prompt_has_9_sections(self):
        """The system prompt requires 9 structured sections."""
        for section in [
            "Primary Request and Intent",
            "Key Technical Concepts",
            "Files and Code Sections",
            "Errors and Fixes",
            "Problem Solving",
            "All User Messages",
            "Pending Tasks",
            "Current Work",
            "Optional Next Step",
        ]:
            assert section in COMPACT_SYSTEM_PROMPT, f"Missing section: {section}"

    def test_prompt_requires_analysis_tags(self):
        assert "<analysis>" in COMPACT_SYSTEM_PROMPT
        assert "<summary>" in COMPACT_SYSTEM_PROMPT

    def test_prompt_preserves_user_messages(self):
        """The prompt explicitly says to never merge user messages."""
        assert "never merge" in COMPACT_SYSTEM_PROMPT.lower() or "Never merge" in COMPACT_SYSTEM_PROMPT

    def test_extract_summary_with_tags(self):
        raw = """<analysis>
Some analysis here about the conversation.
</analysis>
<summary>
## 1. Primary Request
User wants to build a CLI tool.

## 6. All User Messages
- "Build me a CLI"
- "Add --verbose flag"
</summary>"""
        result = ContextManager.extract_summary(raw)
        assert "Primary Request" in result
        assert "Build me a CLI" in result
        assert "<analysis>" not in result
        assert "<summary>" not in result

    def test_extract_summary_without_tags(self):
        """Falls back to raw text when no tags present."""
        raw = "Just a plain summary without any XML tags."
        result = ContextManager.extract_summary(raw)
        assert result == raw

    def test_extract_summary_strips_whitespace(self):
        raw = "<summary>  \n  content here  \n  </summary>"
        result = ContextManager.extract_summary(raw)
        assert result == "content here"

    def test_build_compact_prompt_truncates_tool_results(self):
        """Long tool results are truncated in the compact prompt to keep it manageable."""
        mgr = ContextManager()
        msgs = [
            Message(role="user", content="Read the file"),
            Message(role="assistant", tool_calls=[
                ToolCall(id="tc_1", function_name="file_read", arguments='{}'),
            ]),
            Message(role="tool", content="x" * 5000, tool_call_id="tc_1"),
        ]
        prompt = mgr.build_compact_prompt(msgs)
        # The tool result in the prompt should be truncated
        user_msg = prompt[1]
        assert len(user_msg.content) < 5000 + 500  # Much less than original


# ===================================================================
# 2. Tool-type-aware Microcompact
# ===================================================================

class TestToolTypeAwareMicrocompact:

    def _make_tool_conversation(self, tool_name: str, count: int) -> list[Message]:
        """Create a conversation with N tool calls of the given tool."""
        msgs: list[Message] = []
        for i in range(count):
            msgs.append(Message(
                role="assistant",
                tool_calls=[ToolCall(id=f"tc_{i}", function_name=tool_name, arguments="{}")],
            ))
            msgs.append(Message(
                role="tool", content=f"result_{i}_" + "x" * 100,
                tool_call_id=f"tc_{i}",
            ))
        return msgs

    def test_compactable_tools_defined(self):
        """The known compactable tools set is correct."""
        assert "bash" in COMPACTABLE_TOOLS
        assert "file_read" in COMPACTABLE_TOOLS
        assert "grep" in COMPACTABLE_TOOLS
        assert "glob" in COMPACTABLE_TOOLS

    def test_keep_recent_n(self):
        """The most recent N compactable results are preserved."""
        assert MICROCOMPACT_KEEP_RECENT == 5

    def test_old_compactable_results_cleared(self):
        """Results from compactable tools beyond keep_recent are cleared."""
        mgr = ContextManager()
        # Create 8 bash tool results (3 should be cleared, 5 kept)
        msgs = self._make_tool_conversation("bash", 8)
        result = mgr.microcompact(msgs)

        tool_msgs = [m for m in result if m.role == "tool"]
        cleared = [m for m in tool_msgs if m.content == "[Old tool result content cleared]"]
        kept = [m for m in tool_msgs if "result_" in m.content]
        assert len(cleared) == 3  # 8 - 5 = 3 cleared
        assert len(kept) == 5  # Most recent 5 kept

    def test_non_compactable_tool_not_cleared(self):
        """MCP and unknown tools are never type-cleared, only size-truncated."""
        mgr = ContextManager()
        # Create 10 results from an "mcp_tool" (not in COMPACTABLE_TOOLS)
        msgs = self._make_tool_conversation("mcp__server__custom_tool", 10)
        result = mgr.microcompact(msgs)

        tool_msgs = [m for m in result if m.role == "tool"]
        cleared = [m for m in tool_msgs if "cleared" in m.content]
        assert len(cleared) == 0  # None cleared (not compactable)

    def test_size_fallback_still_works(self):
        """Results exceeding size threshold are still truncated regardless of tool type."""
        mgr = ContextManager()
        msgs = [
            Message(role="assistant", tool_calls=[
                ToolCall(id="tc_1", function_name="mcp__custom", arguments="{}"),
            ]),
            Message(role="tool", content="x" * 100_000, tool_call_id="tc_1"),
        ]
        result = mgr.microcompact(msgs)
        tool_msg = [m for m in result if m.role == "tool"][0]
        assert len(tool_msg.content) < 100_000
        assert "microcompact" in tool_msg.content

    def test_mixed_tools(self):
        """Compactable and non-compactable tools coexist correctly."""
        mgr = ContextManager()
        msgs = []
        # 6 bash calls + 3 mcp calls
        for i in range(6):
            msgs.append(Message(role="assistant", tool_calls=[
                ToolCall(id=f"bash_{i}", function_name="bash", arguments="{}"),
            ]))
            msgs.append(Message(role="tool", content=f"bash_result_{i}", tool_call_id=f"bash_{i}"))
        for i in range(3):
            msgs.append(Message(role="assistant", tool_calls=[
                ToolCall(id=f"mcp_{i}", function_name="mcp__srv__tool", arguments="{}"),
            ]))
            msgs.append(Message(role="tool", content=f"mcp_result_{i}", tool_call_id=f"mcp_{i}"))

        result = mgr.microcompact(msgs)
        tool_msgs = [m for m in result if m.role == "tool"]

        # Bash: 6 total, keep 5, clear 1
        bash_cleared = [m for m in tool_msgs if m.content == "[Old tool result content cleared]"]
        assert len(bash_cleared) == 1

        # MCP: all 3 kept (not compactable)
        mcp_kept = [m for m in tool_msgs if "mcp_result" in m.content]
        assert len(mcp_kept) == 3

    def test_few_results_no_clearing(self):
        """Fewer than keep_recent results → nothing cleared."""
        mgr = ContextManager()
        msgs = self._make_tool_conversation("bash", 3)
        result = mgr.microcompact(msgs)
        tool_msgs = [m for m in result if m.role == "tool"]
        cleared = [m for m in tool_msgs if "cleared" in m.content]
        assert len(cleared) == 0

    def test_already_compacted_not_double_cleared(self):
        """Messages already marked as microcompacted are skipped."""
        mgr = ContextManager()
        msgs = [
            Message(role="assistant", tool_calls=[
                ToolCall(id="tc_1", function_name="bash", arguments="{}"),
            ]),
            Message(role="tool", content="[Old tool result content cleared]",
                    tool_call_id="tc_1", metadata={"microcompacted": True}),
        ]
        # Add more to exceed keep_recent
        msgs.extend(self._make_tool_conversation("bash", 6))
        result = mgr.microcompact(msgs)
        # The already-cleared one should not be counted in compactable_indices
        # (it's skipped by the microcompacted check)
        first_tool = [m for m in result if m.role == "tool"][0]
        assert first_tool.content == "[Old tool result content cleared]"

    def test_compactable_flag_on_builtin_tools(self):
        """Built-in tools have is_compactable correctly set."""
        from calcifer.tools import BashTool, FileReadTool, GlobTool, GrepTool
        from calcifer.tools import FileWriteTool, FileEditTool

        assert BashTool().is_compactable is True
        assert FileReadTool().is_compactable is True
        assert GlobTool().is_compactable is True
        assert GrepTool().is_compactable is True
        # Write/Edit results are short, not compactable
        assert FileWriteTool().is_compactable is False
        assert FileEditTool().is_compactable is False


# ===================================================================
# 3. Autocompact Circuit Breaker
# ===================================================================

class TestAutocompactCircuitBreaker:

    @pytest.mark.asyncio
    async def test_breaker_opens_after_3_failures(self):
        """After 3 consecutive autocompact failures, stop trying."""
        config = CalciferConfig(api_key="test", max_context_tokens=100)
        agent = Agent(config=config)

        # Force needs_compaction to return True
        agent._context_mgr._api_reported_tokens = 95

        # Make LLM call always fail
        agent._provider.chat_completion = AsyncMock(side_effect=RuntimeError("LLM down"))

        msgs = [Message(role="user", content="test")]

        # First 3 calls: attempts and fails, counter increments
        for i in range(3):
            result = await agent._maybe_compact(msgs)
            assert agent._autocompact_failures == i + 1

        # 4th call: circuit breaker open, skips LLM call entirely
        agent._provider.chat_completion.reset_mock()
        result = await agent._maybe_compact(msgs)
        agent._provider.chat_completion.assert_not_called()
        assert agent._autocompact_failures == 3  # Unchanged
        await agent.close()

    @pytest.mark.asyncio
    async def test_breaker_resets_on_success(self):
        """Successful autocompact resets the failure counter."""
        config = CalciferConfig(api_key="test", max_context_tokens=100)
        agent = Agent(config=config)
        agent._context_mgr._api_reported_tokens = 95
        agent._autocompact_failures = 2  # Almost at breaker limit

        # Successful compact
        agent._provider.chat_completion = AsyncMock(return_value=(
            Message(role="assistant", content="<summary>Summary here</summary>"),
            Usage(),
        ))

        msgs = [
            Message(role="system", content="sys"),
            Message(role="user", content="test"),
        ]
        await agent._maybe_compact(msgs)
        assert agent._autocompact_failures == 0  # Reset!
        await agent.close()

    def test_initial_failure_count_is_zero(self):
        agent = Agent(api_key="test")
        assert agent._autocompact_failures == 0
        asyncio.get_event_loop().run_until_complete(agent.close())


# ===================================================================
# 4. Single Tool Call Fast Path
# ===================================================================

class TestSingleToolFastPath:

    @pytest.mark.asyncio
    async def test_single_tool_fast_path(self):
        """Single tool call bypasses partition logic."""
        tools_map = {"add": add_tool}
        tc = ToolCall(id="tc_1", function_name="add", arguments='{"a": 3, "b": 7}')
        ctx = ToolContext()

        results = await run_tools([tc], tools_map, ctx)
        assert len(results) == 1
        assert results[0].content == "10"
        assert results[0].tool_call_id == "tc_1"

    @pytest.mark.asyncio
    async def test_multi_tool_still_uses_partitioning(self):
        """Multiple tool calls still go through partition logic."""
        tools_map = {"add": add_tool}
        tcs = [
            ToolCall(id="tc_1", function_name="add", arguments='{"a": 1, "b": 2}'),
            ToolCall(id="tc_2", function_name="add", arguments='{"a": 3, "b": 4}'),
        ]
        ctx = ToolContext()

        results = await run_tools(tcs, tools_map, ctx)
        assert len(results) == 2
        assert results[0].content == "3"
        assert results[1].content == "7"

    @pytest.mark.asyncio
    async def test_single_unknown_tool_fast_path(self):
        """Fast path also handles unknown tools correctly."""
        tc = ToolCall(id="tc_1", function_name="nonexistent", arguments='{}')
        ctx = ToolContext()

        results = await run_tools([tc], {}, ctx)
        assert len(results) == 1
        assert "No such tool" in results[0].content

    @pytest.mark.asyncio
    async def test_empty_tool_calls(self):
        """Zero tool calls returns empty list."""
        ctx = ToolContext()
        results = await run_tools([], {}, ctx)
        assert results == []
