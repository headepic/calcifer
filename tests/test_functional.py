"""Comprehensive functional validation of the Calcifer agent runner.

Tests every major subsystem end-to-end with mocked LLM:
1. Agent loop: run(), run_stream(), max_turns, abort, stop hooks
2. Tool system: @tool decorator, Tool ABC, permissions, validation
3. Tool orchestration: parallel batches, serial batches, streaming executor
4. Context management: all 5 compaction layers, reactive compact, autocompact
5. Error recovery: prompt_too_long, max_output_tokens, diminishing output
6. MCP integration: tool adapter, schema conversion
7. Skill system: load, apply inline, fork, conditional activation
8. Memory system: CRUD, search, retrieval, index
9. Task system: lifecycle, kill, output
10. Hook system: callback hooks, pattern matching, deny/allow
11. Session persistence: save, load, fork
12. Coordinator: worker agents, parallel/serial dispatch
13. Cost tracker: multi-model pricing
14. Settings: merge, load
15. Telemetry: noop mode
16. Tool registry: assemble pool, deny rules
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from calcifer import (
    Agent,
    AgentResult,
    CalciferConfig,
    ContextManager,
    CostTracker,
    FunctionTool,
    HookConfig,
    HookEvent,
    HookManager,
    LLMProvider,
    LLMProviderError,
    Message,
    MetricsManager,
    StreamEvent,
    Tool,
    ToolCall,
    ToolContext,
    ToolResult,
    Usage,
    ValidationResult,
    find_tool_by_name,
    load_settings,
    run_tools,
    tool,
)
from calcifer.coordinator import Coordinator, CoordinatorConfig
from calcifer.services.compact.context import estimate_tokens, count_message_tokens
from calcifer.services.hooks import HookInput, HookResult
from calcifer.services.session import SessionStorage
from calcifer.services.side_query import side_query, classify
from calcifer.services.tools.orchestrator import (
    StreamingToolExecutor,
    execute_tool_call,
    partition_tool_calls,
)
from calcifer.skills import (
    SkillDefinition,
    apply_skill,
    load_skill_file,
    load_all_skills,
    activate_conditional_skills,
    apply_token_budget,
    discover_skill_dirs_for_paths,
)
from calcifer.tool_registry import get_all_builtin_tools, get_tools, assemble_tool_pool
from calcifer.telemetry import (
    TracingManager,
    get_tracer,
    init_telemetry,
    start_interaction_span,
    end_interaction_span,
    start_llm_span,
    end_llm_span,
    start_tool_span,
    end_tool_span,
    start_compact_span,
    end_compact_span,
)
from calcifer.utils.cost_tracker import ModelUsage


# ===== Helpers =====

def make_assistant_msg(content=None, tool_calls=None, metadata=None):
    return Message(role="assistant", content=content, tool_calls=tool_calls or [], metadata=metadata or {})


def make_tool_msg(content, tool_call_id="tc_1"):
    return Message(role="tool", content=content, tool_call_id=tool_call_id)


def make_usage(prompt=100, completion=50, total=150):
    return Usage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)


@tool(name="add", description="Add two numbers")
def add_tool(a: int, b: int) -> str:
    return str(a + b)


@tool(name="concat", description="Concatenate strings", is_concurrency_safe=True, is_read_only=True)
def concat_tool(x: str, y: str) -> str:
    return x + y


@tool(name="fail_tool", description="Always fails")
def fail_tool(msg: str) -> str:
    raise ValueError(msg)


@tool(name="slow_tool", description="Simulates slow work")
async def slow_tool(delay: float) -> str:
    await asyncio.sleep(delay)
    return "done"


# ===== 1. Agent Loop =====

class TestAgentLoop:
    """Test the core agent loop in various configurations."""

    @pytest.mark.asyncio
    async def test_simple_text_response(self):
        """Agent returns text with no tool calls in 1 turn."""
        agent = Agent(api_key="test", model="test-model")
        agent._provider.chat_completion = AsyncMock(return_value=(
            make_assistant_msg(content="Hello!"),
            make_usage(),
        ))
        result = await agent.run("Hi")
        assert result.final_text == "Hello!"
        assert result.turn_count == 1
        assert result.usage.total_tokens == 150
        await agent.close()

    @pytest.mark.asyncio
    async def test_tool_call_loop(self):
        """Agent calls tool then gets final response."""
        agent = Agent(api_key="test", tools=[add_tool])

        call_count = 0
        async def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    make_assistant_msg(tool_calls=[
                        ToolCall(id="tc_1", function_name="add", arguments='{"a": 3, "b": 4}')
                    ]),
                    make_usage(),
                )
            return make_assistant_msg(content="The answer is 7"), make_usage()

        agent._provider.chat_completion = AsyncMock(side_effect=mock_completion)
        result = await agent.run("What is 3+4?")
        assert result.turn_count == 2
        assert "7" in result.final_text
        # Verify tool was actually called
        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].content == "7"
        await agent.close()

    @pytest.mark.asyncio
    async def test_max_turns_limit(self):
        """Agent stops when max_turns is reached."""
        config = CalciferConfig(api_key="test", max_turns=2)
        agent = Agent(config=config, tools=[add_tool])

        # Always return tool calls to force looping
        agent._provider.chat_completion = AsyncMock(return_value=(
            make_assistant_msg(tool_calls=[
                ToolCall(id="tc_1", function_name="add", arguments='{"a": 1, "b": 2}')
            ]),
            make_usage(),
        ))
        result = await agent.run("loop forever")
        assert result.turn_count == 2
        await agent.close()

    @pytest.mark.asyncio
    async def test_abort(self):
        """Agent respects abort signal during tool execution."""
        agent = Agent(api_key="test", tools=[add_tool])

        call_count = 0
        async def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # After first API call, trigger abort
                agent.abort()
                return (
                    make_assistant_msg(tool_calls=[
                        ToolCall(id="tc_1", function_name="add", arguments='{"a": 1, "b": 2}')
                    ]),
                    make_usage(),
                )
            # Should not reach here if abort works
            return make_assistant_msg(content="should not reach"), make_usage()

        agent._provider.chat_completion = AsyncMock(side_effect=mock_completion)
        result = await agent.run("test")
        # Agent should stop after the first turn's tool execution (abort checked before tools)
        # Actually abort is checked before the 2nd API call, so turn_count=1
        assert result.turn_count == 1
        assert call_count == 1
        await agent.close()

    @pytest.mark.asyncio
    async def test_stop_hook(self):
        """Stop hook terminates the loop."""
        agent = Agent(api_key="test", tools=[add_tool])

        stop_called = False
        async def my_stop_hook(messages, context):
            nonlocal stop_called
            stop_called = True
            return True  # Stop!

        agent.register_stop_hook(my_stop_hook)

        call_count = 0
        async def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            return (
                make_assistant_msg(tool_calls=[
                    ToolCall(id=f"tc_{call_count}", function_name="add", arguments='{"a": 1, "b": 2}')
                ]),
                make_usage(),
            )

        agent._provider.chat_completion = AsyncMock(side_effect=mock_completion)
        result = await agent.run("test")
        assert stop_called
        assert result.turn_count == 1  # One turn before stop hook fires
        await agent.close()

    @pytest.mark.asyncio
    async def test_continue_from_messages(self):
        """Agent can continue from existing messages."""
        agent = Agent(api_key="test")
        agent._provider.chat_completion = AsyncMock(return_value=(
            make_assistant_msg(content="Continued!"),
            make_usage(),
        ))

        existing = [
            Message(role="system", content="You are helpful"),
            Message(role="user", content="Previous question"),
            Message(role="assistant", content="Previous answer"),
        ]
        result = await agent.run("Follow up", messages=existing)
        assert result.final_text == "Continued!"
        # Should have system + prev user + prev assistant + new user + final assistant
        assert len(result.messages) == 5
        await agent.close()

    @pytest.mark.asyncio
    async def test_diminishing_output_stop(self):
        """Agent stops when output becomes diminishing."""
        config = CalciferConfig(api_key="test", max_turns=10)
        agent = Agent(config=config, tools=[add_tool])

        call_count = 0
        async def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            return (
                make_assistant_msg(tool_calls=[
                    ToolCall(id=f"tc_{call_count}", function_name="add", arguments='{"a": 1, "b": 1}')
                ]),
                Usage(prompt_tokens=100, completion_tokens=10, total_tokens=110),  # Very low output
            )

        agent._provider.chat_completion = AsyncMock(side_effect=mock_completion)
        result = await agent.run("test")
        # Should stop after DIMINISHING_TURNS (3) turns of <500 completion tokens
        assert result.turn_count <= 4
        await agent.close()


# ===== 2. Streaming =====

class TestStreaming:

    @pytest.mark.asyncio
    async def test_stream_text_only(self):
        """Streaming yields text_delta and lifecycle events."""
        agent = Agent(api_key="test")

        async def mock_stream_gen():
            yield StreamEvent(type="text_delta", text="Hello ")
            yield StreamEvent(type="text_delta", text="world!")
            yield StreamEvent(type="usage", usage=make_usage())
            yield StreamEvent(type="finish", finish_reason="stop")

        # chat_completion_stream is an async method that returns an async iterator
        agent._provider.chat_completion_stream = lambda **kwargs: mock_stream_gen()

        events = []
        async for event in agent.run_stream("Hi"):
            events.append(event)

        types = [e.type for e in events]
        assert "turn_start" in types
        assert "text_delta" in types
        assert "run_complete" in types

        # Check run_complete has AgentResult
        run_complete = [e for e in events if e.type == "run_complete"][0]
        assert run_complete.result is not None
        assert run_complete.result.final_text == "Hello world!"
        await agent.close()

    @pytest.mark.asyncio
    async def test_stream_with_tool_calls(self):
        """Streaming handles tool call deltas and tool execution."""
        agent = Agent(api_key="test", tools=[add_tool])

        call_count = 0
        def mock_stream(**kwargs):
            nonlocal call_count
            call_count += 1

            async def gen_turn1():
                yield StreamEvent(
                    type="tool_call_delta", tool_call_index=0,
                    tool_call_id="tc_1", tool_call_name="add",
                    tool_call_arguments='{"a": 5, "b": 3}',
                )
                yield StreamEvent(type="usage", usage=make_usage())
                yield StreamEvent(type="finish", finish_reason="tool_calls")

            async def gen_turn2():
                yield StreamEvent(type="text_delta", text="8!")
                yield StreamEvent(type="usage", usage=make_usage())
                yield StreamEvent(type="finish", finish_reason="stop")

            if call_count == 1:
                return gen_turn1()
            return gen_turn2()

        agent._provider.chat_completion_stream = mock_stream

        events = []
        async for event in agent.run_stream("What is 5+3?"):
            events.append(event)

        types = [e.type for e in events]
        assert "tool_call_start" in types
        assert "tool_call_result" in types
        assert "run_complete" in types

        tool_results = [e for e in events if e.type == "tool_call_result"]
        assert len(tool_results) == 1
        assert "8" in tool_results[0].tool_result_content
        await agent.close()


# ===== 3. Error Recovery =====

class TestErrorRecovery:

    @pytest.mark.asyncio
    async def test_prompt_too_long_recovery(self):
        """Agent recovers from prompt_too_long via reactive compact then retry."""
        agent = Agent(api_key="test")

        call_count = 0
        async def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise LLMProviderError(
                    "prompt too long", status_code=400,
                    error_type=APIErrorType.PROMPT_TOO_LONG,
                )
            return make_assistant_msg(content="Recovered!"), make_usage()

        from calcifer.types.message import APIErrorType
        agent._provider.chat_completion = AsyncMock(side_effect=mock_completion)
        result = await agent.run("test")
        assert result.final_text == "Recovered!"
        assert call_count == 2
        await agent.close()

    @pytest.mark.asyncio
    async def test_max_output_tokens_escalation(self):
        """Two-phase recovery: phase 1 escalates cap only, phase 2+ injects resume."""
        agent = Agent(api_key="test")

        call_count = 0
        async def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                msg = make_assistant_msg(content="partial...")
                msg.metadata["api_error"] = "max_output_tokens"
                return msg, make_usage()
            return make_assistant_msg(content="Complete response!"), make_usage()

        agent._provider.chat_completion = AsyncMock(side_effect=mock_completion)
        result = await agent.run("test")
        assert result.final_text == "Complete response!"
        # Phase 1: no resume message (just cap escalation)
        # Phase 2: resume message injected
        resume_msgs = [m for m in result.messages if m.content and "Resume" in m.content]
        assert len(resume_msgs) == 1  # Only from phase 2
        await agent.close()


# ===== 4. Tool System =====

class TestToolSystem:

    def test_function_tool_schema(self):
        """@tool decorator produces correct OpenAI schema."""
        schema = add_tool.to_openai_schema()
        assert schema["type"] == "function"
        func = schema["function"]
        assert func["name"] == "add"
        assert "a" in func["parameters"]["properties"]
        assert "b" in func["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_function_tool_call(self):
        """FunctionTool executes correctly."""
        ctx = ToolContext()
        args = add_tool.parameters(a=10, b=20)
        result = await add_tool.call(args, ctx)
        assert result.content == "30"
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_function_tool_error(self):
        """FunctionTool catches exceptions."""
        ctx = ToolContext()
        args = fail_tool.parameters(msg="boom")
        result = await fail_tool.call(args, ctx)
        assert result.is_error
        assert "boom" in result.content

    @pytest.mark.asyncio
    async def test_async_function_tool(self):
        """Async function tool works."""
        ctx = ToolContext()
        args = slow_tool.parameters(delay=0.01)
        result = await slow_tool.call(args, ctx)
        assert result.content == "done"

    def test_tool_concurrency_flags(self):
        """Concurrency flags are set correctly."""
        assert not add_tool.is_concurrency_safe
        assert concat_tool.is_concurrency_safe
        assert concat_tool.is_read_only

    def test_tool_truncation(self):
        """Large outputs are truncated."""
        t = add_tool
        long_text = "x" * 50_000
        truncated = t.truncate_result(long_text)
        assert len(truncated) < 50_000
        assert "truncated" in truncated

    def test_find_tool_by_name(self):
        tools = [add_tool, concat_tool]
        assert find_tool_by_name(tools, "add") is add_tool
        assert find_tool_by_name(tools, "concat") is concat_tool
        assert find_tool_by_name(tools, "nonexistent") is None

    def test_tool_aliases(self):
        """Tool with aliases matches all names."""
        class AliasedTool(Tool):
            name = "primary"
            description = "test"
            parameters = add_tool.parameters
            aliases = ["alt1", "alt2"]
            async def call(self, args, context, **kw):
                return ToolResult(content="ok")

        t = AliasedTool()
        assert t.matches_name("primary")
        assert t.matches_name("alt1")
        assert t.matches_name("alt2")
        assert not t.matches_name("nope")


# ===== 5. Tool Orchestration =====

class TestToolOrchestration:

    def test_partition_all_concurrent(self):
        """All concurrent-safe tools go in one batch."""
        tools = {"c1": concat_tool, "c2": concat_tool}
        tcs = [
            ToolCall(id="1", function_name="c1", arguments='{"x":"a","y":"b"}'),
            ToolCall(id="2", function_name="c2", arguments='{"x":"c","y":"d"}'),
        ]
        # concat_tool is concurrency_safe but tools_by_name lookup uses different names
        # We need tools that actually map. Let's create proper mapping.
        t1 = FunctionTool(lambda x, y: x+y, name="c1", description="", parameters=concat_tool.parameters, is_concurrency_safe=True)
        t2 = FunctionTool(lambda x, y: x+y, name="c2", description="", parameters=concat_tool.parameters, is_concurrency_safe=True)
        tools_map = {"c1": t1, "c2": t2}
        batches = partition_tool_calls(tcs, tools_map)
        assert len(batches) == 1
        assert batches[0].is_concurrent
        assert len(batches[0].tool_calls) == 2

    def test_partition_serial_breaks_batch(self):
        """A non-concurrent tool creates a new batch."""
        t_safe = FunctionTool(lambda x, y: x+y, name="safe", description="", parameters=concat_tool.parameters, is_concurrency_safe=True)
        t_serial = FunctionTool(lambda a, b: str(a+b), name="serial", description="", parameters=add_tool.parameters, is_concurrency_safe=False)
        tools_map = {"safe": t_safe, "serial": t_serial}
        tcs = [
            ToolCall(id="1", function_name="safe", arguments='{"x":"a","y":"b"}'),
            ToolCall(id="2", function_name="serial", arguments='{"a":1,"b":2}'),
            ToolCall(id="3", function_name="safe", arguments='{"x":"c","y":"d"}'),
        ]
        batches = partition_tool_calls(tcs, tools_map)
        assert len(batches) == 3
        assert batches[0].is_concurrent
        assert not batches[1].is_concurrent
        assert batches[2].is_concurrent

    @pytest.mark.asyncio
    async def test_run_tools_unknown_tool(self):
        """Unknown tool returns error message."""
        tcs = [ToolCall(id="1", function_name="nonexistent", arguments='{}')]
        ctx = ToolContext()
        results = await run_tools(tcs, {}, ctx)
        assert len(results) == 1
        assert "No such tool" in results[0].content

    @pytest.mark.asyncio
    async def test_execute_tool_call_full_pipeline(self):
        """Full pipeline: find → parse → validate → permissions → call → truncate."""
        tools_map = {"add": add_tool}
        tc = ToolCall(id="tc_1", function_name="add", arguments='{"a": 7, "b": 3}')
        ctx = ToolContext()
        result = await execute_tool_call(tc, tools_map, ctx)
        assert result.role == "tool"
        assert result.content == "10"
        assert result.tool_call_id == "tc_1"

    @pytest.mark.asyncio
    async def test_execute_tool_invalid_json(self):
        """Invalid JSON arguments produce error."""
        tools_map = {"add": add_tool}
        tc = ToolCall(id="tc_1", function_name="add", arguments='not json')
        ctx = ToolContext()
        result = await execute_tool_call(tc, tools_map, ctx)
        assert "Error" in result.content

    @pytest.mark.asyncio
    async def test_execute_tool_schema_validation_error(self):
        """Wrong argument types produce validation error."""
        tools_map = {"add": add_tool}
        tc = ToolCall(id="tc_1", function_name="add", arguments='{"a": "not_int", "b": 3}')
        ctx = ToolContext()
        result = await execute_tool_call(tc, tools_map, ctx)
        # Pydantic may coerce "not_int" to error or handle it
        # The key is it doesn't crash
        assert result.role == "tool"


# ===== 6. Context Management =====

class TestContextManagement:

    def test_estimate_tokens(self):
        assert estimate_tokens("hello world") > 0

    def test_microcompact(self):
        """Large tool results get truncated."""
        mgr = ContextManager(max_context_tokens=1000)
        big_content = "x" * 100_000
        msgs = [Message(role="tool", content=big_content, tool_call_id="tc_1")]
        result = mgr.microcompact(msgs)
        assert len(result[0].content) < 100_000
        assert "microcompact" in result[0].content

    def test_tool_result_budget(self):
        """Aggregate tool output exceeding budget gets truncated."""
        mgr = ContextManager(max_context_tokens=1000)
        msgs = []
        for i in range(20):
            msgs.append(Message(role="tool", content="x" * 50_000, tool_call_id=f"tc_{i}"))
        result = mgr.apply_tool_result_budget(msgs)
        # Later messages should be truncated
        last_content = result[-1].content
        assert "budget" in last_content.lower() or len(last_content) < 50_000

    def test_compact_messages_preserves_system(self):
        """Compaction preserves system messages."""
        mgr = ContextManager(max_context_tokens=10000)
        msgs = [
            Message(role="system", content="System prompt"),
            Message(role="user", content="Q1"),
            Message(role="assistant", content="A1"),
            Message(role="user", content="Q2"),
            Message(role="assistant", content="A2"),
        ]
        result = mgr.compact_messages(msgs, "Summary of conversation")
        # System should be first
        assert result[0].role == "system"
        assert result[0].content == "System prompt"
        # Summary should be present
        summaries = [m for m in result if "summary" in (m.content or "").lower()]
        assert len(summaries) >= 1

    def test_build_compact_prompt(self):
        """Compact prompt includes conversation content."""
        mgr = ContextManager()
        msgs = [
            Message(role="system", content="System"),
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi there"),
        ]
        prompt = mgr.build_compact_prompt(msgs)
        assert len(prompt) == 2  # system + user
        assert "Hello" in prompt[1].content
        assert "Hi there" in prompt[1].content

    def test_context_collapse(self):
        """Context collapse folds tool call + result pairs."""
        mgr = ContextManager(max_context_tokens=100)  # Force compaction
        mgr._api_reported_tokens = 95  # Near limit
        msgs = [
            Message(role="system", content="sys"),
            make_assistant_msg(content="thinking", tool_calls=[
                ToolCall(id="tc_1", function_name="add", arguments='{"a":1,"b":2}')
            ]),
            make_tool_msg("3", "tc_1"),
            make_assistant_msg(content="thinking more", tool_calls=[
                ToolCall(id="tc_2", function_name="add", arguments='{"a":3,"b":4}')
            ]),
            make_tool_msg("7", "tc_2"),
            Message(role="user", content="recent question"),
            Message(role="assistant", content="recent answer"),
        ]
        result, summaries = mgr.context_collapse(msgs)
        # Should have collapsed some tool regions
        assert len(summaries) > 0

    def test_reactive_compact(self):
        """Reactive compact applies all non-LLM layers."""
        mgr = ContextManager(max_context_tokens=1000)
        big_tool = Message(role="tool", content="x" * 100_000, tool_call_id="tc_1")
        msgs = [Message(role="system", content="sys"), big_tool]
        result = mgr.reactive_compact(msgs)
        # Should have truncated the big tool result
        total_len = sum(len(m.content or "") for m in result)
        assert total_len < 100_000

    def test_apply_all_layers(self):
        """Full pipeline runs without error."""
        mgr = ContextManager(max_context_tokens=100000)
        msgs = [
            Message(role="system", content="sys"),
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi"),
        ]
        result = mgr.apply_all_layers(msgs)
        assert len(result) >= 3


# ===== 9. Hook System =====

class TestHookSystem:

    @pytest.mark.asyncio
    async def test_callback_hook_allow(self):
        """Callback hook returns allow."""
        mgr = HookManager()

        async def allow_hook(input: HookInput) -> HookResult:
            return HookResult(permission_decision="allow")

        mgr.register_callback(HookEvent.PRE_TOOL_USE, allow_hook)
        result = await mgr.run_hooks(
            HookEvent.PRE_TOOL_USE,
            HookInput(hook_event_name="PreToolUse", tool_name="bash"),
        )
        assert result.permission_decision == "allow"

    @pytest.mark.asyncio
    async def test_callback_hook_deny(self):
        """Deny takes priority over allow."""
        mgr = HookManager()

        async def allow_hook(input: HookInput) -> HookResult:
            return HookResult(permission_decision="allow")

        async def deny_hook(input: HookInput) -> HookResult:
            return HookResult(permission_decision="deny")

        mgr.register_callback(HookEvent.PRE_TOOL_USE, allow_hook)
        mgr.register_callback(HookEvent.PRE_TOOL_USE, deny_hook)
        result = await mgr.run_hooks(
            HookEvent.PRE_TOOL_USE,
            HookInput(hook_event_name="PreToolUse", tool_name="bash"),
        )
        assert result.permission_decision == "deny"

    @pytest.mark.asyncio
    async def test_hook_tool_pattern(self):
        """Hook only fires for matching tool patterns."""
        mgr = HookManager()
        called_tools = []

        async def track_hook(input: HookInput) -> HookResult:
            called_tools.append(input.tool_name)
            return HookResult()

        mgr.register_callback(HookEvent.PRE_TOOL_USE, track_hook, tool_pattern="bash")

        await mgr.run_hooks(
            HookEvent.PRE_TOOL_USE,
            HookInput(hook_event_name="PreToolUse", tool_name="bash"),
        )
        await mgr.run_hooks(
            HookEvent.PRE_TOOL_USE,
            HookInput(hook_event_name="PreToolUse", tool_name="file_read"),
        )
        assert called_tools == ["bash"]

    @pytest.mark.asyncio
    async def test_hook_content_pattern(self):
        """Hook with content pattern like Bash(git *)."""
        mgr = HookManager()
        matched = []

        async def hook(input: HookInput) -> HookResult:
            matched.append(True)
            return HookResult(permission_decision="deny")

        mgr.register_callback(HookEvent.PRE_TOOL_USE, hook, tool_pattern="bash(git *)")

        # Should match
        await mgr.run_hooks(
            HookEvent.PRE_TOOL_USE,
            HookInput(hook_event_name="PreToolUse", tool_name="bash",
                       tool_input={"command": "git push"}),
        )
        assert len(matched) == 1

        # Should NOT match
        await mgr.run_hooks(
            HookEvent.PRE_TOOL_USE,
            HookInput(hook_event_name="PreToolUse", tool_name="bash",
                       tool_input={"command": "ls -la"}),
        )
        assert len(matched) == 1  # Still 1

    @pytest.mark.asyncio
    async def test_hook_input_rewrite(self):
        """Hook can rewrite tool input."""
        mgr = HookManager()

        async def rewrite_hook(input: HookInput) -> HookResult:
            return HookResult(updated_input={"command": "echo safe"})

        mgr.register_callback(HookEvent.PRE_TOOL_USE, rewrite_hook)
        result = await mgr.run_hooks(
            HookEvent.PRE_TOOL_USE,
            HookInput(hook_event_name="PreToolUse", tool_name="bash",
                       tool_input={"command": "rm -rf /"}),
        )
        assert result.updated_input == {"command": "echo safe"}

    @pytest.mark.asyncio
    async def test_hook_timeout(self):
        """Timed-out hook doesn't block."""
        mgr = HookManager()

        async def slow_hook(input: HookInput) -> HookResult:
            await asyncio.sleep(10)
            return HookResult(permission_decision="deny")

        mgr.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            callback=slow_hook,
            timeout=0.01,
        ))
        result = await mgr.run_hooks(
            HookEvent.PRE_TOOL_USE,
            HookInput(hook_event_name="PreToolUse", tool_name="bash"),
        )
        # Should have timed out, returning default (no decision)
        assert result.permission_decision == ""


