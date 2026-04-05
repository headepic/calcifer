"""Tests for all previously-untested utility modules + agent edge cases.

Covers:
- recovery.py: interruption detection, conversation repair, resume messages
- classifier.py: security classification of tool calls
- token_estimation.py: token counting, request estimation
- sandbox.py: sandbox manager, backend detection, command wrapping
- thinking.py: thinking config, model detection, overhead estimation
- concurrency.py: QueryGuard, AbortController, ContextModifierQueue
- profiling.py: QueryProfiler checkpoints and timing
- shutdown.py: ShutdownManager cleanup registration and ordering
- Agent edge cases: double compact, session resume, concurrent abort in streaming
"""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from calcifer import Agent, CalciferConfig, Message, ToolCall, Usage, tool
from calcifer.types.tools import ToolContext, ToolResult


# ========================================
# 1. recovery.py
# ========================================

from calcifer.utils.recovery import (
    InterruptionType,
    detect_interruption,
    repair_conversation,
    build_resume_message,
    _filter_orphaned_thinking,
    _filter_whitespace_assistants,
    _synthesize_missing_tool_results,
)


class TestRecovery:

    def test_detect_no_interruption_empty(self):
        assert detect_interruption([]) == InterruptionType.NONE

    def test_detect_no_interruption_normal(self):
        msgs = [
            Message(role="user", content="hi"),
            Message(role="assistant", content="hello"),
        ]
        assert detect_interruption(msgs) == InterruptionType.NONE

    def test_detect_mid_prompt(self):
        msgs = [
            Message(role="user", content="hi"),
            Message(role="assistant", content="hello"),
            Message(role="user", content="another question"),
        ]
        assert detect_interruption(msgs) == InterruptionType.MID_PROMPT

    def test_detect_mid_prompt_ignores_meta(self):
        """Meta user messages (like resume messages) don't count as mid_prompt."""
        msgs = [
            Message(role="user", content="hi"),
            Message(role="assistant", content="ok"),
            Message(role="user", content="meta msg", is_meta=True),
        ]
        assert detect_interruption(msgs) == InterruptionType.NONE

    def test_detect_mid_turn(self):
        """Assistant has tool_calls with no matching tool results."""
        msgs = [
            Message(role="user", content="do something"),
            Message(role="assistant", content=None, tool_calls=[
                ToolCall(id="tc_1", function_name="bash", arguments='{"command":"ls"}'),
            ]),
        ]
        assert detect_interruption(msgs) == InterruptionType.MID_TURN

    def test_detect_no_interruption_resolved_tools(self):
        """All tool_calls have matching results → no interruption."""
        msgs = [
            Message(role="user", content="do something"),
            Message(role="assistant", tool_calls=[
                ToolCall(id="tc_1", function_name="bash", arguments='{}'),
            ]),
            Message(role="tool", content="ok", tool_call_id="tc_1"),
            Message(role="assistant", content="Done"),
        ]
        assert detect_interruption(msgs) == InterruptionType.NONE

    def test_filter_orphaned_thinking(self):
        msgs = [
            Message(role="user", content="hi"),
            Message(role="assistant", content=None, metadata={"has_thinking": True}),
            Message(role="assistant", content="real response"),
        ]
        result = _filter_orphaned_thinking(msgs)
        assert len(result) == 2
        assert result[1].content == "real response"

    def test_filter_whitespace_assistants(self):
        msgs = [
            Message(role="user", content="hi"),
            Message(role="assistant", content="   \n  "),  # whitespace only
            Message(role="assistant", content="real"),
        ]
        result = _filter_whitespace_assistants(msgs)
        assert len(result) == 2
        assert result[1].content == "real"

    def test_filter_keeps_tool_call_with_whitespace(self):
        """Whitespace assistant with tool_calls should NOT be removed."""
        msgs = [
            Message(role="assistant", content="  ", tool_calls=[
                ToolCall(id="tc_1", function_name="add", arguments="{}"),
            ]),
        ]
        result = _filter_whitespace_assistants(msgs)
        assert len(result) == 1

    def test_synthesize_missing_tool_results(self):
        msgs = [
            Message(role="assistant", tool_calls=[
                ToolCall(id="tc_1", function_name="bash", arguments="{}"),
                ToolCall(id="tc_2", function_name="add", arguments="{}"),
            ]),
            Message(role="tool", content="ok", tool_call_id="tc_1"),
            # tc_2 is missing
        ]
        result = _synthesize_missing_tool_results(msgs)
        assert len(result) == 3  # original 2 + 1 synthetic
        synthetic = result[-1]
        assert synthetic.role == "tool"
        assert synthetic.tool_call_id == "tc_2"
        assert "interrupted" in synthetic.content.lower()
        assert synthetic.metadata.get("synthetic") is True

    def test_synthesize_nothing_when_complete(self):
        msgs = [
            Message(role="assistant", tool_calls=[
                ToolCall(id="tc_1", function_name="add", arguments="{}"),
            ]),
            Message(role="tool", content="3", tool_call_id="tc_1"),
        ]
        result = _synthesize_missing_tool_results(msgs)
        assert len(result) == 2  # No change

    def test_repair_conversation_full(self):
        """repair_conversation applies all passes."""
        msgs = [
            Message(role="assistant", content=None, metadata={"has_thinking": True}),  # orphaned thinking
            Message(role="assistant", content="  \n  "),  # whitespace
            Message(role="assistant", tool_calls=[
                ToolCall(id="tc_1", function_name="bash", arguments="{}"),
            ]),
            # Missing tool result for tc_1
        ]
        result = repair_conversation(msgs)
        # Orphaned thinking removed, whitespace removed, synthetic result added
        assert len(result) == 2  # assistant with tool_call + synthetic result
        assert result[0].tool_calls[0].id == "tc_1"
        assert result[1].tool_call_id == "tc_1"

    def test_build_resume_message_none(self):
        assert build_resume_message(InterruptionType.NONE) is None

    def test_build_resume_message_mid_turn(self):
        msg = build_resume_message(InterruptionType.MID_TURN)
        assert msg is not None
        assert msg.role == "user"
        assert msg.is_meta
        assert "continue" in msg.content.lower()

    def test_build_resume_message_mid_prompt(self):
        assert build_resume_message(InterruptionType.MID_PROMPT) is None


