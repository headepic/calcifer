"""End-to-end tests with REAL LLM API.

Validates every major agent runner pathway against a live LLM endpoint.
Covers: simple chat, tool calling, multi-tool chains, streaming,
session persistence, cost tracking, stop hooks, skills, coordinator,
side queries, built-in tools integration.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest

from calcifer import (
    Agent,
    AgentResult,
    CalciferConfig,
    Message,
    StreamEvent,
    ToolCall,
    Usage,
    tool,
)
from calcifer.coordinator import Coordinator, CoordinatorConfig
from calcifer.services.side_query import side_query, classify
from calcifer.skills import SkillDefinition, apply_skill

# ===== Config =====

API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def make_config(**overrides) -> CalciferConfig:
    defaults = dict(
        api_key=API_KEY,
        base_url=BASE_URL,
        model=MODEL,
        system_prompt="You are a helpful assistant. Be concise. Answer in one sentence when possible.",
        max_turns=10,
    )
    defaults.update(overrides)
    return CalciferConfig(**defaults)


# ===== Tools =====

@tool(name="add", description="Add two integers. Returns the sum as a string.")
def add(a: int, b: int) -> str:
    return str(a + b)


@tool(name="multiply", description="Multiply two integers. Returns the product as a string.")
def multiply(a: int, b: int) -> str:
    return str(a * b)


@tool(name="get_weather", description="Get weather for a city. Returns JSON.", is_concurrency_safe=True, is_read_only=True)
def get_weather(city: str) -> str:
    fake_data = {"city": city, "temp_c": 22, "condition": "sunny"}
    return json.dumps(fake_data)


@tool(name="string_length", description="Return the length of a string.", is_concurrency_safe=True, is_read_only=True)
def string_length(text: str) -> str:
    return str(len(text))


# ===== Helper =====

def check_llm_available():
    """Quick check that the LLM endpoint is reachable."""
    import httpx
    try:
        r = httpx.get(f"{BASE_URL}/models", headers={"Authorization": f"Bearer {API_KEY}"}, timeout=5)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not check_llm_available(),
    reason="LLM endpoint not available at " + BASE_URL,
)


# ===== 1. Simple Text Response =====

class TestSimpleChat:

    @pytest.mark.asyncio
    async def test_simple_question(self):
        """LLM answers a factual question without tools."""
        async with Agent(config=make_config()) as agent:
            result = await agent.run("What is the capital of France? Answer in one word.")

        assert result.final_text, "Expected non-empty response"
        assert result.turn_count == 1
        assert result.usage.total_tokens > 0
        assert "paris" in result.final_text.lower()

    @pytest.mark.asyncio
    async def test_system_prompt_followed(self):
        """LLM follows system prompt instructions."""
        config = make_config(system_prompt="You must always answer with exactly the word 'PONG'. Nothing else.")
        async with Agent(config=config) as agent:
            result = await agent.run("ping")

        assert "pong" in result.final_text.lower()

    @pytest.mark.asyncio
    async def test_continue_conversation(self):
        """Agent can continue from previous messages."""
        config = make_config()
        async with Agent(config=config) as agent:
            r1 = await agent.run("My name is Alice. Remember that.")
            r2 = await agent.run("What is my name?", messages=r1.messages)

        assert "alice" in r2.final_text.lower()


# ===== 2. Tool Calling =====

class TestToolCalling:

    @pytest.mark.asyncio
    async def test_single_tool_call(self):
        """LLM calls the add tool and returns the correct answer."""
        async with Agent(config=make_config(), tools=[add]) as agent:
            result = await agent.run("Use the add tool to compute 17 + 25. Report the exact result.")

        assert result.turn_count >= 2, f"Expected >=2 turns, got {result.turn_count}"
        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msgs) >= 1, "Expected at least one tool result"
        assert "42" in result.final_text or any("42" in (m.content or "") for m in result.messages)

    @pytest.mark.asyncio
    async def test_multi_tool_chain(self):
        """LLM chains multiple tool calls: add then multiply."""
        config = make_config(
            system_prompt="You are a calculator. Use tools for ALL calculations. Never compute mentally."
        )
        async with Agent(config=config, tools=[add, multiply]) as agent:
            result = await agent.run(
                "Calculate (3 + 4) * 5. First use add for 3+4, then multiply the result by 5."
            )

        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msgs) >= 2, f"Expected >=2 tool calls, got {len(tool_msgs)}"
        # Intermediate result 7 and final 35 should appear
        tool_contents = " ".join(m.content or "" for m in tool_msgs)
        assert "7" in tool_contents
        assert "35" in tool_contents or "35" in result.final_text

    @pytest.mark.asyncio
    async def test_tool_error_recovery(self):
        """LLM handles tool returning error gracefully."""

        @tool(name="buggy", description="A tool that always fails with an error message.")
        def buggy(input: str) -> str:
            raise ValueError(f"Simulated error for: {input}")

        async with Agent(config=make_config(), tools=[buggy, add]) as agent:
            result = await agent.run(
                "Try using the buggy tool with input 'test'. "
                "If it fails, use the add tool to compute 1+1 instead."
            )

        # Should recover and either mention the error or use add
        assert result.turn_count >= 2
        assert result.final_text  # Non-empty response


# ===== 3. Streaming =====

class TestStreaming:

    @pytest.mark.asyncio
    async def test_stream_text(self):
        """Streaming yields text_delta events and run_complete."""
        async with Agent(config=make_config()) as agent:
            text_chunks = []
            lifecycle = {"turn_start": 0, "turn_end": 0, "run_complete": False}

            async for event in agent.run_stream("Say hello in exactly 5 words."):
                if event.type == "text_delta" and event.text:
                    text_chunks.append(event.text)
                elif event.type == "turn_start":
                    lifecycle["turn_start"] += 1
                elif event.type == "turn_end":
                    lifecycle["turn_end"] += 1
                elif event.type == "run_complete":
                    lifecycle["run_complete"] = True
                    final_result = event.result

        full_text = "".join(text_chunks)
        assert len(full_text) > 0, "No streamed text received"
        assert lifecycle["turn_start"] >= 1
        assert lifecycle["turn_start"] == lifecycle["turn_end"]
        assert lifecycle["run_complete"]
        assert final_result.turn_count >= 1
        assert final_result.usage.total_tokens > 0

    @pytest.mark.asyncio
    async def test_stream_with_tools(self):
        """Streaming with tool calls emits tool_call_start and tool_call_result."""
        async with Agent(config=make_config(), tools=[add]) as agent:
            events_by_type = {}

            async for event in agent.run_stream("Use the add tool to compute 10 + 20."):
                events_by_type.setdefault(event.type, []).append(event)

        assert "tool_call_start" in events_by_type, f"Missing tool_call_start. Got: {list(events_by_type.keys())}"
        assert "tool_call_result" in events_by_type, f"Missing tool_call_result. Got: {list(events_by_type.keys())}"
        assert "run_complete" in events_by_type

        # Verify tool result content
        for evt in events_by_type["tool_call_result"]:
            if evt.tool_result_content and "30" in evt.tool_result_content:
                break
        else:
            # Check final text
            rc = events_by_type["run_complete"][0]
            assert "30" in rc.result.final_text or any(
                "30" in (m.content or "") for m in rc.result.messages if m.role == "tool"
            )


# ===== 4. Cost Tracking =====

class TestCostTracking:

    @pytest.mark.asyncio
    async def test_cost_tracked(self):
        """Cost tracker records usage after a run."""
        async with Agent(config=make_config(), tools=[add]) as agent:
            await agent.run("What is 2 + 2? Use the add tool.")

            cost = agent.cost_tracker.get_cost()
            summary = agent.cost_tracker.summary()
            total_usage = agent.cost_tracker.get_total_usage()

        # Cost may be 0 if model not in pricing table, but usage must exist
        assert total_usage.prompt_tokens > 0
        assert total_usage.completion_tokens > 0
        assert len(summary) > 0  # At least one model entry


# ===== 5. Session Persistence =====

class TestSessionPersistence:

    @pytest.mark.asyncio
    async def test_save_and_resume(self):
        """Session is saved to disk and can be loaded back."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = make_config()

            # First run - save session
            async with Agent(config=config) as agent:
                agent.enable_session_persistence(tmpdir)
                r1 = await agent.run("My favorite number is 42. Remember it.")
                session_id = agent.session_id

            # Verify session file exists
            session_files = list(Path(tmpdir).glob("*.json"))
            assert len(session_files) == 1

            # Second run - load and continue
            async with Agent(config=config) as agent2:
                agent2.enable_session_persistence(tmpdir)
                loaded = agent2._session.load(session_id)
                assert loaded is not None
                messages, usage, turn_count = loaded
                assert len(messages) >= 2  # At least user + assistant

                r2 = await agent2.run("What is my favorite number?", messages=messages)
                assert "42" in r2.final_text