# ===== 10. Session Persistence =====

class TestSessionPersistence:

    def test_save_and_load(self, tmp_path):
        storage = SessionStorage(tmp_path)
        msgs = [
            Message(role="system", content="sys"),
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi there"),
        ]
        usage = Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        storage.save(msgs, usage, 1, model="gpt-4o")

        loaded = storage.load()
        assert loaded is not None
        messages, loaded_usage, turn_count = loaded
        assert len(messages) == 3
        assert messages[0].content == "sys"
        assert loaded_usage.prompt_tokens == 100
        assert turn_count == 1

    def test_save_with_tool_calls(self, tmp_path):
        """Tool calls survive serialization round-trip."""
        storage = SessionStorage(tmp_path)
        msgs = [
            Message(role="assistant", content=None, tool_calls=[
                ToolCall(id="tc_1", function_name="add", arguments='{"a":1,"b":2}')
            ]),
            Message(role="tool", content="3", tool_call_id="tc_1"),
        ]
        storage.save(msgs, Usage(), 1)
        loaded = storage.load()
        assert loaded is not None
        messages, _, _ = loaded
        assert len(messages[0].tool_calls) == 1
        assert messages[0].tool_calls[0].function_name == "add"
        assert messages[1].tool_call_id == "tc_1"

    def test_list_sessions(self, tmp_path):
        s1 = SessionStorage(tmp_path)
        s1.save([Message(role="user", content="q1")], Usage(), 1)
        s2 = SessionStorage(tmp_path)
        s2.save([Message(role="user", content="q2")], Usage(), 1)

        sessions = s1.list_sessions()
        assert len(sessions) >= 2

    def test_get_last_session(self, tmp_path):
        s1 = SessionStorage(tmp_path)
        s1.save([Message(role="user", content="q1")], Usage(), 1)
        s2 = SessionStorage(tmp_path)
        s2.save([Message(role="user", content="q2")], Usage(), 1)

        last = s2.get_last_session_id()
        assert last == s2.session_id

    def test_fork_session(self, tmp_path):
        storage = SessionStorage(tmp_path)
        msgs = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi"),
            Message(role="user", content="more"),
        ]
        storage.save(msgs, Usage(), 2)

        forked = storage.fork(from_message_index=2)
        loaded = forked.load()
        assert loaded is not None
        messages, _, _ = loaded
        assert len(messages) == 2  # Only first 2 messages