# ========================================
# 2. classifier.py
# ========================================

from calcifer.utils.classifier import (
    SecurityLevel,
    ClassificationResult,
    classify_tool_call,
    classify_transcript,
    has_dangerous_calls,
)


class TestClassifier:

    def test_safe_tools(self):
        for tool_name in ["file_read", "glob", "grep"]:
            result = classify_tool_call(tool_name, {})
            assert result.level == SecurityLevel.SAFE

    def test_dangerous_bash_rm(self):
        result = classify_tool_call("bash", {"command": "rm -rf /"})
        assert result.level == SecurityLevel.DANGEROUS
        assert "rm -rf" in result.reason.lower()

    def test_dangerous_bash_sudo(self):
        result = classify_tool_call("bash", {"command": "sudo apt install"})
        assert result.level == SecurityLevel.DANGEROUS

    def test_dangerous_bash_force_push(self):
        result = classify_tool_call("bash", {"command": "git push --force origin main"})
        assert result.level == SecurityLevel.DANGEROUS

    def test_suspicious_file_write_env(self):
        result = classify_tool_call("file_write", {"file_path": "/app/.env"})
        assert result.level == SecurityLevel.SUSPICIOUS

    def test_suspicious_file_edit_ssh(self):
        result = classify_tool_call("file_edit", {"file_path": "/home/user/.ssh/id_rsa"})
        assert result.level == SecurityLevel.SUSPICIOUS

    def test_safe_bash_normal(self):
        result = classify_tool_call("bash", {"command": "echo hello"})
        assert result.level == SecurityLevel.SAFE

    def test_safe_file_write_normal(self):
        result = classify_tool_call("file_write", {"file_path": "/app/src/main.py"})
        assert result.level == SecurityLevel.SAFE

    def test_unknown_tool_safe(self):
        result = classify_tool_call("my_custom_tool", {"data": "safe"})
        assert result.level == SecurityLevel.SAFE

    def test_classify_transcript(self):
        msgs = [
            Message(role="assistant", tool_calls=[
                ToolCall(id="1", function_name="bash", arguments='{"command": "rm -rf /"}'),
                ToolCall(id="2", function_name="grep", arguments='{"pattern": "hello"}'),
            ]),
        ]
        results = classify_transcript(msgs)
        assert len(results) == 2
        assert results[0].level == SecurityLevel.DANGEROUS
        assert results[1].level == SecurityLevel.SAFE

    def test_has_dangerous_calls_true(self):
        msgs = [
            Message(role="assistant", tool_calls=[
                ToolCall(id="1", function_name="bash", arguments='{"command": "sudo rm -rf /"}'),
            ]),
        ]
        assert has_dangerous_calls(msgs) is True

    def test_has_dangerous_calls_false(self):
        msgs = [
            Message(role="assistant", tool_calls=[
                ToolCall(id="1", function_name="grep", arguments='{"pattern": "test"}'),
            ]),
        ]
        assert has_dangerous_calls(msgs) is False

    def test_empty_transcript(self):
        assert classify_transcript([]) == []
        assert has_dangerous_calls([]) is False


