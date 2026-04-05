"""Comprehensive Skill system tests.

Covers every function and edge case in:
- skills/loader.py: _parse_frontmatter, load_skill_file, load_skills_dir,
    load_all_skills, discover_skill_dirs_for_paths, activate_conditional_skills,
    apply_token_budget
- skills/executor.py: apply_skill, run_skill_fork
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from calcifer import Agent, CalciferConfig, Message, Usage, tool
from calcifer.skills.loader import (
    SkillDefinition,
    _parse_frontmatter,
    load_skill_file,
    load_skills_dir,
    load_all_skills,
    discover_skill_dirs_for_paths,
    activate_conditional_skills,
    apply_token_budget,
    SKILL_DESCRIPTION_MAX_CHARS,
    SKILL_LIST_MAX_CHARS,
)
from calcifer.skills.executor import apply_skill, run_skill_fork
from calcifer.tool import FunctionTool, Tool
from calcifer.types.tools import ToolContext, ToolResult


@tool(name="add", description="Add two numbers")
def add_tool(a: int, b: int) -> str:
    return str(a + b)


@tool(name="grep", description="Search files")
def grep_tool(pattern: str) -> str:
    return f"found: {pattern}"


@tool(name="bash", description="Run command")
def bash_tool(command: str) -> str:
    return f"ran: {command}"


# ===================================================================
# _parse_frontmatter
# ===================================================================

class TestParseFrontmatter:

    def test_valid_frontmatter(self):
        text = "---\nname: test\ndescription: A test\n---\nBody content here"
        fm, body = _parse_frontmatter(text)
        assert fm == {"name": "test", "description": "A test"}
        assert body == "Body content here"

    def test_no_frontmatter(self):
        text = "Just plain markdown content"
        fm, body = _parse_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_single_separator(self):
        """Only one --- should not parse as frontmatter."""
        text = "---\nname: test\nno closing separator"
        fm, body = _parse_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_empty_frontmatter(self):
        text = "---\n---\nBody after empty frontmatter"
        fm, body = _parse_frontmatter(text)
        # yaml.safe_load("") returns None, which is not a dict
        assert fm == {}

    def test_non_dict_frontmatter(self):
        """Frontmatter that parses to a list should be rejected."""
        text = "---\n- item1\n- item2\n---\nBody"
        fm, body = _parse_frontmatter(text)
        assert fm == {}

    def test_invalid_yaml(self):
        text = "---\n: invalid: [yaml\n---\nBody"
        fm, body = _parse_frontmatter(text)
        assert fm == {}

    def test_multiline_body(self):
        text = "---\nname: test\n---\nLine 1\nLine 2\nLine 3"
        fm, body = _parse_frontmatter(text)
        assert fm == {"name": "test"}
        assert "Line 1\nLine 2\nLine 3" == body

    def test_frontmatter_with_complex_values(self):
        text = "---\nname: test\nallowed-tools:\n  - bash\n  - grep\npaths:\n  - '*.py'\n---\nBody"
        fm, body = _parse_frontmatter(text)
        assert fm["allowed-tools"] == ["bash", "grep"]
        assert fm["paths"] == ["*.py"]

    def test_body_contains_triple_dash(self):
        """Triple dashes inside body should not confuse parsing."""
        text = "---\nname: test\n---\nBody with --- dashes inside"
        fm, body = _parse_frontmatter(text)
        assert fm == {"name": "test"}
        assert "--- dashes inside" in body


# ===================================================================
# load_skill_file
# ===================================================================

class TestLoadSkillFile:

    def test_full_frontmatter(self, tmp_path):
        f = tmp_path / "skill.md"
        f.write_text("""---
name: researcher
description: Research assistant
allowed-tools: [bash, grep]
model: gpt-4o
context: fork
agent: research-agent
effort: high
user-invocable: true
paths:
  - "*.py"
  - "docs/*.md"
custom-field: extra
---