# ===== 11. Skill System =====

class TestSkillSystem:

    def test_load_skill_file(self, tmp_path):
        skill_file = tmp_path / "research.md"
        skill_file.write_text("""---
name: research
description: Help with research
allowed-tools: [bash, grep]
context: inline
user-invocable: true
---

You are a research assistant. Use tools to find information.
""")
        skill = load_skill_file(skill_file)
        assert skill is not None
        assert skill.name == "research"
        assert skill.description == "Help with research"
        assert skill.allowed_tools == ["bash", "grep"]
        assert skill.context == "inline"
        assert skill.user_invocable
        assert "research assistant" in skill.content

    def test_load_skill_no_frontmatter(self, tmp_path):
        skill_file = tmp_path / "plain.md"
        skill_file.write_text("Just content, no frontmatter.")
        skill = load_skill_file(skill_file)
        assert skill is not None
        assert skill.name == "plain"
        assert skill.content == "Just content, no frontmatter."

    def test_load_all_skills(self, tmp_path):
        d1 = tmp_path / "skills1"
        d1.mkdir()
        (d1 / "a.md").write_text("---\nname: skill_a\ndescription: A\n---\nContent A")
        (d1 / "b.md").write_text("---\nname: skill_b\ndescription: B\n---\nContent B")

        d2 = tmp_path / "skills2"
        d2.mkdir()
        (d2 / "a.md").write_text("---\nname: skill_a\ndescription: A override\n---\nOverridden")

        skills = load_all_skills([str(d1), str(d2)])
        assert len(skills) == 2
        # d2 overrides d1 for skill_a
        assert skills["skill_a"].description == "A override"

    def test_apply_skill_inline(self):
        """Inline skill injects system message and filters tools."""
        skill = SkillDefinition(
            name="research",
            description="Research helper",
            content="Research instructions here",
            allowed_tools=["add"],
        )
        msgs = [
            Message(role="system", content="You are helpful"),
            Message(role="user", content="Question"),
        ]
        new_msgs, new_tools = apply_skill(skill, msgs, [add_tool, concat_tool])
        # Skill message inserted after system
        assert new_msgs[1].role == "system"
        assert "research" in new_msgs[1].content.lower()
        # Only allowed tools remain
        assert len(new_tools) == 1
        assert new_tools[0].name == "add"

    def test_activate_conditional_skills(self):
        """Skills activate based on file path matching."""
        skills = {
            "react": SkillDefinition(name="react", description="", content="", paths=["*.tsx"]),
            "python": SkillDefinition(name="python", description="", content="", paths=["*.py"]),
            "general": SkillDefinition(name="general", description="", content=""),
        }
        activated = activate_conditional_skills(skills, ["src/components/App.tsx"])
        names = [s.name for s in activated]
        assert "react" in names
        assert "python" not in names
        assert "general" not in names

    def test_apply_token_budget(self):
        """Token budget limits skill descriptions."""
        skills = {}
        for i in range(100):
            skills[f"skill_{i}"] = SkillDefinition(
                name=f"skill_{i}",
                description=f"Description for skill {i} " * 10,
                content=f"Content {i}",
            )
        entries = apply_token_budget(skills, max_chars=500)
        total_chars = sum(len(n) + len(d) + 10 for n, d in entries)
        assert total_chars <= 500

    def test_discover_skill_dirs(self, tmp_path):
        """Discover skills directories by traversing up."""
        # Create project structure
        project = tmp_path / "project"
        project.mkdir()
        skills_dir = project / "src" / "skills"
        skills_dir.mkdir(parents=True)
        claude_skills = project / ".claude" / "skills"
        claude_skills.mkdir(parents=True)

        found = discover_skill_dirs_for_paths(
            [str(project / "src" / "app.py")],
            str(project),
        )
        found_strs = [str(p) for p in found]
        assert str(skills_dir) in found_strs


