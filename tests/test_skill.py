"""Tests for skill system."""

import tempfile
from pathlib import Path

import pytest

from calcifer import Message, tool
from calcifer.skills import SkillDefinition, apply_skill, load_skill_file, load_skills_dir


def test_load_skill_file(tmp_path):
    skill_file = tmp_path / "review.md"
    skill_file.write_text(
        "---\n"
        "name: code-review\n"
        "description: Review code for quality\n"
        "allowed-tools: [file_read, grep]\n"
        "---\n\n"
        "You are a code reviewer. Check for bugs and style issues.\n"
    )

    skill = load_skill_file(skill_file)
    assert skill is not None
    assert skill.name == "code-review"
    assert skill.description == "Review code for quality"
    assert skill.allowed_tools == ["file_read", "grep"]
    assert "code reviewer" in skill.content


def test_load_skill_no_frontmatter(tmp_path):
    skill_file = tmp_path / "plain.md"
    skill_file.write_text("Just some markdown content.\n")

    skill = load_skill_file(skill_file)
    assert skill is not None
    assert skill.name == "plain"
    assert skill.content.strip() == "Just some markdown content."


def test_load_skills_dir(tmp_path):
    (tmp_path / "a.md").write_text("---\nname: alpha\n---\nContent A")
    (tmp_path / "b.md").write_text("---\nname: beta\n---\nContent B")
    (tmp_path / "not_skill.txt").write_text("ignored")

    skills = load_skills_dir(tmp_path)
    assert len(skills) == 2
    names = {s.name for s in skills}
    assert names == {"alpha", "beta"}


def test_apply_skill():
    skill = SkillDefinition(
        name="test-skill",
        description="Test",
        content="Do the thing.",
        allowed_tools=["bash"],
    )

    @tool(name="bash", description="bash")
    def bash() -> str:
        return ""

    @tool(name="file_read", description="read")
    def file_read() -> str:
        return ""

    messages = [
        Message(role="system", content="System prompt"),
        Message(role="user", content="Hello"),
    ]

    new_msgs, new_tools = apply_skill(skill, messages, [bash, file_read])

    # Skill should be injected after system message
    assert new_msgs[0].role == "system"
    assert new_msgs[0].content == "System prompt"
    assert "test-skill" in (new_msgs[1].content or "")
    assert new_msgs[2].role == "user"

    # Tool whitelist should filter
    assert len(new_tools) == 1
    assert new_tools[0].name == "bash"