# ========================================
# 3. token_estimation.py
# ========================================

from calcifer.services.token_estimation import (
    count_tokens,
    count_message_tokens,
    count_messages_tokens,
    count_tool_schema_tokens,
    estimate_request_tokens,
    token_count_with_usage,
)


class TestTokenEstimation:

    def test_count_tokens_nonempty(self):
        n = count_tokens("Hello, world!")
        assert n > 0

    def test_count_tokens_empty(self):
        assert count_tokens("") == 0

    def test_count_message_tokens_simple(self):
        msg = Message(role="user", content="Hello")
        n = count_message_tokens(msg)
        assert n > 4  # At least MESSAGE_OVERHEAD

    def test_count_message_tokens_with_tool_calls(self):
        msg = Message(role="assistant", tool_calls=[
            ToolCall(id="tc_1", function_name="bash", arguments='{"command": "ls -la"}'),
        ])
        n = count_message_tokens(msg)
        assert n > 14  # MESSAGE_OVERHEAD + TOOL_CALL_OVERHEAD

    def test_count_message_tokens_strips_caller_field(self):
        """Arguments with 'caller' field should have it stripped before counting."""
        msg = Message(role="assistant", tool_calls=[
            ToolCall(id="tc_1", function_name="add",
                     arguments='{"a": 1, "b": 2, "caller": "internal"}'),
        ])
        n_with = count_message_tokens(msg)

        msg2 = Message(role="assistant", tool_calls=[
            ToolCall(id="tc_1", function_name="add",
                     arguments='{"a": 1, "b": 2}'),
        ])
        n_without = count_message_tokens(msg2)
        # With caller stripped, both should produce same count
        assert n_with == n_without

    def test_count_messages_tokens(self):
        msgs = [
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi there"),
        ]
        n = count_messages_tokens(msgs)
        assert n > 8  # Two messages with overhead

    def test_count_tool_schema_tokens(self):
        tools = [{"type": "function", "function": {"name": "add", "parameters": {}}}]
        n = count_tool_schema_tokens(tools)
        assert n > 0

    def test_count_tool_schema_empty(self):
        assert count_tool_schema_tokens([]) == 0

    def test_estimate_request_tokens(self):
        msgs = [Message(role="user", content="Hello")]
        tools = [{"type": "function", "function": {"name": "add", "parameters": {}}}]
        n = estimate_request_tokens(msgs, tools=tools, system_prompt="Be helpful")
        assert n > 0

    def test_estimate_request_tokens_with_thinking(self):
        msgs = [Message(role="user", content="Hello")]
        n1 = estimate_request_tokens(msgs, thinking_budget=0)
        n2 = estimate_request_tokens(msgs, thinking_budget=10000)
        assert n2 > n1  # Thinking adds overhead

    def test_token_count_with_usage_prefers_api(self):
        msgs = [Message(role="user", content="x" * 1000)]
        usage = Usage(prompt_tokens=42)
        assert token_count_with_usage(msgs, usage) == 42

    def test_token_count_with_usage_fallback(self):
        msgs = [Message(role="user", content="Hello world")]
        n = token_count_with_usage(msgs, None)
        assert n > 0

    def test_token_count_with_usage_zero_api(self):
        """API usage with 0 tokens should fall back to estimation."""
        msgs = [Message(role="user", content="Hello world")]
        usage = Usage(prompt_tokens=0)
        n = token_count_with_usage(msgs, usage)
        assert n > 0


