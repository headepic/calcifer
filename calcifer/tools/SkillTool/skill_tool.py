"""SkillTool: on-demand skill loading for the agent.

Mirrors Claude Code's SkillTool:
- LLM sees skill names/descriptions in system prompt (lightweight)
- LLM calls skill(name="xxx", arguments="...") to load full content
- Content is returned as tool result → LLM follows instructions next turn
- Invoked skills are tracked for post-compact re-injection
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable

from pydantic import BaseModel, Field

from ...tool import Tool
from ...types.tools import ToolContext, ToolResult

logger = logging.getLogger(__name__)


class SkillInput(BaseModel):
    name: str = Field(description="Name of the skill to load")
    arguments: str = Field(default="", description="Arguments to pass to the skill")


# Module-level state: tracks invoked skills for post-compact re-injection
_invoked_skills: dict[str, InvokedSkillInfo] = {}


class InvokedSkillInfo:
    __slots__ = ("skill_name", "content", "invoked_at", "agent_id")

    def __init__(self, skill_name: str, content: str, agent_id: str | None = None):
        self.skill_name = skill_name
        self.content = content
        self.invoked_at = time.time()
        self.agent_id = agent_id


def add_invoked_skill(name: str, content: str, agent_id: str | None = None) -> None:
    key = f"{agent_id or ''}:{name}"
    _invoked_skills[key] = InvokedSkillInfo(name, content, agent_id)


def get_invoked_skills(agent_id: str | None = None) -> list[InvokedSkillInfo]:
    """Get invoked skills, optionally filtered by agent_id."""
    results = list(_invoked_skills.values())
    if agent_id is not None:
        results = [s for s in results if s.agent_id == agent_id]
    return sorted(results, key=lambda s: s.invoked_at, reverse=True)


def clear_invoked_skills() -> None:
    _invoked_skills.clear()


class SkillTool(Tool):
    """Load a skill's full content on demand.

    The LLM sees only skill names and short descriptions in the system prompt.
    When it needs a skill's instructions, it calls this tool to get the full content.
    """

    name = "skill"
    description = (
        "Load a skill by name. Returns the skill's full instructions. "
        "Use this when you need detailed guidance for a specific task. "
        "Available skills are listed in the system prompt."
    )
    parameters = SkillInput
    is_concurrency_safe = True
    is_read_only = True
    always_load = True  # Never deferred — must always be available

    def __init__(self, skills: dict[str, Any] | None = None):
        """Initialize with a skill registry.

        Args:
            skills: dict mapping skill name → SkillDefinition
        """
        self._skills: dict[str, Any] = skills or {}

    def set_skills(self, skills: dict[str, Any]) -> None:
        """Update the skill registry (e.g., after hot-reload)."""
        self._skills = skills

    async def call(
        self,
        args: BaseModel,
        context: ToolContext,
        on_progress: Callable | None = None,
    ) -> ToolResult:
        assert isinstance(args, SkillInput)
        name = args.name.strip().lower()
        arguments = args.arguments.strip()

        # Find skill
        skill = self._skills.get(name)
        if not skill:
            # Try fuzzy match
            for sname, sdef in self._skills.items():
                if sname.lower() == name or name in sname.lower():
                    skill = sdef
                    name = sname
                    break

        if not skill:
            available = ", ".join(sorted(self._skills.keys()))
            return ToolResult(
                content=f"Unknown skill: '{args.name}'. Available skills: {available}",
                is_error=True,
            )

        # Expand variables in skill content
        content = skill.content
        if arguments:
            content = _substitute_variables(content, arguments)

        # Track invocation for post-compact re-injection
        agent_id = context.chain_id
        add_invoked_skill(name, content, agent_id)
        logger.info("Skill '%s' loaded (%d chars)", name, len(content))

        # Build result with skill metadata
        parts = [f"# Skill: {skill.name}"]
        if skill.description:
            parts.append(f"_{skill.description}_")
        parts.append("")
        parts.append(content)

        if skill.allowed_tools:
            parts.append(f"\n**Allowed tools for this skill:** {', '.join(skill.allowed_tools)}")

        return ToolResult(content="\n".join(parts))


def _substitute_variables(content: str, arguments: str) -> str:
    """Substitute $ARGUMENTS and positional $1..$N in skill content."""
    # $ARGUMENTS → full argument string
    content = content.replace("$ARGUMENTS", arguments)

    # $1, $2, ..., $N → positional arguments
    parts = arguments.split()
    for i, part in enumerate(parts, 1):
        content = content.replace(f"${i}", part)

    # ${@:N} → all args from position N onward
    for match in re.finditer(r"\$\{@:(\d+)\}", content):
        n = int(match.group(1))
        rest = " ".join(parts[n - 1:]) if n <= len(parts) else ""
        content = content.replace(match.group(0), rest)

    return content
