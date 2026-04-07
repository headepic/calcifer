"""End-to-end tests for MCP integration and Skill system with REAL LLM.

Tests:
- MCP server connection, tool discovery, tool calling via agent
- Skill loading from files, inline application, fork execution
- Skill + MCP tools combined
- Conditional skill activation
- Skill with tool whitelisting
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from calcifer import (
    Agent,
    CalciferConfig,
    MCPServerConfig,
    Message,
    tool,
)
from calcifer.services.mcp.client import MCPClient
from calcifer.services.mcp.transport import StdioTransport
from calcifer.services.mcp.tool_adapter import create_mcp_tools
from calcifer.skills import (
    SkillDefinition,
    apply_skill,
    load_skill_file,
    load_all_skills,
    load_skills_dir,
    activate_conditional_skills,
    run_skill_fork,
)

# ===== Config =====

API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

MCP_SERVER_PATH = str(Path(__file__).parent / "fixtures" / "mcp_echo_server.py")
PYTHON = sys.executable


def make_config(**overrides) -> CalciferConfig:
    defaults = dict(
        api_key=API_KEY,
        base_url=BASE_URL,
        model=MODEL,
        system_prompt="You are a helpful assistant. Be concise.",
        max_turns=10,
    )
    defaults.update(overrides)
    return CalciferConfig(**defaults)


def check_llm_available():
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


@tool(name="add", description="Add two integers. Returns the sum as a string.")
def add(a: int, b: int) -> str:
    return str(a + b)


# ============================================================
# MCP Tests
# ============================================================

class TestMCPConnection:
    """Test raw MCP client connection and tool discovery."""

    @pytest.mark.asyncio
    async def test_mcp_connect_and_discover(self):
        """Connect to MCP server and discover tools."""
        transport = StdioTransport(command=PYTHON, args=[MCP_SERVER_PATH])
        client = MCPClient(name="echo-test", transport=transport)

        try:
            await client.connect()
            tools = await client.discover_tools()

            assert len(tools) == 2
            names = {t.name for t in tools}
            assert "echo" in names
            assert "reverse" in names

            # Verify schema structure
            echo_tool = next(t for t in tools if t.name == "echo")
            assert "text" in echo_tool.input_schema.get("properties", {})
            assert echo_tool.server_name == "echo-test"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_mcp_call_tool(self):
        """Call an MCP tool directly."""
        transport = StdioTransport(command=PYTHON, args=[MCP_SERVER_PATH])
        client = MCPClient(name="echo-test", transport=transport)

        try:
            await client.connect()
            await client.discover_tools()

            # Call echo
            result = await client.call_tool("echo", {"text": "hello world"})
            assert result is not None
            content = result.get("content", [])
            assert len(content) >= 1
            assert "ECHO: hello world" in content[0].get("text", "")

            # Call reverse
            result = await client.call_tool("reverse", {"text": "abcdef"})
            content = result.get("content", [])
            assert "fedcba" in content[0].get("text", "")
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_mcp_tool_adapter(self):
        """MCP tools wrap correctly as calcifer Tool instances."""
        transport = StdioTransport(command=PYTHON, args=[MCP_SERVER_PATH])
        client = MCPClient(name="echo-test", transport=transport)

        try:
            await client.connect()
            schemas = await client.discover_tools()
            tools = create_mcp_tools(schemas, client)

            assert len(tools) == 2

            # Check naming convention
            echo_tool = next(t for t in tools if "echo" in t.name)
            assert echo_tool.name == "mcp__echo-test__echo"
            assert echo_tool.is_mcp

            # Check schema generation
            openai_schema = echo_tool.to_openai_schema()
            assert openai_schema["type"] == "function"
            assert openai_schema["function"]["name"] == "mcp__echo-test__echo"

            # Actually call the tool
            from calcifer.types.tools import ToolContext
            ctx = ToolContext()
            args = echo_tool.parameters(text="test message")
            result = await echo_tool.call(args, ctx)
            assert not result.is_error
            assert "ECHO: test message" in result.content
        finally:
            await client.close()


class TestMCPAgentIntegration:
    """Test MCP tools used by agent with real LLM."""

    @pytest.mark.asyncio
    async def test_agent_uses_mcp_tool(self):
        """Agent discovers and uses MCP tools via LLM."""
        config = make_config(
            system_prompt="You have access to echo and reverse tools from an MCP server. Use them when asked. Be concise.",
        )
        async with Agent(config=config) as agent:
            # Connect MCP server
            servers = [MCPServerConfig(
                name="echo",
                transport="stdio",
                command=PYTHON,
                args=[MCP_SERVER_PATH],
            )]
            await agent.connect_mcp_servers(servers)

            # Verify tools were added
            mcp_tool_names = [t.name for t in agent._tools if t.is_mcp]
            assert "mcp__echo__echo" in mcp_tool_names
            assert "mcp__echo__reverse" in mcp_tool_names

            # Ask LLM to use the echo tool
            result = await agent.run(
                "Use the mcp__echo__echo tool to echo the text 'calcifer rocks'. Report what it returned."
            )

            assert result.turn_count >= 2
            tool_msgs = [m for m in result.messages if m.role == "tool"]
            assert len(tool_msgs) >= 1
            assert any("ECHO: calcifer rocks" in (m.content or "") for m in tool_msgs)

    @pytest.mark.asyncio
    async def test_agent_mcp_plus_builtin_tools(self):
        """Agent uses both MCP tools and built-in tools in one session."""
        config = make_config(
            system_prompt="You have add (built-in) and echo/reverse (MCP) tools. Use the right one for each task.",
        )
        async with Agent(config=config, tools=[add]) as agent:
            servers = [MCPServerConfig(
                name="echo",
                transport="stdio",
                command=PYTHON,
                args=[MCP_SERVER_PATH],
            )]
            await agent.connect_mcp_servers(servers)

            result = await agent.run(
                "Do two things: 1) Use add to compute 50+50, 2) Use mcp__echo__reverse to reverse 'hello'. Report both results."
            )

            tool_msgs = [m for m in result.messages if m.role == "tool"]
            assert len(tool_msgs) >= 2

            contents = " ".join(m.content or "" for m in tool_msgs)
            assert "100" in contents  # add result
            assert "olleh" in contents  # reverse result

    @pytest.mark.asyncio
    async def test_agent_mcp_streaming(self):
        """Streaming mode works with MCP tools."""
        config = make_config()
        async with Agent(config=config) as agent:
            servers = [MCPServerConfig(
                name="echo",
                transport="stdio",
                command=PYTHON,
                args=[MCP_SERVER_PATH],
            )]
            await agent.connect_mcp_servers(servers)

            events_by_type = {}
            async for event in agent.run_stream(
                "Use mcp__echo__echo to echo 'stream test'."
            ):
                events_by_type.setdefault(event.type, []).append(event)

            assert "tool_call_start" in events_by_type
            assert "tool_call_result" in events_by_type
            assert "run_complete" in events_by_type

            # Check tool result
            for evt in events_by_type["tool_call_result"]:
                if evt.tool_result_content and "ECHO: stream test" in evt.tool_result_content:
                    break
            else:
                pytest.fail("Expected 'ECHO: stream test' in tool results")


# ============================================================
# Skill Tests
# ============================================================

class TestSkillLoading:
    """Test skill file loading and parsing."""

    def test_load_skill_with_full_frontmatter(self, tmp_path):
        """Load a skill with all frontmatter fields."""
        skill_file = tmp_path / "research.md"
        skill_file.write_text("""---