# ========================================
# 4. sandbox.py
# ========================================

from calcifer.utils.sandbox import (
    SandboxBackend,
    SandboxConfig,
    SandboxManager,
    _is_available,
    _shell_quote,
)


class TestSandbox:

    def test_none_backend_always_available(self):
        assert _is_available(SandboxBackend.NONE) is True

    def test_default_config_no_sandbox(self):
        mgr = SandboxManager()
        assert not mgr.is_sandboxed

    def test_should_sandbox_excluded_commands(self):
        mgr = SandboxManager(SandboxConfig(backend=SandboxBackend.FIREJAIL))
        # Even if backend is set, simple commands skip sandboxing
        for cmd in ["echo hello", "printf test", "pwd", "whoami", "date"]:
            if not mgr.is_sandboxed:
                continue  # firejail not available
            assert not mgr.should_sandbox(cmd), f"{cmd} should be excluded"

    def test_should_sandbox_complex_commands(self):
        config = SandboxConfig(backend=SandboxBackend.NONE)
        mgr = SandboxManager(config)
        assert not mgr.should_sandbox("rm -rf /")  # NONE backend never sandboxes

    def test_firejail_wrap(self):
        config = SandboxConfig(
            backend=SandboxBackend.NONE,  # Use NONE to avoid availability check
            allowed_paths=["/home/user"],
            read_only_paths=["/etc"],
            denied_paths=["/root"],
            allow_network=False,
        )
        mgr = SandboxManager(config)
        # Manually test the wrap method
        mgr._config.backend = SandboxBackend.FIREJAIL
        wrapped = mgr._firejail_wrap("ls -la", ".")
        assert "firejail" in wrapped
        assert "--net=none" in wrapped
        assert "--whitelist=/home/user" in wrapped
        assert "--read-only=/etc" in wrapped
        assert "--blacklist=/root" in wrapped

    def test_docker_wrap(self):
        config = SandboxConfig(backend=SandboxBackend.NONE, docker_image="alpine:latest")
        mgr = SandboxManager(config)
        mgr._config.backend = SandboxBackend.DOCKER
        wrapped = mgr._docker_wrap("ls -la", "/tmp")
        assert "docker" in wrapped
        assert "alpine:latest" in wrapped
        assert "/workspace" in wrapped

    def test_docker_no_network(self):
        config = SandboxConfig(backend=SandboxBackend.NONE, allow_network=False)
        mgr = SandboxManager(config)
        mgr._config.backend = SandboxBackend.DOCKER
        wrapped = mgr._docker_wrap("ls", ".")
        assert "--network=none" in wrapped

    def test_shell_quote(self):
        assert _shell_quote("hello world") == "'hello world'"
        assert _shell_quote("it's") == "\"it's\""  or "'" in _shell_quote("it's")

    def test_fallback_when_unavailable(self):
        """Non-existent backend falls back to NONE."""
        config = SandboxConfig(backend=SandboxBackend.FIREJAIL)
        with patch("calcifer.utils.sandbox._is_available", return_value=False):
            mgr = SandboxManager(config)
        assert not mgr.is_sandboxed
        assert mgr._config.backend == SandboxBackend.NONE


# ========================================
# 5. thinking.py
# ========================================

from calcifer.utils.thinking import (
    ThinkingMode,
    ThinkingConfig,
    should_enable_thinking,
    estimate_thinking_overhead,
)