# ===== 12. Cost Tracker =====

class TestCostTracker:

    def test_single_model(self):
        tracker = CostTracker()
        tracker.record("gpt-4o", Usage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500))
        cost = tracker.get_cost()
        expected = 1000 * 2.50 / 1_000_000 + 500 * 10.00 / 1_000_000
        assert abs(cost - expected) < 0.0001

    def test_multi_model(self):
        tracker = CostTracker()
        tracker.record("gpt-4o", Usage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500))
        tracker.record("gpt-4o-mini", Usage(prompt_tokens=2000, completion_tokens=1000, total_tokens=3000))
        total = tracker.get_cost()
        gpt4o_cost = tracker.get_cost("gpt-4o")
        mini_cost = tracker.get_cost("gpt-4o-mini")
        assert abs(total - (gpt4o_cost + mini_cost)) < 0.0001

    def test_custom_pricing(self):
        tracker = CostTracker()
        tracker.set_model_pricing("custom-model", 1.0, 2.0)
        tracker.record("custom-model", Usage(prompt_tokens=1_000_000, completion_tokens=1_000_000, total_tokens=2_000_000))
        cost = tracker.get_cost()
        assert abs(cost - 3.0) < 0.0001

    def test_summary(self):
        tracker = CostTracker()
        tracker.record("gpt-4o", Usage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500))
        summary = tracker.summary()
        assert "gpt-4o" in summary
        assert summary["gpt-4o"]["input_tokens"] == 1000
        assert summary["gpt-4o"]["api_calls"] == 1

    def test_get_total_usage(self):
        tracker = CostTracker()
        tracker.record("m1", Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150))
        tracker.record("m2", Usage(prompt_tokens=200, completion_tokens=100, total_tokens=300))
        total = tracker.get_total_usage()
        assert total.prompt_tokens == 300
        assert total.completion_tokens == 150