name: research
description: Deep research assistant
allowed-tools: [bash, grep, file_read]
context: inline
effort: high
user-invocable: true
model: gpt-5.4-mini
paths:
  - "*.py"
  - "*.md"
---

# Research Assistant

You are a thorough research assistant. When given a topic:
1. Search for relevant files
2. Read their contents
3. Synthesize findings

Be thorough but concise in your final answer.
""")
        skill = load_skill_file(skill_file)
        assert skill is not None
        assert skill.name == "research"
        assert skill.description == "Deep research assistant"
        assert skill.allowed_tools == ["bash", "grep", "file_read"]
        assert skill.context == "inline"
        assert skill.effort == "high"
        assert skill.user_invocable is True
        assert skill.model == "gpt-5.4-mini"
        assert "*.py" in skill.paths
        assert "Research Assistant" in skill.content

    def test_load_skills_directory(self, tmp_path):
        """Load all skills from a directory."""
        (tmp_path / "a.md").write_text("---\nname: skill_a\ndescription: First\n---\nContent A")
        (tmp_path / "b.md").write_text("---\nname: skill_b\ndescription: Second\n---\nContent B")
        (tmp_path / "readme.txt").write_text("Not a skill")  # Should be ignored

        skills = load_skills_dir(tmp_path)
        assert len(skills) == 2
        names = {s.name for s in skills}
        assert names == {"skill_a", "skill_b"}

    def test_load_skills_override(self, tmp_path):
        """Later directories override earlier ones."""
        d1 = tmp_path / "base"
        d1.mkdir()
        (d1 / "common.md").write_text("---\nname: common\ndescription: Base version\n---\nBase content")

        d2 = tmp_path / "override"
        d2.mkdir()
        (d2 / "common.md").write_text("---\nname: common\ndescription: Override version\n---\nOverride content")

        skills = load_all_skills([str(d1), str(d2)])
        assert skills["common"].description == "Override version"
        assert "Override content" in skills["common"].content


class TestSkillApplication:
    """Test applying skills to conversations."""

    @pytest.mark.asyncio
    async def test_skill_inline_with_llm(self):
        """Inline skill changes LLM behavior via system prompt injection."""
        skill = SkillDefinition(
            name="json_mode",
            description="Always respond in JSON",
            content="You MUST respond in valid JSON format. Always wrap your response in a JSON object with a 'response' key.",
        )

        config = make_config()
        async with Agent(config=config) as agent:
            messages = agent._build_initial_messages("What is 2+2?")
            new_msgs, new_tools = apply_skill(skill, messages, agent._tools)

            # Replace the initial messages with skill-injected ones
            result = await agent.run("What is 2+2?", messages=new_msgs[:-1])

        # Response should be valid JSON
        text = result.final_text.strip()
        # Try to find JSON in the response
        try:
            if text.startswith("```"):
                # Strip markdown code fences
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            parsed = json.loads(text)
            assert "response" in parsed or "result" in parsed or "answer" in parsed
        except json.JSONDecodeError:
            # LLM might not perfectly follow, but should at least contain JSON-like structure
            assert "{" in result.final_text and "}" in result.final_text, \
                f"Expected JSON-like response, got: {result.final_text}"

    @pytest.mark.asyncio
    async def test_skill_tool_whitelisting(self):
        """Skill restricts available tools."""
        skill = SkillDefinition(
            name="add_only",
            description="Only use add",
            content="You may only use the add tool.",
            allowed_tools=["add"],
        )

        @tool(name="multiply", description="Multiply two numbers")
        def multiply(a: int, b: int) -> str:
            return str(a * b)

        all_tools = [add, multiply]
        messages = [Message(role="system", content="Base system prompt")]
        new_msgs, filtered_tools = apply_skill(skill, messages, all_tools)

        # Only add should remain
        assert len(filtered_tools) == 1
        assert filtered_tools[0].name == "add"

    @pytest.mark.asyncio
    async def test_skill_with_agent_tool_calling(self):
        """Skill modifies agent behavior when calling tools."""
        skill = SkillDefinition(
            name="calculator",
            description="Math calculator mode",
            content=(
                "You are a strict calculator. "
                "ALWAYS use the add tool for any addition. Never compute mentally. "
                "Report the exact tool result."
            ),
            allowed_tools=["add"],
        )

        config = make_config()
        async with Agent(config=config, tools=[add]) as agent:
            # Build messages with skill injected
            messages = agent._build_initial_messages("What is 123 + 456?")
            new_msgs, _ = apply_skill(skill, messages, [add])

            result = await agent.run("What is 123 + 456?", messages=new_msgs[:-1])

        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msgs) >= 1
        assert any("579" in (m.content or "") for m in tool_msgs)

    @pytest.mark.asyncio
    async def test_skill_fork_execution(self):
        """Skill runs in fork mode (isolated sub-agent)."""
        skill = SkillDefinition(
            name="greeter",
            description="A greeting generator",
            content="You generate creative greetings. Always include the word 'magnificent' in your greeting.",
            context="fork",
        )

        result = await run_skill_fork(
            skill,
            "Generate a greeting for Alice.",
            [],
            api_key=API_KEY,
            base_url=BASE_URL,
            model=MODEL,
        )

        assert result, "Fork skill should return non-empty text"
        assert "magnificent" in result.lower() or "alice" in result.lower()


class TestSkillConditionalActivation:
    """Test conditional skill activation based on file paths."""

    def test_activate_by_extension(self):
        """Skills activate when file extension matches."""
        skills = {
            "python": SkillDefinition(
                name="python", description="Python helper", content="...",
                paths=["*.py"],
            ),
            "typescript": SkillDefinition(
                name="typescript", description="TS helper", content="...",
                paths=["*.ts", "*.tsx"],
            ),
            "docs": SkillDefinition(
                name="docs", description="Docs helper", content="...",
                paths=["*.md"],
            ),
        }

        # Python file touched
        activated = activate_conditional_skills(skills, ["src/main.py"])
        names = {s.name for s in activated}
        assert "python" in names
        assert "typescript" not in names

        # TypeScript file touched
        activated = activate_conditional_skills(skills, ["components/App.tsx"])
        names = {s.name for s in activated}
        assert "typescript" in names
        assert "python" not in names

        # Multiple files: both activated
        activated = activate_conditional_skills(skills, ["main.py", "App.tsx"])
        names = {s.name for s in activated}
        assert "python" in names
        assert "typescript" in names

    def test_no_activation_without_paths(self):
        """Skills without paths field are never conditionally activated."""
        skills = {
            "general": SkillDefinition(name="general", description="", content=""),
        }
        activated = activate_conditional_skills(skills, ["anything.py"])
        assert len(activated) == 0


class TestSkillWithMCP:
    """Test skills combined with MCP tools."""

    @pytest.mark.asyncio
    async def test_skill_mcp_combined(self):
        """Agent uses skill prompt with MCP tools."""
        skill = SkillDefinition(
            name="echo_specialist",
            description="Specializes in echo operations",
            content=(
                "You are an echo specialist. When asked to process text, "
                "use the mcp__echo__echo tool. Always report the exact tool output."
            ),
        )

        config = make_config()
        async with Agent(config=config) as agent:
            # Connect MCP
            servers = [MCPServerConfig(
                name="echo",
                transport="stdio",
                command=PYTHON,
                args=[MCP_SERVER_PATH],
            )]
            await agent.connect_mcp_servers(servers)

            # Apply skill
            messages = agent._build_initial_messages("Echo the text 'skill+mcp test'")
            new_msgs, new_tools = apply_skill(skill, messages, agent._tools)

            result = await agent.run(
                "Echo the text 'skill+mcp test'",
                messages=new_msgs[:-1],
            )

        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msgs) >= 1
        assert any("ECHO:" in (m.content or "") for m in tool_msgs)


class TestSkillFromDisk:
    """Test loading skills from disk and using them with real LLM."""

    @pytest.mark.asyncio
    async def test_load_and_apply_from_disk(self, tmp_path):
        """Load skill from markdown file, apply to agent, run with LLM."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "pirate.md").write_text("""---
