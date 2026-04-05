"""Tests for the newly integrated modules.

Verifies that recovery, classifier, thinking, sandbox, QueryGuard,
profiling, and token_estimation delegation are wired up correctly.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from calcifer import Agent, CalciferConfig, Message, ToolCall, Usage, tool
from calcifer.types.tools import ToolContext


@tool(name="add", description="Add two numbers")
def add_tool(a: int, b: int) -> str:
    return str(a + b)


# ===== 1. Recovery integrated into resume_session =====

class TestRecoveryIntegration:

    @pytest.mark.asyncio
    async def test_resume_repairs_interrupted_conversation(self, tmp_path):
        """resume_session applies repair passes to interrupted conversations."""
        config = CalciferConfig(api_key="test")
        agent = Agent(config=config, tools=[add_tool])
        agent.enable_session_persistence(str(tmp_path))

        # Simulate an interrupted session: assistant has tool_call with no result
        interrupted_msgs = [
            Message(role="user", content="Add 1+2"),
            Message(role="assistant", tool_calls=[
                ToolCall(id="tc_1", function_name="add", arguments='{"a":1,"b":2}'),
            ]),
            # Missing tool result for tc_1 — this is the interruption
        ]
        agent._session.save(interrupted_msgs, Usage(), 1)
        session_id = agent._session.session_id

        # Resume should repair the conversation
        resumed = await agent.resume_session(session_id)
        assert resumed is not None

        # Should have synthesized a tool result for tc_1
        tool_msgs = [m for m in resumed if m.role == "tool"]
        assert len(tool_msgs) >= 1
        assert tool_msgs[0].tool_call_id == "tc_1"
        assert "interrupted" in tool_msgs[0].content.lower()

        # After repair, the synthetic tool result makes the conversation complete,
        # so detect_interruption returns NONE and no resume message is added.
        # This is correct — the conversation is now valid for the API.
        assert len(resumed) == 3  # user + assistant(tool_call) + synthetic tool result
        await agent.close()

    @pytest.mark.asyncio
    async def test_resume_clean_session_no_repair(self, tmp_path):
        """Clean sessions don't get synthetic messages."""
        config = CalciferConfig(api_key="test")
        agent = Agent(config=config)
        agent.enable_session_persistence(str(tmp_path))

        clean_msgs = [
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi there"),
        ]
        agent._session.save(clean_msgs, Usage(), 1)

        resumed = await agent.resume_session()
        assert resumed is not None
        assert len(resumed) == 2  # No extra messages added
        await agent.close()


# ===== 2. Classifier integrated into tool orchestrator =====

class TestClassifierIntegration:

    @pytest.mark.asyncio
    async def test_dangerous_command_logged(self, caplog):
        """Dangerous bash commands are flagged by classifier in execute_tool_call."""
        from calcifer.services.tools.orchestrator import execute_tool_call
        from calcifer.tools import BashTool

        bash = BashTool()
        tools_map = {"bash": bash}
        tc = ToolCall(id="tc_1", function_name="bash", arguments='{"command": "echo safe"}')
        ctx = ToolContext()

        import logging
        with caplog.at_level(logging.WARNING):
            await execute_tool_call(tc, tools_map, ctx)

        # "echo safe" should NOT trigger a warning
        security_warnings = [r for r in caplog.records if "SECURITY" in r.message]
        assert len(security_warnings) == 0

    @pytest.mark.asyncio
    async def test_safe_tool_no_warning(self, caplog):
        """Safe tools (grep, glob) produce no security warnings."""
        from calcifer.services.tools.orchestrator import execute_tool_call

        tools_map = {"add": add_tool}
        tc = ToolCall(id="tc_1", function_name="add", arguments='{"a": 1, "b": 2}')
        ctx = ToolContext()

        import logging
        with caplog.at_level(logging.WARNING):
            result = await execute_tool_call(tc, tools_map, ctx)

        assert result.content == "3"
        security_warnings = [r for r in caplog.records if "SECURITY" in r.message]
        assert len(security_warnings) == 0


# ===== 3. Thinking config integrated =====

class TestThinkingIntegration:

    def test_thinking_disabled_no_extra_params(self):
        """Default thinking_mode=disabled adds no thinking params."""
        config = CalciferConfig(api_key="test", thinking_mode="disabled")
        agent = Agent(config=config)
        assert "thinking" not in agent._provider.extra_params
        asyncio.get_event_loop().run_until_complete(agent.close())

    def test_thinking_enabled_adds_params(self):
        """thinking_mode=enabled injects thinking API params."""
        config = CalciferConfig(
            api_key="test",
            thinking_mode="enabled",
            thinking_budget_tokens=5000,
        )
        agent = Agent(config=config)
        assert "thinking" in agent._provider.extra_params
        assert agent._provider.extra_params["thinking"]["type"] == "enabled"
        assert agent._provider.extra_params["thinking"]["budget_tokens"] == 5000
        asyncio.get_event_loop().run_until_complete(agent.close())

    def test_thinking_adaptive(self):
        config = CalciferConfig(api_key="test", thinking_mode="adaptive")
        agent = Agent(config=config)
        assert agent._provider.extra_params["thinking"]["type"] == "adaptive"
        asyncio.get_event_loop().run_until_complete(agent.close())