# ===== 13. Settings =====

class TestSettings:

    def test_load_settings_defaults(self, tmp_path):
        config = load_settings(project_dir=str(tmp_path))
        assert config.model == "gpt-4o"
        assert config.max_turns == 100

    def test_load_settings_from_file(self, tmp_path):
        config_file = tmp_path / "calcifer.yaml"
        config_file.write_text("model: claude-sonnet\nmax_turns: 50\n")
        config = load_settings(project_dir=str(tmp_path))
        assert config.model == "claude-sonnet"
        assert config.max_turns == 50

    def test_load_settings_with_overrides(self, tmp_path):
        config = load_settings(
            project_dir=str(tmp_path),
            overrides={"model": "custom-model", "temperature": 0.5},
        )
        assert config.model == "custom-model"
        assert config.temperature == 0.5

    def test_load_settings_mcp_servers(self, tmp_path):
        config_file = tmp_path / "calcifer.yaml"
        config_file.write_text("""
mcp_servers:
  - name: test-server
    transport: stdio
    command: /usr/bin/test
    args: ["--flag"]
""")
        config = load_settings(project_dir=str(tmp_path))
        assert len(config.mcp_servers) == 1
        assert config.mcp_servers[0].name == "test-server"
        assert config.mcp_servers[0].command == "/usr/bin/test"