# ===== 6. Stop Hooks =====

class TestStopHooks:

    @pytest.mark.asyncio
    async def test_cost_budget_stop(self):
        """Stop hook terminates agent after the first tool-execution turn.

        Note: LLM may emit multiple parallel tool calls in a single turn.
        The stop hook fires after ALL tool calls in that turn complete.
        So we expect the agent to stop after turn 1 (the tool turn), without
        making a second LLM call to produce a final text response.
        """
        config = make_config()
        stopped_by_hook = False

        async with Agent(config=config, tools=[add]) as agent:
            def budget_hook(messages, context):
                nonlocal stopped_by_hook
                tool_msgs = [m for m in messages if m.role == "tool"]
                if len(tool_msgs) >= 1:
                    stopped_by_hook = True
                    return True  # Stop!
                return False

            agent.register_stop_hook(budget_hook)
            result = await agent.run(
                "Use add to compute 1+1, then 2+2, then 3+3. Report all results."
            )

        assert stopped_by_hook, "Stop hook should have fired"
        # Agent should stop after the tool turn (turn 1) — it never gets
        # a chance to make the second LLM call for the final text.
        # The LLM may batch all 3 tool calls in one turn or split them.
        assert result.turn_count <= 2, f"Expected <=2 turns, got {result.turn_count}"
        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msgs) >= 1, "Expected at least one tool call"


