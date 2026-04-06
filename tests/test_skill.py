"""Tests for skill system."""

import tempfile
from pathlib import Path

import pytest

from calcifer import Message, tool
from calcifer.skills import (
    SkillDefinition, apply_skill, apply_token_budget,
    load_skill_file, load_skills_dir,
)


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


# -- when_to_use frontmatter field tests (feature: when-to-use-skill-field) --


def test_skill_when_to_use_parsing_kebab_case(tmp_path):
    """load_skill_file parses `when-to-use` from frontmatter."""
    skill_file = tmp_path / "explain.md"
    skill_file.write_text(
        "---\n"
        "name: explain\n"
        "description: Explain a concept in plain English\n"
        "when-to-use: User asks 'what is X' or 'how does Y work'\n"
        "---\n\n"
        "Provide a clear explanation.\n"
    )

    skill = load_skill_file(skill_file)
    assert skill is not None
    assert skill.when_to_use == "User asks 'what is X' or 'how does Y work'"


def test_skill_when_to_use_parsing_snake_case(tmp_path):
    """load_skill_file also accepts `when_to_use` spelling (snake_case)."""
    skill_file = tmp_path / "summarize.md"
    skill_file.write_text(
        "---\n"
        "name: summarize\n"
        "description: Summarize a long document\n"
        "when_to_use: Document is longer than 500 words\n"
        "---\n\n"
        "Produce a summary.\n"
    )

    skill = load_skill_file(skill_file)
    assert skill is not None
    assert skill.when_to_use == "Document is longer than 500 words"


def test_skill_when_to_use_absent(tmp_path):
    """Skills without when-to-use load correctly with empty default."""
    skill_file = tmp_path / "plain.md"
    skill_file.write_text(
        "---\n"
        "name: plain\n"
        "description: Plain skill without when_to_use\n"
        "---\n\nBody.\n"
    )

    skill = load_skill_file(skill_file)
    assert skill is not None
    assert skill.when_to_use == ""


def test_skill_when_to_use_not_in_metadata(tmp_path):
    """when-to-use should be consumed by known_keys, not dumped into metadata."""
    skill_file = tmp_path / "k.md"
    skill_file.write_text(
        "---\n"
        "name: k\n"
        "description: desc\n"
        "when-to-use: trigger\n"
        "custom-extra: value\n"
        "---\nBody\n"
    )
    skill = load_skill_file(skill_file)
    assert skill is not None
    assert skill.when_to_use == "trigger"
    assert "when-to-use" not in skill.metadata
    assert "when_to_use" not in skill.metadata
    assert skill.metadata.get("custom-extra") == "value"


def test_skill_budget_includes_when_to_use():
    """apply_token_budget appends (use when: ...) to description when set."""
    skills = {
        "review": SkillDefinition(
            name="review",
            description="Review code changes",
            content="",
            when_to_use="User says 'review my diff' or 'check my changes'",
        ),
    }
    entries = apply_token_budget(skills, max_chars=10_000)
    assert len(entries) == 1
    name, desc = entries[0]
    assert name == "review"
    # Description must still be present
    assert "Review code changes" in desc
    # when_to_use should be appended in the (use when: ...) form
    assert "(use when:" in desc
    assert "review my diff" in desc


def test_skill_budget_without_when_to_use():
    """Skills without when_to_use do NOT get (use when: ...) in the entry."""
    skills = {
        "plain": SkillDefinition(
            name="plain",
            description="Plain skill",
            content="",
        ),
    }
    entries = apply_token_budget(skills, max_chars=10_000)
    assert len(entries) == 1
    _, desc = entries[0]
    assert desc == "Plain skill"
    assert "(use when:" not in desc


def test_skill_budget_counts_when_to_use_chars():
    """The char budget accounts for when_to_use length (no free ride)."""
    # Craft a tight budget: name(4) + desc(10) + when_to_use(30) + format overhead
    # should push past a very small budget if counted, fit otherwise.
    skills = {
        "tiny": SkillDefinition(
            name="tiny",
            description="short desc",
            content="",
            when_to_use="x" * 100,
        ),
    }
    # Budget small enough that description alone fits but description + when_to_use doesn't
    entries = apply_token_budget(skills, max_chars=30)
    assert len(entries) == 0, "budget accounting should include when_to_use length"