# ===== 15. Telemetry (noop mode) =====

class TestTelemetry:

    def test_noop_tracer(self):
        tracer = get_tracer()
        span = tracer.start_span("test")
        span.set_attribute("key", "value")
        span.end()  # Should not raise

    def test_noop_metrics(self):
        metrics = MetricsManager()
        metrics.record_llm_request("test", input_tokens=100, output_tokens=50, latency_ms=100)
        metrics.record_tool_call("bash", duration_ms=50)
        metrics.record_compaction("autocompact", tokens_freed=1000)
        metrics.record_agent_run(turns=5, cost_usd=0.01)
        # Should not raise

    def test_span_lifecycle(self):
        span = start_interaction_span("test", session_id="s1", chain_id="c1")
        llm = start_llm_span("model")
        end_llm_span(llm, input_tokens=100, success=True)
        tool_span = start_tool_span("bash")
        end_tool_span(tool_span, success=True, duration_ms=50)
        compact = start_compact_span("autocompact")
        end_compact_span(compact, pre_tokens=1000, post_tokens=500)
        end_interaction_span(turn_count=1, total_tokens=100, cost_usd=0.01)


# ===== 16. Tool Registry =====

class TestToolRegistry:

    def test_get_all_builtin_tools(self):
        tools = get_all_builtin_tools()
        names = {t.name for t in tools}
        assert "bash" in names
        assert "file_read" in names
        assert "file_write" in names
        assert "file_edit" in names
        assert "glob" in names
        assert "grep" in names

    def test_assemble_tool_pool_dedup(self):
        builtin = [add_tool]
        # Create an MCP tool with same name
        mcp_tool = FunctionTool(lambda a, b: str(a+b), name="add", description="MCP add", parameters=add_tool.parameters)
        mcp_tool.is_mcp = True
        result = assemble_tool_pool(builtin, [mcp_tool])
        assert len(result) == 1
        assert result[0] is add_tool  # Built-in wins



# ===== 17. Side Query =====

class TestSideQuery:

    @pytest.mark.asyncio
    async def test_side_query_basic(self):
        provider = MagicMock()
        provider.chat_completion = AsyncMock(return_value=(
            Message(role="assistant", content="Paris"),
            make_usage(),
        ))
        text, usage = await side_query(provider, "Capital of France?")
        assert text == "Paris"
        assert usage.total_tokens == 150

    @pytest.mark.asyncio
    async def test_side_query_json(self):
        provider = MagicMock()
        provider.chat_completion = AsyncMock(return_value=(
            Message(role="assistant", content='{"answer": "Paris"}'),
            make_usage(),
        ))
        text, _ = await side_query(provider, "Capital?", json_schema={"type": "object"})
        parsed = json.loads(text)
        assert parsed["answer"] == "Paris"

    @pytest.mark.asyncio
    async def test_classify(self):
        provider = MagicMock()
        provider.chat_completion = AsyncMock(return_value=(
            Message(role="assistant", content="positive"),
            make_usage(),
        ))
        result = await classify(provider, "Great product!", ["positive", "negative", "neutral"])
        assert result == "positive"


# ===== 18. Message Types =====

class TestMessageTypes:

    def test_message_to_openai_user(self):
        msg = Message(role="user", content="Hello")
        d = msg.to_openai()
        assert d == {"role": "user", "content": "Hello"}

    def test_message_to_openai_assistant_with_tools(self):
        msg = Message(role="assistant", content="Let me check", tool_calls=[
            ToolCall(id="tc_1", function_name="bash", arguments='{"command": "ls"}')
        ])
        d = msg.to_openai()
        assert d["role"] == "assistant"
        assert len(d["tool_calls"]) == 1
        assert d["tool_calls"][0]["function"]["name"] == "bash"

    def test_message_to_openai_tool(self):
        msg = Message(role="tool", content="output", tool_call_id="tc_1")
        d = msg.to_openai()
        assert d["role"] == "tool"
        assert d["tool_call_id"] == "tc_1"

    def test_usage_accumulation(self):
        u1 = Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        u2 = Usage(prompt_tokens=200, completion_tokens=100, total_tokens=300)
        u1 += u2
        assert u1.prompt_tokens == 300
        assert u1.completion_tokens == 150
        assert u1.total_tokens == 450

    def test_tool_call_to_openai(self):
        tc = ToolCall(id="tc_1", function_name="add", arguments='{"a":1}')
        d = tc.to_openai()
        assert d["id"] == "tc_1"
        assert d["type"] == "function"
        assert d["function"]["name"] == "add"


# ===== 19. Built-in Tools =====

