"""Skill executor: inline and fork execution modes.

Mirrors Claude Code's SkillTool:
- Inline mode: inject skill prompt into system message, apply tool whitelist
- Fork mode: run skill in an isolated sub-agent with its own context
"""

from __future__ import annotations

from ..types.message import Message
from ..tool import Tool
from .loader import SkillDefinition


def apply_skill(
    skill: SkillDefinition,
    messages: list[Message],
    available_tools: list[Tool],
) -> tuple[list[Message], list[Tool]]:
    """Apply a skill to the conversation (inline mode).

    Returns updated (messages, tools):
    - Injects the skill content as a system message
    - Filters tools to the skill's allowed-tools whitelist (if specified)
    """
    skill_msg = Message(
        role="system",
        content=f"[Skill: {skill.name}]\n\n{skill.content}",
        metadata={"skill_name": skill.name, "skill_source": skill.source_path},
    )

    new_messages = list(messages)
    insert_idx = 0
    for i, msg in enumerate(new_messages):
        if msg.role == "system":
            insert_idx = i + 1
        else:
            break
    new_messages.insert(insert_idx, skill_msg)

    if skill.allowed_tools:
        allowed_set = set(skill.allowed_tools)
        filtered_tools = [t for t in available_tools if t.name in allowed_set]
    else:
        filtered_tools = list(available_tools)

    return new_messages, filtered_tools


async def run_skill_fork(
    skill: SkillDefinition,
    prompt: str,
    available_tools: list[Tool],
    *,
    api_key: str = "",
    base_url: str = "http://127.0.0.1:8317/v1",
    model: str | None = None,
) -> str:
    """Run a skill in fork mode — isolated sub-agent.

    Creates a new Agent with the skill's system prompt and tool whitelist,
    runs the prompt, returns the final text.
    """
    from ..agent import Agent
    from ..config import CalciferConfig

    if skill.allowed_tools:
        allowed_set = set(skill.allowed_tools)
        tools = [t for t in available_tools if t.name in allowed_set]
    else:
        tools = list(available_tools)

    config = CalciferConfig(
        api_key=api_key,
        base_url=base_url,
        model=model or skill.model or "gpt-4o",
        system_prompt=f"[Skill: {skill.name}]\n\n{skill.content}",
        max_turns=30,
    )

    async with Agent(config=config, tools=tools) as agent:
        result = await agent.run(prompt)
        return result.final_text
