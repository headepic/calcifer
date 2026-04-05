"""Integration tests: full agent loop with mocked LLM."""

from unittest.mock import AsyncMock, patch

import pytest

from calcifer import Agent, CalciferConfig, Message, ToolCall, Usage, tool
from calcifer.tools.BashTool import BashTool
from calcifer.tools.FileReadTool import FileReadTool


@pytest.mark.asyncio
async def test_agent_with_orchestrator():
    """Agent uses orchestrator for parallel tool execution."""

    @tool(name="fast_read", description="Fast read", is_concurrency_safe=True)
    def fast_read(path: str) -> str:
        return f"contents of {path}"

    config = CalciferConfig(api_key="test")
    agent = Agent(config=config, tools=[fast_read])

    # Turn 1: LLM requests 3 parallel reads
    turn1 = (
        Message(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(id="1", function_name="fast_read", arguments='{"path":"a.txt"}'),
                ToolCall(id="2", function_name="fast_read", arguments='{"path":"b.txt"}'),
                ToolCall(id="3", function_name="fast_read", arguments='{"path":"c.txt"}'),
            ],
        ),
        Usage(),
    )
    turn2 = (
        Message(role="assistant", content="Read all 3 files."),
        Usage(),
    )

    with patch.object(agent._provider, "chat_completion", new_callable=AsyncMock) as mock:
        mock.side_effect = [turn1, turn2]
        result = await agent.run("Read a.txt, b.txt, c.txt")

    assert result.turn_count == 2
    tool_msgs = [m for m in result.messages if m.role == "tool"]
    assert len(tool_msgs) == 3
    assert all("contents of" in m.content for m in tool_msgs)


@pytest.mark.asyncio
async def test_agent_with_builtin_tools(tmp_path):
    """Agent uses built-in file tools."""
    test_file = tmp_path / "hello.txt"
    test_file.write_text("Hello World\nLine 2\n")

    config = CalciferConfig(api_key="test")
    agent = Agent(config=config, tools=[FileReadTool()])

    turn1 = (
        Message(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="1",
                    function_name="file_read",
                    arguments=f'{{"file_path": "{test_file}"}}',
                ),
            ],
        ),
        Usage(),
    )
    turn2 = (
        Message(role="assistant", content="The file contains 'Hello World'."),
        Usage(),
    )

    with patch.object(agent._provider, "chat_completion", new_callable=AsyncMock) as mock:
        mock.side_effect = [turn1, turn2]
        result = await agent.run("Read hello.txt")

    tool_msgs = [m for m in result.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "Hello World" in tool_msgs[0].content


@pytest.mark.asyncio
async def test_agent_multi_turn_tool_chain():
    """Agent chains multiple tool calls across turns."""

    @tool(name="step1", description="Step 1")
    def step1() -> str:
        return "result_from_step1"

    @tool(name="step2", description="Step 2")
    def step2(input: str) -> str:
        return f"processed_{input}"

    config = CalciferConfig(api_key="test")
    agent = Agent(config=config, tools=[step1, step2])

    turn1 = (
        Message(
            role="assistant", content=None,
            tool_calls=[ToolCall(id="1", function_name="step1", arguments="{}")],
        ),
        Usage(),
    )
    turn2 = (
        Message(
            role="assistant", content=None,
            tool_calls=[
                ToolCall(id="2", function_name="step2", arguments='{"input":"result_from_step1"}'),
            ],
        ),
        Usage(),
    )
    turn3 = (
        Message(role="assistant", content="Done: processed_result_from_step1"),
        Usage(),
    )

    with patch.object(agent._provider, "chat_completion", new_callable=AsyncMock) as mock:
        mock.side_effect = [turn1, turn2, turn3]
        result = await agent.run("Do the multi-step thing")

    assert result.turn_count == 3
    assert "processed_result_from_step1" in result.final_text


@pytest.mark.asyncio
async def test_agent_context_manager():
    """Agent works as async context manager."""
    config = CalciferConfig(api_key="test")

    async with Agent(config=config) as agent:
        with patch.object(agent._provider, "chat_completion", new_callable=AsyncMock) as mock:
            mock.return_value = (
                Message(role="assistant", content="Hi!"),
                Usage(),
            )
            result = await agent.run("Hello")
            assert result.final_text == "Hi!"


@pytest.mark.asyncio
async def test_agent_skill_application():
    """Agent applies skill to modify conversation."""
    from calcifer.skills import SkillDefinition

    config = CalciferConfig(api_key="test")

    @tool(name="bash", description="bash")
    def bash() -> str:
        return ""

    @tool(name="file_read", description="read")
    def file_read() -> str:
        return ""

    agent = Agent(config=config, tools=[bash, file_read])
    agent._skills = {
        "review": SkillDefinition(
            name="review",
            description="Code review",
            content="Review the code carefully.",
            allowed_tools=["file_read"],
        )
    }

    messages = [Message(role="user", content="Review this")]
    new_msgs, new_tools = agent.apply_skill("review", messages)

    assert any("review" in (m.content or "").lower() for m in new_msgs)
    assert len(new_tools) == 1
    assert new_tools[0].name == "file_read"