# ===== 7. Max Turns =====

class TestMaxTurns:

    @pytest.mark.asyncio
    async def test_max_turns_enforced(self):
        """Agent respects max_turns limit."""
        config = make_config(max_turns=2)

        @tool(name="counter", description="Increment a counter. Always call this tool again after getting a result.")
        def counter(n: int) -> str:
            return str(n + 1)

        async with Agent(config=config, tools=[counter]) as agent:
            result = await agent.run("Start by calling counter with n=0, then keep calling it.")

        assert result.turn_count <= 2


# ===== 8. Abort =====

class TestAbort:

    @pytest.mark.asyncio
    async def test_abort_during_run(self):
        """Abort stops agent after current turn."""
        config = make_config(max_turns=20)

        async with Agent(config=config, tools=[add]) as agent:
            call_count = 0
            original_execute = agent._execute_tools

            async def tracked_execute(tool_calls, context):
                nonlocal call_count
                call_count += 1
                result = await original_execute(tool_calls, context)
                agent.abort()  # Abort after first tool execution
                return result

            agent._execute_tools = tracked_execute
            result = await agent.run(
                "Use add to compute 1+1, then 2+2, then 3+3. Do all three."
            )

        # Should stop after first tool execution
        assert call_count == 1


# ===== 9. Side Query =====

class TestSideQuery:

    @pytest.mark.asyncio
    async def test_side_query(self):
        """Side query makes a standalone LLM call."""
        from calcifer.services.api.provider import LLMProvider

        provider = LLMProvider(
            api_key=API_KEY, base_url=BASE_URL, model=MODEL,
        )
        try:
            text, usage = await side_query(
                provider,
                "What is 2+2? Answer with just the number.",
                system_prompt="You are a math assistant. Be concise.",
            )
            assert "4" in text
            assert usage.total_tokens > 0
        finally:
            await provider.close()

    @pytest.mark.asyncio
    async def test_classify(self):
        """Classify text into categories via LLM."""
        from calcifer.services.api.provider import LLMProvider

        provider = LLMProvider(
            api_key=API_KEY, base_url=BASE_URL, model=MODEL,
        )
        try:
            result = await classify(
                provider,
                "The product broke after one day of use. Terrible!",
                ["positive", "negative", "neutral"],
            )
            assert "negative" in result.lower()
        finally:
            await provider.close()


# ===== 10. Skill System =====

class TestSkillSystem:

    @pytest.mark.asyncio
    async def test_skill_inline(self):
        """Skill injection modifies agent behavior."""
        skill = SkillDefinition(
            name="pirate",
            description="Talk like a pirate",
            content="You MUST respond in pirate speak. Use 'Arrr', 'matey', 'ye', etc.",
        )

        config = make_config()
        async with Agent(config=config) as agent:
            messages = [Message(role="user", content="How are you today?")]
            messages = agent._build_initial_messages("How are you today?")
            new_msgs, new_tools = apply_skill(skill, messages, [])
            result = await agent.run("How are you today?", messages=new_msgs[:-1])

        text = result.final_text.lower()
        # Should contain pirate-like language
        assert any(w in text for w in ["arr", "matey", "ye", "ahoy", "sail", "pirate", "aye"]), \
            f"Expected pirate speak, got: {result.final_text}"


# ===== 11. Built-in Tools with Real LLM =====

