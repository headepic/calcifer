"""Skill loader: scan directories for Markdown skill definitions.

Mirrors Claude Code's skills/ system:
- YAML frontmatter + markdown body
- Multiple source directories with priority (later overrides earlier)
- Dynamic discovery (triggered by file operations)
- Conditional activation (paths frontmatter)
- Token budget (1% of context)
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Token budget: skill descriptions use at most 1% of context (default ~8K chars)
SKILL_DESCRIPTION_MAX_CHARS = 250
SKILL_LIST_MAX_CHARS = 8_000


@dataclass
class SkillDefinition:
    """A loaded skill definition."""

    name: str
    description: str
    content: str  # The markdown body (system prompt)
    allowed_tools: list[str] = field(default_factory=list)
    model: str | None = None
    source_path: str = ""

    # Execution mode: "inline" (default) or "fork"
    context: str = "inline"
    # Agent type for fork mode
    agent: str = "general-purpose"
    # Effort level
    effort: str = ""
    # User-invocable via /name
    user_invocable: bool = False
    # Conditional activation: only active when matching file paths are touched
    paths: list[str] = field(default_factory=list)
    # Guidance for when the model should invoke this skill — surfaced in the
    # skill list alongside description. Accepted from frontmatter as either
    # `when-to-use` or `when_to_use`.
    when_to_use: str = ""
    # Extra frontmatter fields
    metadata: dict[str, Any] = field(default_factory=dict)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown text."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        frontmatter = yaml.safe_load(parts[1])
        if not isinstance(frontmatter, dict):
            return {}, text
    except yaml.YAMLError:
        return {}, text
    body = parts[2].strip()
    return frontmatter, body


def load_skill_file(path: Path) -> SkillDefinition | None:
    """Load a single skill from a markdown file."""
    try:
        text = path.read_text()
    except Exception as e:
        logger.warning("Failed to read skill file %s: %s", path, e)
        return None

    frontmatter, body = _parse_frontmatter(text)

    known_keys = {
        "name", "description", "allowed-tools", "model", "context",
        "agent", "effort", "user-invocable", "paths",
        "when-to-use", "when_to_use",
    }
    metadata = {k: v for k, v in frontmatter.items() if k not in known_keys}

    allowed_tools = frontmatter.get("allowed-tools", [])
    paths_raw = frontmatter.get("paths", [])
    when_to_use = frontmatter.get("when-to-use") or frontmatter.get("when_to_use") or ""

    return SkillDefinition(
        name=frontmatter.get("name", path.stem),
        description=frontmatter.get("description", ""),
        content=body,
        allowed_tools=allowed_tools if isinstance(allowed_tools, list) else [],
        model=frontmatter.get("model"),
        source_path=str(path),
        context=frontmatter.get("context", "inline"),
        agent=frontmatter.get("agent", "general-purpose"),
        effort=frontmatter.get("effort", ""),
        user_invocable=frontmatter.get("user-invocable", False),
        paths=paths_raw if isinstance(paths_raw, list) else [],
        when_to_use=when_to_use if isinstance(when_to_use, str) else "",
        metadata=metadata,
    )


def load_skills_dir(directory: str | Path) -> list[SkillDefinition]:
    """Load all skills from a directory (non-recursive)."""
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return []
    skills: list[SkillDefinition] = []
    for path in sorted(dir_path.glob("*.md")):
        skill = load_skill_file(path)
        if skill:
            skills.append(skill)
    logger.info("Loaded %d skills from %s", len(skills), directory)
    return skills


def load_all_skills(dirs: list[str | Path]) -> dict[str, SkillDefinition]:
    """Load skills from multiple directories. Later dirs override earlier."""
    skills: dict[str, SkillDefinition] = {}
    for d in dirs:
        for skill in load_skills_dir(d):
            skills[skill.name] = skill
    return skills


# -- Dynamic discovery --

def discover_skill_dirs_for_paths(
    file_paths: list[str],
    project_root: str,
) -> list[Path]:
    """Discover skill directories by traversing up from touched file paths.

    Mirrors Claude Code's discoverSkillDirsForPaths — when a file is read/edited,
    check parent directories for skills/ folders up to the project root.
    """
    discovered: list[Path] = []
    root = Path(project_root).resolve()
    seen: set[str] = set()

    for file_path in file_paths:
        current = Path(file_path).resolve().parent
        while current >= root:
            skills_dir = current / "skills"
            dir_key = str(skills_dir)
            if dir_key not in seen and skills_dir.is_dir():
                discovered.append(skills_dir)
                seen.add(dir_key)

            # Also check .claude/skills/
            claude_skills = current / ".claude" / "skills"
            cdir_key = str(claude_skills)
            if cdir_key not in seen and claude_skills.is_dir():
                discovered.append(claude_skills)
                seen.add(cdir_key)

            if current == root:
                break
            current = current.parent

    return discovered


def activate_conditional_skills(
    skills: dict[str, SkillDefinition],
    file_paths: list[str],
) -> list[SkillDefinition]:
    """Activate skills whose `paths` frontmatter matches touched file paths.

    Returns the list of newly activated skills.
    Uses PurePath.match() which supports ** globstar patterns.
    """
    from pathlib import PurePath

    activated: list[SkillDefinition] = []
    for skill in skills.values():
        if not skill.paths:
            continue
        for pattern in skill.paths:
            for fp in file_paths:
                if PurePath(fp).match(pattern):
                    activated.append(skill)
                    break
            if skill in activated:
                break
    return activated


def apply_token_budget(
    skills: dict[str, SkillDefinition],
    max_chars: int = SKILL_LIST_MAX_CHARS,
) -> list[tuple[str, str]]:
    """Generate a token-budgeted skill list for the system prompt.

    Returns list of (name, truncated_description) within budget.

    When a skill has `when_to_use` set, it is appended to the description
    in the form "`description`\\n(use when: `when_to_use`)" so the model
    gets explicit invocation guidance alongside the description. The
    when_to_use text shares the same per-entry char budget as description.
    """
    entries: list[tuple[str, str]] = []
    total_chars = 0

    for skill in skills.values():
        desc = skill.description[:SKILL_DESCRIPTION_MAX_CHARS]
        if skill.when_to_use:
            when = skill.when_to_use[:SKILL_DESCRIPTION_MAX_CHARS]
            desc = f"{desc}\n(use when: {when})"
        entry_chars = len(skill.name) + len(desc) + 10
        if total_chars + entry_chars > max_chars:
            break
        entries.append((skill.name, desc))
        total_chars += entry_chars

    return entries