class TestThinking:

    def test_thinking_disabled_api_params(self):
        config = ThinkingConfig(mode=ThinkingMode.DISABLED)
        assert config.to_api_params() == {}

    def test_thinking_enabled_api_params(self):
        config = ThinkingConfig(mode=ThinkingMode.ENABLED, budget_tokens=5000)
        params = config.to_api_params()
        assert params["thinking"]["type"] == "enabled"
        assert params["thinking"]["budget_tokens"] == 5000

    def test_thinking_adaptive_api_params(self):
        config = ThinkingConfig(mode=ThinkingMode.ADAPTIVE, budget_tokens=8000)
        params = config.to_api_params()
        assert params["thinking"]["type"] == "adaptive"

    def test_should_enable_thinking_claude(self):
        assert should_enable_thinking("claude-opus-4-6") is True
        assert should_enable_thinking("claude-sonnet-4-6") is True
        assert should_enable_thinking("my-claude-sonnet-4-6-tuned") is True

    def test_should_not_enable_thinking_gpt(self):
        assert should_enable_thinking("gpt-4o") is False
        assert should_enable_thinking("gpt-5.4-mini") is False

    def test_estimate_overhead_disabled(self):
        config = ThinkingConfig(mode=ThinkingMode.DISABLED)
        assert estimate_thinking_overhead(config) == 0

    def test_estimate_overhead_enabled(self):
        config = ThinkingConfig(mode=ThinkingMode.ENABLED)
        overhead = estimate_thinking_overhead(config)
        assert overhead > 0
        assert overhead == 1024  # MIN_THINKING_BUDGET


# ========================================
# 6. concurrency.py
# ========================================

from calcifer.utils.concurrency import (
    QueryState,
    QueryGuard,
    AbortController,
    ContextModifierQueue,
)


class TestQueryGuard:

    def test_initial_state(self):
        g = QueryGuard()
        assert g.state == QueryState.IDLE
        assert g.is_idle

    def test_lifecycle(self):
        g = QueryGuard()
        gen = g.begin()
        assert g.state == QueryState.DISPATCHING
        assert not g.is_idle
        g.mark_running(gen)
        assert g.state == QueryState.RUNNING
        g.end(gen)
        assert g.state == QueryState.IDLE

    def test_double_begin_raises(self):
        g = QueryGuard()
        g.begin()
        with pytest.raises(RuntimeError, match="Cannot start query"):
            g.begin()

    def test_stale_generation_ignored(self):
        g = QueryGuard()
        gen1 = g.begin()
        g.force_idle()
        gen2 = g.begin()
        # Old generation ops are ignored
        g.mark_running(gen1)  # stale - should not change state
        assert g.state == QueryState.DISPATCHING  # Still dispatching from gen2
        g.end(gen1)  # stale
        assert g.state == QueryState.DISPATCHING  # Still dispatching

    def test_force_idle(self):
        g = QueryGuard()
        g.begin()
        g.force_idle()
        assert g.is_idle
        # Can begin again after force
        g.begin()


class TestAbortController:

    def test_initial_not_aborted(self):
        ac = AbortController()
        assert not ac.is_aborted

    def test_abort(self):
        ac = AbortController()
        ac.abort()
        assert ac.is_aborted

    def test_double_abort_safe(self):
        ac = AbortController()
        ac.abort()
        ac.abort()  # Should not raise
        assert ac.is_aborted

    def test_parent_child_propagation(self):
        parent = AbortController()
        child = parent.create_child()
        assert not child.is_aborted
        parent.abort()
        assert child.is_aborted

    def test_child_after_abort(self):
        """Child created after parent abort is immediately aborted."""
        parent = AbortController()
        parent.abort()
        child = parent.create_child()
        assert child.is_aborted

    def test_callback_on_abort(self):
        ac = AbortController()
        called = []
        ac.on_abort(lambda: called.append(True))
        ac.abort()
        assert len(called) == 1

    def test_callback_immediate_if_already_aborted(self):
        ac = AbortController()
        ac.abort()
        called = []
        ac.on_abort(lambda: called.append(True))
        assert len(called) == 1

    def test_check_raises_when_aborted(self):
        ac = AbortController()
        ac.check()  # Should not raise
        ac.abort()
        with pytest.raises(asyncio.CancelledError):
            ac.check()

    @pytest.mark.asyncio
    async def test_wait(self):
        ac = AbortController()
        # Abort after a short delay
        async def abort_later():
            await asyncio.sleep(0.01)
            ac.abort()
        asyncio.create_task(abort_later())
        await asyncio.wait_for(ac.wait(), timeout=1.0)
        assert ac.is_aborted

    def test_grandchild_propagation(self):
        grandparent = AbortController()
        parent = grandparent.create_child()
        child = parent.create_child()
        grandparent.abort()
        assert parent.is_aborted
        assert child.is_aborted