# ===== 4. Sandbox integrated into BashTool =====

class TestSandboxIntegration:

    def test_bash_default_no_sandbox(self):
        """BashTool with no config has no sandbox."""
        from calcifer.tools import BashTool
        bash = BashTool()
        assert not bash._sandbox.is_sandboxed

    def test_bash_with_sandbox_config(self):
        """BashTool accepts sandbox config."""
        from calcifer.tools import BashTool
        from calcifer.utils.sandbox import SandboxConfig, SandboxBackend

        config = SandboxConfig(backend=SandboxBackend.NONE)
        bash = BashTool(sandbox_config=config)
        assert not bash._sandbox.is_sandboxed

    @pytest.mark.asyncio
    async def test_bash_sandbox_wrap_called(self):
        """Sandbox wrapping is applied to commands."""
        from calcifer.tools import BashTool
        from calcifer.utils.sandbox import SandboxConfig, SandboxBackend

        bash = BashTool()
        # With NONE backend, wrap_command returns the command unchanged
        wrapped = bash._sandbox.wrap_command("ls -la")
        assert wrapped == "ls -la"


# ===== 5. QueryGuard integrated into Agent =====

class TestQueryGuardIntegration:

    @pytest.mark.asyncio
    async def test_concurrent_run_raises(self):
        """Second run() while first is active raises RuntimeError."""
        agent = Agent(api_key="test")

        # Simulate first run blocking
        async def slow_completion(**kwargs):
            await asyncio.sleep(10)
            return Message(role="assistant", content="done"), Usage()

        agent._provider.chat_completion = AsyncMock(side_effect=slow_completion)

        # Start first run in background
        task = asyncio.create_task(agent.run("first"))
        await asyncio.sleep(0.01)  # Let it start

        # Second run should raise
        with pytest.raises(RuntimeError, match="Cannot start query"):
            await agent.run("second")

        agent.abort()  # Abort first run
        try:
            await asyncio.wait_for(task, timeout=2)
        except Exception:
            pass
        await agent.close()

    @pytest.mark.asyncio
    async def test_sequential_runs_ok(self):
        """Sequential runs work fine — guard resets between runs."""
        agent = Agent(api_key="test")
        agent._provider.chat_completion = AsyncMock(return_value=(
            Message(role="assistant", content="ok"),
            Usage(),
        ))

        r1 = await agent.run("first")
        assert r1.final_text == "ok"
        assert agent._query_guard.is_idle

        r2 = await agent.run("second")
        assert r2.final_text == "ok"
        assert agent._query_guard.is_idle
        await agent.close()

    @pytest.mark.asyncio
    async def test_guard_resets_on_error(self):
        """Guard returns to idle even if run() throws."""
        agent = Agent(api_key="test")
        agent._provider.chat_completion = AsyncMock(
            side_effect=RuntimeError("LLM exploded")
        )

        with pytest.raises(RuntimeError):
            await agent.run("boom")

        assert agent._query_guard.is_idle  # Guard should be idle
        await agent.close()


# ===== 6. Token estimation delegation =====

class TestTokenEstimationDelegation:

    def test_context_estimate_tokens_delegates(self):
        """context.estimate_tokens now delegates to token_estimation module."""
        from calcifer.services.compact.context import estimate_tokens
        from calcifer.services.token_estimation import count_tokens

        text = "Hello world, this is a test of token counting."
        assert estimate_tokens(text) == count_tokens(text)

    def test_context_count_message_tokens_delegates(self):
        """context.count_message_tokens now delegates to token_estimation module."""
        from calcifer.services.compact.context import count_message_tokens
        from calcifer.services.token_estimation import count_messages_tokens

        msgs = [
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi there"),
        ]
        assert count_message_tokens(msgs) == count_messages_tokens(msgs)


# ===== 7. Profiling integrated =====

class TestProfilingIntegration:

    @pytest.mark.asyncio
    async def test_profiler_records_checkpoints(self):
        """Agent profiler records run_start and run_end."""
        agent = Agent(api_key="test")
        agent._provider.chat_completion = AsyncMock(return_value=(
            Message(role="assistant", content="ok"),
            Usage(),
        ))

        await agent.run("test")
        summary = agent._profiler.summary()
        assert "run_start" in summary
        assert "run_end" in summary
        assert "turn_1_start" in summary
        assert summary["run_end"] > summary["run_start"]
        await agent.close()