You are a research assistant.
""")
        skill = load_skill_file(f)
        assert skill is not None
        assert skill.name == "researcher"
        assert skill.description == "Research assistant"
        assert skill.allowed_tools == ["bash", "grep"]
        assert skill.model == "gpt-4o"
        assert skill.context == "fork"
        assert skill.agent == "research-agent"
        assert skill.effort == "high"
        assert skill.user_invocable is True
        assert skill.paths == ["*.py", "docs/*.md"]
        assert skill.source_path == str(f)
        assert skill.metadata == {"custom-field": "extra"}
        assert "research assistant" in skill.content.lower()

    def test_minimal_frontmatter(self, tmp_path):
        f = tmp_path / "simple.md"
        f.write_text("---\nname: simple\n---\nMinimal content.")
        skill = load_skill_file(f)
        assert skill.name == "simple"
        assert skill.description == ""
        assert skill.allowed_tools == []
        assert skill.model is None
        assert skill.context == "inline"
        assert skill.agent == "general-purpose"
        assert skill.user_invocable is False
        assert skill.paths == []

    def test_no_frontmatter(self, tmp_path):
        f = tmp_path / "plain.md"
        f.write_text("Just plain content, no frontmatter.")
        skill = load_skill_file(f)
        assert skill.name == "plain"  # Derived from filename
        assert skill.content == "Just plain content, no frontmatter."

    def test_name_defaults_to_stem(self, tmp_path):
        f = tmp_path / "my-cool-skill.md"
        f.write_text("---\ndescription: no name field\n---\ncontent")
        skill = load_skill_file(f)
        assert skill.name == "my-cool-skill"

    def test_nonexistent_file(self, tmp_path):
        f = tmp_path / "does_not_exist.md"
        skill = load_skill_file(f)
        assert skill is None

    def test_unreadable_file(self, tmp_path):
        f = tmp_path / "noperm.md"
        f.write_text("content")
        f.chmod(0o000)
        skill = load_skill_file(f)
        # Restore permissions for cleanup
        f.chmod(0o644)
        assert skill is None

    def test_allowed_tools_not_a_list(self, tmp_path):
        """If allowed-tools is a string, it should be treated as empty list."""
        f = tmp_path / "bad_tools.md"
        f.write_text("---\nname: bad\nallowed-tools: just_a_string\n---\ncontent")
        skill = load_skill_file(f)
        assert skill.allowed_tools == []

    def test_paths_not_a_list(self, tmp_path):
        """If paths is a string, it should be treated as empty list."""
        f = tmp_path / "bad_paths.md"
        f.write_text("---\nname: bad\npaths: just_a_string\n---\ncontent")
        skill = load_skill_file(f)
        assert skill.paths == []

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("")
        skill = load_skill_file(f)
        assert skill is not None
        assert skill.name == "empty"
        assert skill.content == ""

    def test_metadata_extraction(self, tmp_path):
        """Unknown frontmatter keys go into metadata."""
        f = tmp_path / "meta.md"
        f.write_text("---\nname: meta\ncustom_key: custom_value\nanother: 42\n---\ncontent")
        skill = load_skill_file(f)
        assert skill.metadata == {"custom_key": "custom_value", "another": 42}


# ===================================================================
# load_skills_dir / load_all_skills
# ===================================================================

class TestLoadSkillsDir:

    def test_load_from_directory(self, tmp_path):
        (tmp_path / "a.md").write_text("---\nname: alpha\n---\nAlpha content")
        (tmp_path / "b.md").write_text("---\nname: beta\n---\nBeta content")
        (tmp_path / "not_md.txt").write_text("Ignored")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "nested.md").write_text("---\nname: nested\n---\nNested")

        skills = load_skills_dir(tmp_path)
        names = {s.name for s in skills}
        assert names == {"alpha", "beta"}  # Non-recursive, ignores .txt and subdirs

    def test_load_nonexistent_dir(self):
        skills = load_skills_dir("/nonexistent/path")
        assert skills == []

    def test_load_empty_dir(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        skills = load_skills_dir(empty)
        assert skills == []

    def test_sorted_loading(self, tmp_path):
        """Skills load in alphabetical order."""
        (tmp_path / "z_skill.md").write_text("---\nname: z\n---\nZ")
        (tmp_path / "a_skill.md").write_text("---\nname: a\n---\nA")
        (tmp_path / "m_skill.md").write_text("---\nname: m\n---\nM")
        skills = load_skills_dir(tmp_path)
        assert [s.name for s in skills] == ["a", "m", "z"]


class TestLoadAllSkills:

    def test_multi_dir_merge(self, tmp_path):
        d1 = tmp_path / "dir1"
        d1.mkdir()
        (d1 / "shared.md").write_text("---\nname: shared\ndescription: from d1\n---\nD1")
        (d1 / "only_d1.md").write_text("---\nname: only_d1\n---\nD1 only")

        d2 = tmp_path / "dir2"
        d2.mkdir()
        (d2 / "shared.md").write_text("---\nname: shared\ndescription: from d2\n---\nD2")
        (d2 / "only_d2.md").write_text("---\nname: only_d2\n---\nD2 only")

        skills = load_all_skills([str(d1), str(d2)])
        assert len(skills) == 3
        assert skills["shared"].description == "from d2"  # Later overrides
        assert "only_d1" in skills
        assert "only_d2" in skills

    def test_empty_dirs_list(self):
        skills = load_all_skills([])
        assert skills == {}

    def test_all_nonexistent_dirs(self):
        skills = load_all_skills(["/no/such/dir1", "/no/such/dir2"])
        assert skills == {}


# ===================================================================
# discover_skill_dirs_for_paths
# ===================================================================

class TestDiscoverSkillDirs:

    def test_discover_skills_dir(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        skills = project / "src" / "skills"
        skills.mkdir(parents=True)
        (skills / "test.md").touch()

        found = discover_skill_dirs_for_paths(
            [str(project / "src" / "main.py")],
            str(project),
        )
        assert any("skills" in str(p) for p in found)

    def test_discover_claude_skills_dir(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        claude_skills = project / ".claude" / "skills"
        claude_skills.mkdir(parents=True)

        found = discover_skill_dirs_for_paths(
            [str(project / "app.py")],
            str(project),
        )
        assert any(".claude" in str(p) for p in found)

    def test_discover_dedup(self, tmp_path):
        """Same dir discovered from multiple files is not duplicated."""
        project = tmp_path / "project"
        project.mkdir()
        skills = project / "skills"
        skills.mkdir()

        found = discover_skill_dirs_for_paths(
            [str(project / "a.py"), str(project / "b.py")],
            str(project),
        )
        skill_paths = [str(p) for p in found]
        assert skill_paths.count(str(skills)) == 1

    def test_discover_nested(self, tmp_path):
        """Skills dirs at multiple levels are all discovered."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "skills").mkdir()
        (project / "src" / "skills").mkdir(parents=True)

        found = discover_skill_dirs_for_paths(
            [str(project / "src" / "app.py")],
            str(project),
        )
        found_strs = [str(p) for p in found]
        assert str(project / "src" / "skills") in found_strs
        assert str(project / "skills") in found_strs

    def test_discover_empty_paths(self, tmp_path):
        found = discover_skill_dirs_for_paths([], str(tmp_path))
        assert found == []

    def test_discover_no_skills_dirs(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        found = discover_skill_dirs_for_paths(
            [str(project / "app.py")],
            str(project),
        )
        assert found == []


# ===================================================================
# activate_conditional_skills
# ===================================================================

class TestActivateConditionalSkills:

    def test_single_pattern_match(self):
        skills = {
            "py": SkillDefinition(name="py", description="", content="", paths=["*.py"]),
        }
        activated = activate_conditional_skills(skills, ["src/main.py"])
        assert len(activated) == 1
        assert activated[0].name == "py"

    def test_no_match(self):
        skills = {
            "py": SkillDefinition(name="py", description="", content="", paths=["*.py"]),
        }
        activated = activate_conditional_skills(skills, ["app.js"])
        assert activated == []

    def test_globstar_pattern(self):
        skills = {
            "ts": SkillDefinition(name="ts", description="", content="", paths=["**/*.ts"]),
        }
        activated = activate_conditional_skills(skills, ["src/components/App.ts"])
        assert len(activated) == 1

    def test_multiple_patterns_or(self):
        """Skill with multiple patterns: any match activates."""
        skills = {
            "web": SkillDefinition(name="web", description="", content="",
                                   paths=["*.tsx", "*.css", "*.html"]),
        }
        activated = activate_conditional_skills(skills, ["style.css"])
        assert len(activated) == 1

    def test_multiple_files(self):
        """Multiple files can activate different skills."""
        skills = {
            "py": SkillDefinition(name="py", description="", content="", paths=["*.py"]),
            "ts": SkillDefinition(name="ts", description="", content="", paths=["*.ts"]),
        }
        activated = activate_conditional_skills(skills, ["app.py", "app.ts"])
        names = {s.name for s in activated}
        assert names == {"py", "ts"}

    def test_skill_without_paths_never_activates(self):
        skills = {
            "general": SkillDefinition(name="general", description="", content=""),
        }
        activated = activate_conditional_skills(skills, ["anything.py"])
        assert activated == []

    def test_empty_file_list(self):
        skills = {
            "py": SkillDefinition(name="py", description="", content="", paths=["*.py"]),
        }
        activated = activate_conditional_skills(skills, [])
        assert activated == []

    def test_skill_activated_only_once(self):
        """A skill matching multiple files is only activated once."""
        skills = {
            "py": SkillDefinition(name="py", description="", content="", paths=["*.py"]),
        }
        activated = activate_conditional_skills(skills, ["a.py", "b.py", "c.py"])
        assert len(activated) == 1


# ===================================================================
# apply_token_budget
# ===================================================================

class TestApplyTokenBudget:

    def test_within_budget(self):
        skills = {
            "a": SkillDefinition(name="a", description="Short desc", content=""),
            "b": SkillDefinition(name="b", description="Another desc", content=""),
        }
        entries = apply_token_budget(skills, max_chars=5000)
        assert len(entries) == 2

    def test_truncates_long_descriptions(self):
        skills = {
            "long": SkillDefinition(name="long", description="x" * 500, content=""),
        }
        entries = apply_token_budget(skills)
        assert len(entries) == 1
        name, desc = entries[0]
        assert len(desc) <= SKILL_DESCRIPTION_MAX_CHARS

    def test_budget_limit(self):
        """Skills exceeding budget are dropped."""
        skills = {}
        for i in range(100):
            skills[f"skill_{i}"] = SkillDefinition(
                name=f"skill_{i}",
                description=f"Description for skill {i} is moderately long",
                content="",
            )
        entries = apply_token_budget(skills, max_chars=200)
        total = sum(len(n) + len(d) + 10 for n, d in entries)
        assert total <= 200
        assert len(entries) < 100

    def test_empty_skills(self):
        entries = apply_token_budget({})
        assert entries == []

    def test_zero_budget(self):
        skills = {"a": SkillDefinition(name="a", description="desc", content="")}
        entries = apply_token_budget(skills, max_chars=0)
        assert entries == []


# ===================================================================
# apply_skill (inline mode)
# ===================================================================

class TestApplySkill:

    def test_inject_after_system(self):
        skill = SkillDefinition(name="test", description="", content="Skill instructions")
        msgs = [
            Message(role="system", content="System prompt"),
            Message(role="user", content="Question"),
        ]
        new_msgs, _ = apply_skill(skill, msgs, [])
        assert len(new_msgs) == 3
        assert new_msgs[0].content == "System prompt"
        assert "[Skill: test]" in new_msgs[1].content
        assert "Skill instructions" in new_msgs[1].content
        assert new_msgs[2].content == "Question"

    def test_inject_after_multiple_system(self):
        skill = SkillDefinition(name="test", description="", content="Instructions")
        msgs = [
            Message(role="system", content="System 1"),
            Message(role="system", content="System 2"),
            Message(role="user", content="User"),
        ]
        new_msgs, _ = apply_skill(skill, msgs, [])
        assert new_msgs[0].content == "System 1"
        assert new_msgs[1].content == "System 2"
        assert "[Skill: test]" in new_msgs[2].content
        assert new_msgs[3].content == "User"

    def test_inject_no_system_message(self):
        """Skill is inserted at index 0 when no system messages exist."""
        skill = SkillDefinition(name="test", description="", content="Instructions")
        msgs = [Message(role="user", content="Question")]
        new_msgs, _ = apply_skill(skill, msgs, [])
        assert "[Skill: test]" in new_msgs[0].content
        assert new_msgs[1].content == "Question"

    def test_tool_whitelist(self):
        skill = SkillDefinition(name="test", description="", content="",
                                allowed_tools=["add", "grep"])
        _, filtered = apply_skill(skill, [], [add_tool, grep_tool, bash_tool])
        names = {t.name for t in filtered}
        assert names == {"add", "grep"}

    def test_empty_whitelist_passes_all(self):
        skill = SkillDefinition(name="test", description="", content="",
                                allowed_tools=[])
        _, filtered = apply_skill(skill, [], [add_tool, grep_tool, bash_tool])
        assert len(filtered) == 3

    def test_whitelist_no_match(self):
        skill = SkillDefinition(name="test", description="", content="",
                                allowed_tools=["nonexistent"])
        _, filtered = apply_skill(skill, [], [add_tool])
        assert filtered == []

    def test_does_not_mutate_original(self):
        skill = SkillDefinition(name="test", description="", content="C")
        original = [Message(role="user", content="Q")]
        new_msgs, _ = apply_skill(skill, original, [])
        assert len(original) == 1  # Original not modified
        assert len(new_msgs) == 2

    def test_skill_metadata_in_message(self):
        skill = SkillDefinition(name="test", description="", content="C",
                                source_path="/skills/test.md")
        new_msgs, _ = apply_skill(skill, [Message(role="user", content="Q")], [])
        skill_msg = new_msgs[0]
        assert skill_msg.metadata["skill_name"] == "test"
        assert skill_msg.metadata["skill_source"] == "/skills/test.md"


# ===================================================================
# run_skill_fork (mocked)
# ===================================================================

class TestRunSkillFork:

    @pytest.mark.asyncio
    async def test_fork_basic(self):
        """Fork mode runs a sub-agent and returns its final text."""
        skill = SkillDefinition(
            name="forked", description="", content="Be a pirate",
            context="fork", model="test-model",
        )

        with patch("calcifer.agent.Agent") as MockAgent:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)

            from calcifer.agent import AgentResult
            mock_instance.run = AsyncMock(return_value=AgentResult(
                messages=[], final_text="Arrr matey!", usage=Usage(), turn_count=1,
            ))
            MockAgent.return_value = mock_instance

            result = await run_skill_fork(
                skill, "Say hello", [],
                api_key="test", base_url="http://test", model="gpt-4o",
            )
            assert result == "Arrr matey!"

            # Verify the sub-agent was created with skill system prompt
            call_args = MockAgent.call_args
            config = call_args[1]["config"] if "config" in call_args[1] else call_args[0][0]
            assert "pirate" in config.system_prompt.lower()

    @pytest.mark.asyncio
    async def test_fork_uses_skill_model(self):
        """Fork mode uses the skill's model if specified."""
        skill = SkillDefinition(
            name="forked", description="", content="content",
            context="fork", model="custom-model",
        )

        with patch("calcifer.agent.Agent") as MockAgent:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            from calcifer.agent import AgentResult
            mock_instance.run = AsyncMock(return_value=AgentResult(
                messages=[], final_text="ok", usage=Usage(), turn_count=1,
            ))
            MockAgent.return_value = mock_instance

            await run_skill_fork(skill, "test", [], api_key="k", base_url="http://b")
            config = MockAgent.call_args[1]["config"] if "config" in MockAgent.call_args[1] else MockAgent.call_args[0][0]
            assert config.model == "custom-model"

    @pytest.mark.asyncio
    async def test_fork_tool_whitelist(self):
        """Fork mode filters tools by skill's allowed-tools."""
        skill = SkillDefinition(
            name="forked", description="", content="",
            context="fork", allowed_tools=["add"],
        )

        with patch("calcifer.agent.Agent") as MockAgent:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            from calcifer.agent import AgentResult
            mock_instance.run = AsyncMock(return_value=AgentResult(
                messages=[], final_text="ok", usage=Usage(), turn_count=1,
            ))
            MockAgent.return_value = mock_instance

            await run_skill_fork(
                skill, "test", [add_tool, bash_tool],
                api_key="k", base_url="http://b",
            )
            tools_arg = MockAgent.call_args[1].get("tools", [])
            assert len(tools_arg) == 1
            assert tools_arg[0].name == "add"
