"""Memory store: CRUD for Markdown memory files with YAML frontmatter.

Follows Claude Code's memory system:
- Each memory is a separate .md file with YAML frontmatter
- MEMORY.md is the index file
- Types: user, feedback, project, reference
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

MEMORY_INDEX_FILE = "MEMORY.md"
MEMORY_INDEX_MAX_LINES = 200

MEMORY_TYPES = {"user", "feedback", "project", "reference"}


@dataclass
class MemoryEntry:
    """A single memory entry."""

    name: str
    description: str
    type: str  # user, feedback, project, reference
    content: str
    file_path: str = ""
    modified_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryStore:
    """File-backed memory store."""

    def __init__(self, directory: str | Path):
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, entry: MemoryEntry) -> str:
        """Save a memory entry to disk. Returns the file path."""
        # Generate filename from name
        safe_name = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in entry.name.lower()
        )
        filename = f"{entry.type}_{safe_name}.md"
        path = self._dir / filename

        # Build file content
        frontmatter = {
            "name": entry.name,
            "description": entry.description,
            "type": entry.type,
        }
        if entry.metadata:
            frontmatter.update(entry.metadata)

        content = f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\n{entry.content}\n"
        path.write_text(content)

        entry.file_path = str(path)
        entry.modified_at = time.time()

        # Update index
        self._update_index()

        return str(path)

    def load(self, file_path: str | Path) -> MemoryEntry | None:
        """Load a memory entry from a file."""
        path = Path(file_path)
        if not path.exists():
            return None

        text = path.read_text()
        if not text.startswith("---"):
            return None

        parts = text.split("---", 2)
        if len(parts) < 3:
            return None

        try:
            frontmatter = yaml.safe_load(parts[1])
            if not isinstance(frontmatter, dict):
                return None
        except yaml.YAMLError:
            return None

        body = parts[2].strip()
        known_keys = {"name", "description", "type"}

        return MemoryEntry(
            name=frontmatter.get("name", path.stem),
            description=frontmatter.get("description", ""),
            type=frontmatter.get("type", "project"),
            content=body,
            file_path=str(path),
            modified_at=path.stat().st_mtime,
            metadata={k: v for k, v in frontmatter.items() if k not in known_keys},
        )

    def list_memories(self, memory_type: str | None = None) -> list[MemoryEntry]:
        """List all memories, optionally filtered by type."""
        entries: list[MemoryEntry] = []
        for path in sorted(self._dir.glob("*.md")):
            if path.name == MEMORY_INDEX_FILE:
                continue
            entry = self.load(path)
            if entry:
                if memory_type and entry.type != memory_type:
                    continue
                entries.append(entry)
        return sorted(entries, key=lambda e: e.modified_at, reverse=True)

    def delete(self, file_path: str | Path) -> bool:
        """Delete a memory entry."""
        path = Path(file_path)
        if path.exists():
            path.unlink()
            self._update_index()
            return True
        return False

    def search(self, query: str) -> list[MemoryEntry]:
        """Simple text search across all memories."""
        query_lower = query.lower()
        results: list[MemoryEntry] = []
        for entry in self.list_memories():
            if (
                query_lower in entry.name.lower()
                or query_lower in entry.description.lower()
                or query_lower in entry.content.lower()
            ):
                results.append(entry)
        return results

    def _update_index(self) -> None:
        """Rebuild the MEMORY.md index file."""
        entries = self.list_memories()
        lines = ["# Memory Index\n"]

        for entry in entries[:MEMORY_INDEX_MAX_LINES - 2]:
            rel_path = Path(entry.file_path).name
            desc = entry.description[:120] if entry.description else entry.name
            lines.append(f"- [{entry.name}]({rel_path}) — {desc}")

        index_path = self._dir / MEMORY_INDEX_FILE
        index_path.write_text("\n".join(lines) + "\n")