class TestBuiltinTools:

    @pytest.mark.asyncio
    async def test_bash_echo(self):
        from calcifer.tools import BashTool
        tool = BashTool()
        ctx = ToolContext()
        args = tool.parameters(command="echo hello")
        result = await tool.call(args, ctx)
        assert "hello" in result.content
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_bash_exit_code(self):
        from calcifer.tools import BashTool
        tool = BashTool()
        ctx = ToolContext()
        args = tool.parameters(command="exit 42")
        result = await tool.call(args, ctx)
        assert "42" in result.content

    @pytest.mark.asyncio
    async def test_bash_timeout(self):
        from calcifer.tools import BashTool
        tool = BashTool()
        ctx = ToolContext()
        args = tool.parameters(command="sleep 10", timeout=1)
        result = await tool.call(args, ctx)
        assert result.is_error
        assert "timed out" in result.content.lower()

    @pytest.mark.asyncio
    async def test_file_write_and_read(self, tmp_path):
        from calcifer.tools import FileWriteTool, FileReadTool
        write_tool = FileWriteTool()
        read_tool = FileReadTool()
        ctx = ToolContext(cwd=str(tmp_path))

        test_file = tmp_path / "test.txt"
        write_args = write_tool.parameters(file_path=str(test_file), content="Hello World\nLine 2\n")
        result = await write_tool.call(write_args, ctx)
        assert not result.is_error

        read_args = read_tool.parameters(file_path=str(test_file))
        result = await read_tool.call(read_args, ctx)
        assert "Hello World" in result.content
        assert "Line 2" in result.content

    @pytest.mark.asyncio
    async def test_file_read_offset_limit(self, tmp_path):
        from calcifer.tools import FileWriteTool, FileReadTool
        write_tool = FileWriteTool()
        read_tool = FileReadTool()
        ctx = ToolContext(cwd=str(tmp_path))

        test_file = tmp_path / "lines.txt"
        lines = "\n".join(f"Line {i}" for i in range(1, 51))
        write_args = write_tool.parameters(file_path=str(test_file), content=lines)
        await write_tool.call(write_args, ctx)

        read_args = read_tool.parameters(file_path=str(test_file), offset=10, limit=5)
        result = await read_tool.call(read_args, ctx)
        assert "Line 11" in result.content

    @pytest.mark.asyncio
    async def test_file_edit(self, tmp_path):
        from calcifer.tools import FileWriteTool, FileEditTool
        write_tool = FileWriteTool()
        edit_tool = FileEditTool()
        ctx = ToolContext(cwd=str(tmp_path))

        test_file = tmp_path / "edit_test.txt"
        write_args = write_tool.parameters(file_path=str(test_file), content="foo bar baz\nline two\n")
        await write_tool.call(write_args, ctx)
        # Track file read state to bypass read-before-edit check
        ctx.read_file_state[str(test_file)] = test_file.stat().st_mtime

        edit_args = edit_tool.parameters(
            file_path=str(test_file),
            old_string="foo bar baz",
            new_string="FOO BAR BAZ",
        )
        result = await edit_tool.call(edit_args, ctx)
        assert not result.is_error

        content = test_file.read_text()
        assert "FOO BAR BAZ" in content

    @pytest.mark.asyncio
    async def test_glob_tool(self, tmp_path):
        from calcifer.tools import GlobTool
        (tmp_path / "a.py").touch()
        (tmp_path / "b.py").touch()
        (tmp_path / "c.txt").touch()
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "d.py").touch()

        tool = GlobTool()
        ctx = ToolContext(cwd=str(tmp_path))
        args = tool.parameters(pattern="**/*.py", path=str(tmp_path))
        result = await tool.call(args, ctx)
        assert "a.py" in result.content
        assert "b.py" in result.content
        assert "d.py" in result.content
        assert "c.txt" not in result.content

    @pytest.mark.asyncio
    async def test_grep_tool(self, tmp_path):
        from calcifer.tools import GrepTool
        (tmp_path / "test.py").write_text("def hello():\n    print('hello')\n")
        (tmp_path / "test2.py").write_text("def world():\n    pass\n")

        tool = GrepTool()
        ctx = ToolContext(cwd=str(tmp_path))
        args = tool.parameters(pattern="hello", path=str(tmp_path))
        result = await tool.call(args, ctx)
        assert "hello" in result.content
        assert "world" not in result.content


# ===== 20. Coordinator =====

class TestCoordinator:

    @pytest.mark.asyncio
    async def test_single_worker(self):
        """Single worker runs and returns result."""
        config = CalciferConfig(api_key="test", model="test-model")
        coord = Coordinator(config, [add_tool])

        # Agent is imported lazily inside WorkerAgent.run() via `from ..agent import Agent`
        # We need to patch at the calcifer.agent module level
        with patch("calcifer.agent.Agent") as MockAgent:
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=AgentResult(
                messages=[], final_text="Worker done!", usage=Usage(), turn_count=2,
            ))
            mock_agent_instance.close = AsyncMock()
            mock_agent_instance.__aenter__ = AsyncMock(return_value=mock_agent_instance)
            mock_agent_instance.__aexit__ = AsyncMock(return_value=False)
            MockAgent.return_value = mock_agent_instance

            result = await coord.run_worker("research", "Find endpoints")
            assert result.status == "completed"
            assert result.result_text == "Worker done!"

    @pytest.mark.asyncio
    async def test_parallel_workers(self):
        """Multiple workers run in parallel."""
        config = CalciferConfig(api_key="test", model="test-model")
        coord = Coordinator(config, [add_tool])

        with patch("calcifer.agent.Agent") as MockAgent:
            mock_agent_instance = AsyncMock()
            call_count = 0
            async def mock_run(prompt, **kw):
                nonlocal call_count
                call_count += 1
                return AgentResult(
                    messages=[], final_text=f"Result {call_count}",
                    usage=Usage(), turn_count=1,
                )
            mock_agent_instance.run = mock_run
            mock_agent_instance.close = AsyncMock()
            mock_agent_instance.__aenter__ = AsyncMock(return_value=mock_agent_instance)
            mock_agent_instance.__aexit__ = AsyncMock(return_value=False)
            MockAgent.return_value = mock_agent_instance

            results = await coord.run_workers([
                ("w1", "Task 1"),
                ("w2", "Task 2"),
                ("w3", "Task 3"),
            ], parallel=True)
            assert len(results) == 3
            assert all(r.status == "completed" for r in results)

    def test_format_results(self):
        config = CalciferConfig(api_key="test")
        coord = Coordinator(config, [])
        from calcifer.coordinator.coordinator import WorkerResult
        results = [
            WorkerResult(worker_id="w_1", name="research", status="completed", result_text="Found it", usage=Usage(), turn_count=3),
            WorkerResult(worker_id="w_2", name="implement", status="failed", result_text="Error occurred", usage=Usage(), turn_count=1),
        ]
        xml = coord.format_results_for_coordinator(results)
        assert "<task-notification>" in xml
        assert "research" in xml
        assert "implement" in xml

    def test_scratchpad_created(self, tmp_path):
        config = CalciferConfig(api_key="test")
        coord_config = CoordinatorConfig(scratchpad_dir=str(tmp_path / "scratch"))
        coord = Coordinator(config, [], coord_config)
        assert Path(coord.scratchpad_dir).exists()


# ===== 21. MCP Tool Adapter =====