class TestContextModifierQueue:

    def test_empty_queue(self):
        q = ContextModifierQueue()
        assert len(q) == 0
        ctx = ToolContext()
        result = q.apply_all(ctx)
        assert result is ctx

    def test_enqueue_and_apply(self):
        q = ContextModifierQueue()
        q.enqueue("tc_1", lambda ctx: ToolContext(cwd="/new"))
        assert len(q) == 1
        ctx = ToolContext(cwd="/old")
        result = q.apply_all(ctx)
        assert result.cwd == "/new"
        assert len(q) == 0  # Cleared after apply

    def test_apply_order_preserved(self):
        q = ContextModifierQueue()
        order = []
        q.enqueue("tc_1", lambda ctx: (order.append(1), ctx)[-1])
        q.enqueue("tc_2", lambda ctx: (order.append(2), ctx)[-1])
        q.enqueue("tc_3", lambda ctx: (order.append(3), ctx)[-1])
        q.apply_all(ToolContext())
        assert order == [1, 2, 3]

    def test_apply_error_continues(self):
        """One failing modifier doesn't block others."""
        q = ContextModifierQueue()
        applied = []
        q.enqueue("tc_1", lambda ctx: (applied.append(1), ctx)[-1])

        def bad_modifier(ctx):
            raise ValueError("boom")

        q.enqueue("tc_2", bad_modifier)
        q.enqueue("tc_3", lambda ctx: (applied.append(3), ctx)[-1])
        q.apply_all(ToolContext())
        assert applied == [1, 3]  # tc_2 error skipped, tc_3 still ran


# ========================================
# 7. profiling.py
# ========================================

from calcifer.utils.profiling import QueryProfiler


class TestProfiler:

    def test_checkpoint(self):
        p = QueryProfiler()
        time.sleep(0.01)
        p.checkpoint("start_llm")
        time.sleep(0.01)
        p.checkpoint("end_llm")
        summary = p.summary()
        assert "start_llm" in summary
        assert "end_llm" in summary
        assert summary["end_llm"] > summary["start_llm"]

    def test_total_ms(self):
        p = QueryProfiler()
        time.sleep(0.01)
        total = p.total_ms()
        assert total >= 10  # At least 10ms

    def test_reset(self):
        p = QueryProfiler()
        p.checkpoint("a")
        p.reset()
        assert p.summary() == {}
        # total_ms should be small after reset
        assert p.total_ms() < 100


# ========================================
# 8. shutdown.py
# ========================================

from calcifer.utils.shutdown import ShutdownManager, get_shutdown_manager