class TestBuiltinToolsIntegration:

    @pytest.mark.asyncio
    async def test_bash_via_agent(self):
        """Agent uses bash tool to run a command."""
        from calcifer.tools import BashTool

        config = make_config(
            system_prompt="You are a shell assistant. Use the bash tool to execute commands. Be concise."
        )
        async with Agent(config=config, tools=[BashTool()]) as agent:
            result = await agent.run("Use bash to run: echo 'hello from calcifer'")

        assert result.turn_count >= 2
        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msgs) >= 1
        assert any("hello from calcifer" in (m.content or "") for m in tool_msgs)

    @pytest.mark.asyncio
    async def test_file_ops_via_agent(self):
        """Agent uses file tools to write and read a file."""
        from calcifer.tools import FileWriteTool, FileReadTool

        with tempfile.TemporaryDirectory() as tmpdir:
            config = make_config(
                system_prompt=(
                    f"You are a file assistant. Use file_write and file_read tools. "
                    f"All file paths must be under {tmpdir}. Be concise."
                ),
            )
            async with Agent(config=config, tools=[FileWriteTool(), FileReadTool()]) as agent:
                test_file = str(Path(tmpdir) / "test.txt")
                result = await agent.run(
                    f"Write 'Hello Calcifer!' to {test_file}, then read it back."
                )

            # Verify file was actually written
            assert Path(test_file).exists(), f"File {test_file} should exist"
            content = Path(test_file).read_text()
            assert "Hello Calcifer" in content


# ===== 12. Parallel Tool Calls =====

class TestParallelTools:

    @pytest.mark.asyncio
    async def test_concurrent_safe_tools(self):
        """LLM invokes multiple concurrency-safe tools in parallel."""
        config = make_config(
            system_prompt="You are a weather assistant. You can check weather for multiple cities at once using parallel tool calls."
        )
        async with Agent(config=config, tools=[get_weather]) as agent:
            result = await agent.run(
                "Get the weather for Tokyo, London, and Paris. Make all three calls."
            )

        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msgs) >= 3, f"Expected >=3 tool calls, got {len(tool_msgs)}"
        cities_found = set()
        for m in tool_msgs:
            content = m.content or ""
            for city in ["Tokyo", "London", "Paris"]:
                if city.lower() in content.lower():
                    cities_found.add(city)
        assert len(cities_found) == 3, f"Expected 3 cities, found {cities_found}"


# ===== 13. Coordinator (Multi-Agent) =====

class TestCoordinator:

    @pytest.mark.asyncio
    async def test_coordinator_workers(self):
        """Coordinator dispatches work to multiple workers."""
        config = make_config(max_turns=5)
        coord = Coordinator(config, [add], CoordinatorConfig(max_workers=3))

        results = await coord.run_workers([
            ("math1", "What is the result of using add tool with a=10, b=20? Just give the number."),
            ("math2", "What is the result of using add tool with a=100, b=200? Just give the number."),
        ], parallel=True)

        assert len(results) == 2
        for r in results:
            assert r.status == "completed", f"Worker {r.name} status: {r.status}, text: {r.result_text}"
            assert r.turn_count >= 1


# ===== 14. Context Management with Real LLM =====

class TestContextManagement:

    @pytest.mark.asyncio
    async def test_long_conversation(self):
        """Agent handles a multi-turn conversation without context errors."""
        config = make_config(max_turns=15)
        async with Agent(config=config, tools=[add]) as agent:
            messages = None
            for i in range(3):
                prompt = f"Use add to compute {i*10} + {i*10+1}."
                if messages:
                    result = await agent.run(prompt, messages=messages)
                else:
                    result = await agent.run(prompt)
                messages = result.messages

        # Should complete all turns without error
        assert result.turn_count >= 1
        assert len(result.messages) > 6  # Multiple turns of conversation


# ===== 15. Comprehensive Integration =====

class TestFullIntegration:

    @pytest.mark.asyncio
    async def test_full_agent_lifecycle(self):
        """Full lifecycle: tools + streaming + cost + session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = make_config()
            async with Agent(config=config, tools=[add, multiply]) as agent:
                agent.enable_session_persistence(tmpdir)

                # Non-streaming run with tools
                r1 = await agent.run("Use add to compute 7+3, then multiply the result by 2.")

                assert r1.turn_count >= 2
                tool_msgs = [m for m in r1.messages if m.role == "tool"]
                assert len(tool_msgs) >= 2

                # Streaming run continuing conversation
                events = []
                async for event in agent.run_stream(
                    "What were the intermediate results?",
                    messages=r1.messages,
                ):
                    events.append(event)

                run_complete = [e for e in events if e.type == "run_complete"]
                assert len(run_complete) == 1
                r2 = run_complete[0].result
                assert r2.final_text

                # Cost tracking
                total_usage = agent.cost_tracker.get_total_usage()
                assert total_usage.prompt_tokens > 0

                # Session saved
                session_files = list(Path(tmpdir).glob("*.json"))
                assert len(session_files) >= 1