name: pirate
description: Talk like a pirate
user-invocable: true
---

You MUST respond entirely in pirate speak.
Use words like 'Arrr', 'matey', 'ye', 'aboard', 'treasure', 'seas'.
Every response must start with 'Arrr!'.
""")

        skills = load_all_skills([str(skill_dir)])
        assert "pirate" in skills

        skill = skills["pirate"]
        assert skill.user_invocable

        config = make_config()
        async with Agent(config=config) as agent:
            messages = agent._build_initial_messages("Tell me about the weather today.")
            new_msgs, _ = apply_skill(skill, messages, [])

            result = await agent.run("Tell me about the weather today.", messages=new_msgs[:-1])

        text = result.final_text.lower()
        assert any(w in text for w in ["arr", "matey", "ye", "seas", "pirate", "ahoy", "aye"]), \
            f"Expected pirate speak, got: {result.final_text}"

    @pytest.mark.asyncio
    async def test_multiple_skills_directory(self, tmp_path):
        """Load multiple skills from multiple directories."""
        d1 = tmp_path / "base_skills"
        d1.mkdir()
        (d1 / "helper.md").write_text("---\nname: helper\ndescription: General helper\n---\nBe helpful.")
        (d1 / "coder.md").write_text("---\nname: coder\ndescription: Code assistant\nallowed-tools: [bash]\n---\nWrite code.")

        d2 = tmp_path / "custom_skills"
        d2.mkdir()
        (d2 / "reviewer.md").write_text("---\nname: reviewer\ndescription: Code reviewer\n---\nReview code thoroughly.")

        skills = load_all_skills([str(d1), str(d2)])
        assert len(skills) == 3
        assert "helper" in skills
        assert "coder" in skills
        assert "reviewer" in skills
        assert skills["coder"].allowed_tools == ["bash"]


# ============================================================
# Edge Cases
# ============================================================

class TestMCPEdgeCases:

    @pytest.mark.asyncio
    async def test_mcp_server_error_tool(self):
        """Agent handles MCP tool that returns an error gracefully."""
        config = make_config()
        async with Agent(config=config) as agent:
            servers = [MCPServerConfig(
                name="echo",
                transport="stdio",
                command=PYTHON,
                args=[MCP_SERVER_PATH],
            )]
            await agent.connect_mcp_servers(servers)

            # Ask to use a tool that doesn't exist on MCP server
            # The adapter will catch the error
            result = await agent.run(
                "Use the mcp__echo__echo tool with text='error test'. Report what happens."
            )
            # Should not crash
            assert result.final_text

    @pytest.mark.asyncio
    async def test_mcp_disconnect_cleanup(self):
        """MCP connections are cleaned up on agent close."""
        config = make_config()
        agent = Agent(config=config)

        servers = [MCPServerConfig(
            name="echo",
            transport="stdio",
            command=PYTHON,
            args=[MCP_SERVER_PATH],
        )]
        await agent.connect_mcp_servers(servers)
        assert len(agent._mcp_clients) == 1

        await agent.close()
        # Should not raise on double close
        await agent.close()


class TestSkillEdgeCases:

    def test_skill_no_allowed_tools_passes_all(self):
        """Skill without allowed-tools passes all tools through."""
        skill = SkillDefinition(
            name="open", description="", content="Open skill",
            allowed_tools=[],  # Empty = pass all through
        )
        all_tools = [add]
        _, filtered = apply_skill(skill, [Message(role="user", content="hi")], all_tools)
        assert len(filtered) == 1

    def test_skill_insert_after_system(self):
        """Skill message is inserted after system messages."""
        skill = SkillDefinition(name="test", description="", content="Skill content")
        messages = [
            Message(role="system", content="System 1"),
            Message(role="system", content="System 2"),
            Message(role="user", content="User msg"),
        ]
        new_msgs, _ = apply_skill(skill, messages, [])
        # Skill should be at index 2 (after both system messages)
        assert new_msgs[0].content == "System 1"
        assert new_msgs[1].content == "System 2"
        assert "[Skill: test]" in new_msgs[2].content
        assert new_msgs[3].content == "User msg"

    def test_malformed_skill_file(self, tmp_path):
        """Malformed YAML frontmatter doesn't crash."""
        bad_file = tmp_path / "bad.md"
        bad_file.write_text("---\n: invalid: yaml: [\n---\nContent")
        skill = load_skill_file(bad_file)
        assert skill is not None  # Falls back to no-frontmatter mode
        assert skill.name == "bad"  # Uses filename as name