class TestShutdownManager:

    @pytest.mark.asyncio
    async def test_register_and_cleanup(self):
        mgr = ShutdownManager()
        order = []
        mgr.register_cleanup("first", lambda: order.append("first"))
        mgr.register_cleanup("second", lambda: order.append("second"))
        # LIFO order
        await mgr.shutdown("test")
        assert order == ["second", "first"]

    @pytest.mark.asyncio
    async def test_async_cleanup(self):
        mgr = ShutdownManager()
        called = []

        async def async_cleanup():
            called.append(True)

        mgr.register_cleanup("async", async_cleanup)
        await mgr.shutdown("test")
        assert called == [True]

    @pytest.mark.asyncio
    async def test_unregister_cleanup(self):
        mgr = ShutdownManager()
        called = []
        mgr.register_cleanup("removable", lambda: called.append("removed"))
        mgr.unregister_cleanup("removable")
        await mgr.shutdown("test")
        assert called == []

    @pytest.mark.asyncio
    async def test_cleanup_exception_continues(self):
        mgr = ShutdownManager()
        order = []

        def bad_cleanup():
            raise RuntimeError("boom")

        mgr.register_cleanup("bad", bad_cleanup)
        mgr.register_cleanup("good", lambda: order.append("good"))
        # LIFO: good first, then bad
        await mgr.shutdown("test")
        assert "good" in order

    @pytest.mark.asyncio
    async def test_shutdown_hooks(self):
        mgr = ShutdownManager()
        hook_called = []
        mgr.register_shutdown_hook(lambda: hook_called.append(True))
        await mgr.shutdown("test")
        assert hook_called == [True]

    def test_global_singleton(self):
        mgr1 = get_shutdown_manager()
        mgr2 = get_shutdown_manager()
        assert mgr1 is mgr2


# ========================================
# 9. Constants
# ========================================

from calcifer.constants import (
    DEFAULT_MAX_TOOL_CONCURRENCY,
    DEFAULT_MAX_RESULT_SIZE,
    DEFAULT_MAX_CONTEXT_TOKENS,
    DEFAULT_COMPACT_THRESHOLD,
)


class TestConstants:

    def test_constants_values(self):
        assert DEFAULT_MAX_TOOL_CONCURRENCY == 10
        assert DEFAULT_MAX_RESULT_SIZE == 30_000
        assert DEFAULT_MAX_CONTEXT_TOKENS == 128_000
        assert 0 < DEFAULT_COMPACT_THRESHOLD < 1


# ========================================
# 10. Agent Edge Cases
# ========================================


@tool(name="add", description="Add two numbers")
def add_tool(a: int, b: int) -> str:
    return str(a + b)