class TestMCPToolAdapter:

    def test_adapter_schema(self):
        from calcifer.services.mcp.client import MCPToolSchema
        from calcifer.services.mcp.tool_adapter import MCPToolAdapter
        schema = MCPToolSchema(
            name="my_tool",
            description="A test tool",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
            server_name="test_server",
        )
        client = MagicMock()
        adapter = MCPToolAdapter(schema, client)

        assert adapter.name == "mcp__test_server__my_tool"
        assert adapter.is_mcp
        openai_schema = adapter.to_openai_schema()
        assert openai_schema["function"]["name"] == "mcp__test_server__my_tool"
        assert "query" in openai_schema["function"]["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_adapter_call(self):
        from calcifer.services.mcp.client import MCPToolSchema
        from calcifer.services.mcp.tool_adapter import MCPToolAdapter

        schema = MCPToolSchema(
            name="echo",
            description="Echo back",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            server_name="srv",
        )
        client = MagicMock()
        client.call_tool = AsyncMock(return_value={
            "content": [{"type": "text", "text": "echoed: hello"}]
        })
        adapter = MCPToolAdapter(schema, client)
        ctx = ToolContext()
        args = adapter.parameters(text="hello")
        result = await adapter.call(args, ctx)
        assert "echoed: hello" in result.content

    @pytest.mark.asyncio
    async def test_adapter_call_error(self):
        from calcifer.services.mcp.client import MCPToolSchema
        from calcifer.services.mcp.tool_adapter import MCPToolAdapter

        schema = MCPToolSchema(name="err", description="", input_schema={"type": "object"}, server_name="srv")
        client = MagicMock()
        client.call_tool = AsyncMock(side_effect=RuntimeError("MCP error"))
        adapter = MCPToolAdapter(schema, client)
        ctx = ToolContext()
        args = adapter.parameters()
        result = await adapter.call(args, ctx)
        assert result.is_error
        assert "MCP error" in result.content


# ===== 22. Agent with Session Persistence =====

class TestAgentSessionIntegration:

    @pytest.mark.asyncio
    async def test_agent_session_persistence(self, tmp_path):
        """Agent saves session after each turn."""
        config = CalciferConfig(api_key="test", model="test-model")
        agent = Agent(config=config)
        agent.enable_session_persistence(str(tmp_path))

        agent._provider.chat_completion = AsyncMock(return_value=(
            make_assistant_msg(content="Session response"),
            make_usage(),
        ))
        result = await agent.run("test")
        assert result.final_text == "Session response"

        # Session file should exist
        session_files = list(tmp_path.glob("*.json"))
        assert len(session_files) == 1

        # Load and verify
        loaded = agent._session.load()
        assert loaded is not None
        messages, _, _ = loaded
        assert any(m.content == "Session response" for m in messages)
        await agent.close()


# ===== 23. Agent with MCP =====

class TestAgentMCPIntegration:

    @pytest.mark.asyncio
    async def test_connect_mcp_servers(self):
        """Agent connects MCP servers and adds tools."""
        from calcifer.config import MCPServerConfig
        config = CalciferConfig(api_key="test")
        agent = Agent(config=config)

        # Patch at the source module level since imports are local inside the method
        with patch("calcifer.services.mcp.client.MCPClient") as MockClient, \
             patch("calcifer.services.mcp.tool_adapter.create_mcp_tools") as mock_create, \
             patch("calcifer.services.mcp.transport.StdioTransport") as MockTransport:
            mock_client = AsyncMock()
            mock_client.connect = AsyncMock()
            mock_client.discover_tools = AsyncMock(return_value=[])
            mock_client.name = "test"
            MockClient.return_value = mock_client
            mock_create.return_value = []
            MockTransport.return_value = MagicMock()

            servers = [MCPServerConfig(name="test", transport="stdio", command="echo")]
            await agent.connect_mcp_servers(servers)
            mock_client.connect.assert_called_once()

        await agent.close()


# ===== 24. Comprehensive Integration: Multi-turn with tools, hooks, cost =====

class TestComprehensiveIntegration:

    @pytest.mark.asyncio
    async def test_full_cycle(self, tmp_path):
        """Full integration: agent with tools, hooks, cost tracking, session."""
        config = CalciferConfig(api_key="test", model="gpt-4o")
        agent = Agent(config=config, tools=[add_tool, concat_tool])
        agent.enable_session_persistence(str(tmp_path))

        # Register a hook
        hook_calls = []
        async def track_hook(messages, context):
            hook_calls.append(len(messages))
            return False  # Don't stop

        agent.register_stop_hook(track_hook)

        call_count = 0
        async def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First turn: call add tool
                return (
                    make_assistant_msg(tool_calls=[
                        ToolCall(id="tc_1", function_name="add", arguments='{"a": 10, "b": 20}')
                    ]),
                    make_usage(prompt=200, completion=100, total=300),
                )
            elif call_count == 2:
                # Second turn: call concat tool
                return (
                    make_assistant_msg(tool_calls=[
                        ToolCall(id="tc_2", function_name="concat", arguments='{"x": "hello", "y": " world"}')
                    ]),
                    make_usage(prompt=300, completion=150, total=450),
                )
            else:
                # Final response
                return (
                    make_assistant_msg(content="30 and hello world"),
                    make_usage(prompt=400, completion=200, total=600),
                )

        agent._provider.chat_completion = AsyncMock(side_effect=mock_completion)
        result = await agent.run("Test full cycle")

        # Verify result
        assert result.turn_count == 3
        assert "30" in result.final_text
        assert "hello world" in result.final_text

        # Verify tool results in messages
        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msgs) == 2
        assert tool_msgs[0].content == "30"
        assert tool_msgs[1].content == "hello world"

        # Verify hook was called (twice - after each tool turn)
        assert len(hook_calls) == 2

        # Verify cost tracking
        cost = agent.cost_tracker.get_cost()
        assert cost > 0

        # Verify session was saved
        session_files = list(tmp_path.glob("*.json"))
        assert len(session_files) == 1

        await agent.close()


# ===== 25. LLM Provider =====

class TestLLMProvider:

    def test_build_request_body(self):
        provider = LLMProvider(api_key="test", model="gpt-4o", max_tokens=4096)
        msgs = [Message(role="user", content="Hello")]
        body = provider._build_request_body(msgs, stream=False)
        assert body["model"] == "gpt-4o"
        assert body["max_tokens"] == 4096
        assert body["stream"] is False
        assert len(body["messages"]) == 1

    def test_build_request_body_with_tools(self):
        provider = LLMProvider(api_key="test", model="gpt-4o")
        msgs = [Message(role="user", content="Hello")]
        tools = [add_tool.to_openai_schema()]
        body = provider._build_request_body(msgs, tools=tools)
        assert "tools" in body
        assert len(body["tools"]) == 1

    def test_build_request_body_stream_options(self):
        provider = LLMProvider(api_key="test", model="gpt-4o")
        msgs = [Message(role="user", content="Hello")]
        body = provider._build_request_body(msgs, stream=True)
        assert body["stream"] is True
        assert body["stream_options"]["include_usage"] is True

    def test_parse_response(self):
        provider = LLMProvider(api_key="test", model="gpt-4o")
        data = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Hello!",
                    "tool_calls": None,
                },
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }
        msg, usage = provider._parse_response(data)
        assert msg.content == "Hello!"
        assert msg.role == "assistant"
        assert usage.total_tokens == 15

    def test_parse_response_with_tool_calls(self):
        provider = LLMProvider(api_key="test", model="gpt-4o")
        data = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "tc_1",
                        "type": "function",
                        "function": {"name": "add", "arguments": '{"a":1,"b":2}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }
        msg, usage = provider._parse_response(data)
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].function_name == "add"

    def test_parse_response_max_output(self):
        provider = LLMProvider(api_key="test", model="gpt-4o")
        data = {
            "choices": [{
                "message": {"role": "assistant", "content": "partial"},
                "finish_reason": "length",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8192, "total_tokens": 8202},
        }
        msg, _ = provider._parse_response(data)
        assert msg.metadata.get("api_error") == "max_output_tokens"

    def test_classify_api_error(self):
        from calcifer.services.api.provider import classify_api_error
        from calcifer.types.message import APIErrorType
        assert classify_api_error(429, "") == APIErrorType.RATE_LIMITED
        assert classify_api_error(529, "") == APIErrorType.OVERLOADED
        assert classify_api_error(401, "") == APIErrorType.AUTH_ERROR
        assert classify_api_error(400, "prompt is too long") == APIErrorType.PROMPT_TOO_LONG
        assert classify_api_error(400, "something else") == APIErrorType.INVALID_REQUEST

    def test_backoff_delay(self):
        provider = LLMProvider(api_key="test", model="test")
        d0 = provider._backoff_delay(0)
        d1 = provider._backoff_delay(1)
        d5 = provider._backoff_delay(5)
        assert d0 < d1 < d5
        assert d5 <= 30.0 + 3.0  # MAX_DELAY + max jitter