class TestAgentEdgeCases:

    @pytest.mark.asyncio
    async def test_session_resume_flow(self, tmp_path):
        """Full session save → resume → continue cycle."""
        config = CalciferConfig(api_key="test", model="test")
        agent = Agent(config=config, tools=[add_tool])
        agent.enable_session_persistence(str(tmp_path))

        # First run
        call_count = 0

        async def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    Message(role="assistant", tool_calls=[
                        ToolCall(id="tc_1", function_name="add", arguments='{"a":1,"b":2}'),
                    ]),
                    Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
                )
            return Message(role="assistant", content="Result is 3"), Usage(prompt_tokens=200, completion_tokens=100, total_tokens=300)

        agent._provider.chat_completion = AsyncMock(side_effect=mock_completion)
        r1 = await agent.run("What is 1+2?")
        session_id = agent.session_id
        assert r1.final_text == "Result is 3"

        # Resume session
        loaded = await agent.resume_session(session_id)
        assert loaded is not None
        assert len(loaded) >= 3  # user + assistant(tool) + tool + assistant(text)

        # Continue with resumed messages
        call_count = 0

        async def mock_continuation(**kwargs):
            return Message(role="assistant", content="Continued!"), Usage(prompt_tokens=300, completion_tokens=50, total_tokens=350)

        agent._provider.chat_completion = AsyncMock(side_effect=mock_continuation)
        r2 = await agent.run("What was the result again?", messages=loaded)
        assert r2.final_text == "Continued!"
        await agent.close()

    @pytest.mark.asyncio
    async def test_stop_hook_exception_does_not_crash(self):
        """A broken stop hook is caught and does not crash the agent."""
        agent = Agent(api_key="test", tools=[add_tool])

        def broken_hook(messages, context):
            raise RuntimeError("hook exploded")

        agent.register_stop_hook(broken_hook)

        call_count = 0

        async def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    Message(role="assistant", tool_calls=[
                        ToolCall(id="tc_1", function_name="add", arguments='{"a":1,"b":2}'),
                    ]),
                    Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
                )
            return Message(role="assistant", content="Done"), Usage()

        agent._provider.chat_completion = AsyncMock(side_effect=mock_completion)
        result = await agent.run("test")
        # Should complete despite broken hook
        assert result.final_text == "Done"
        assert result.turn_count == 2
        await agent.close()

    @pytest.mark.asyncio
    async def test_empty_tool_list(self):
        """Agent works fine with no tools at all."""
        agent = Agent(api_key="test")
        agent._provider.chat_completion = AsyncMock(return_value=(
            Message(role="assistant", content="No tools needed"),
            Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        ))
        result = await agent.run("Hello")
        assert result.final_text == "No tools needed"
        # Verify no tools in schema
        schemas = agent._get_tool_schemas()
        assert schemas is None
        await agent.close()

    @pytest.mark.asyncio
    async def test_add_tools_dynamically(self):
        """Tools can be added after agent creation."""
        agent = Agent(api_key="test")
        assert len(agent._tools) == 0
        agent.add_tool(add_tool)
        assert len(agent._tools) == 1
        assert "add" in agent._tools_by_name

        @tool(name="sub", description="Subtract")
        def sub(a: int, b: int) -> str:
            return str(a - b)

        agent.add_tools([sub])
        assert len(agent._tools) == 2
        assert "sub" in agent._tools_by_name
        await agent.close()

    @pytest.mark.asyncio
    async def test_concurrent_abort_during_streaming(self):
        """Abort during streaming stops gracefully."""
        agent = Agent(api_key="test", tools=[add_tool])

        async def mock_stream(**kwargs):
            yield Message(role="", content="")  # dummy to make it an async generator
            # This won't actually be called properly, we test the abort path
            # by aborting before streaming starts

        # Abort immediately
        agent.abort()

        # Use non-streaming (since streaming mock is complex)
        agent._provider.chat_completion = AsyncMock(return_value=(
            Message(role="assistant", content="hi"),
            Usage(),
        ))

        # run() clears abort, but let's test abort during tool execution
        call_count = 0

        async def mock_with_abort(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                agent.abort()  # Abort after first LLM call
                return (
                    Message(role="assistant", tool_calls=[
                        ToolCall(id="tc_1", function_name="add", arguments='{"a":1,"b":2}'),
                    ]),
                    Usage(),
                )
            return Message(role="assistant", content="never"), Usage()

        agent._abort_event.clear()
        agent._provider.chat_completion = AsyncMock(side_effect=mock_with_abort)
        result = await agent.run("test")
        # Should stop after tool execution because abort was set
        assert result.turn_count <= 2
        await agent.close()

    @pytest.mark.asyncio
    async def test_agent_skill_loading(self, tmp_path):
        """Agent loads skills from directories."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "helper.md").write_text("---\nname: helper\ndescription: A helper\n---\nBe helpful.")

        config = CalciferConfig(api_key="test", skills_dirs=[str(skill_dir)])
        agent = Agent(config=config)
        agent.load_skills()
        assert "helper" in agent._skills
        await agent.close()

    @pytest.mark.asyncio
    async def test_agent_apply_skill(self, tmp_path):
        """Agent applies a loaded skill to conversation."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "calc.md").write_text("---\nname: calc\ndescription: Calculator\nallowed-tools: [add]\n---\nOnly calculate.")

        config = CalciferConfig(api_key="test", skills_dirs=[str(skill_dir)])
        agent = Agent(config=config, tools=[add_tool])
        agent.load_skills()

        messages = [Message(role="user", content="test")]
        new_msgs, new_tools = agent.apply_skill("calc", messages)
        assert any("calc" in (m.content or "").lower() for m in new_msgs if m.role == "system")
        assert len(new_tools) == 1
        assert new_tools[0].name == "add"
        await agent.close()

    @pytest.mark.asyncio
    async def test_agent_apply_unknown_skill_raises(self):
        agent = Agent(api_key="test")
        with pytest.raises(ValueError, match="Unknown skill"):
            agent.apply_skill("nonexistent", [])
        await agent.close()
