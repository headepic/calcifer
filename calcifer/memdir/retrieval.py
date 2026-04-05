"""Memory retrieval: find relevant memories for the current context.

Simplified version of Claude Code's findRelevantMemories —
uses keyword matching instead of LLM-based selection.
"""

from __future__ import annotations

from .store import MemoryEntry, MemoryStore


def find_relevant_memories(
    store: MemoryStore,
    query: str,
    max_results: int = 5,
) -> list[MemoryEntry]:
    """Find memories relevant to a query.

    Uses simple keyword scoring. For LLM-based retrieval,
    the caller can use the store.list_memories() and send them
    to the LLM for selection.
    """
    all_memories = store.list_memories()
    if not all_memories:
        return []

    query_words = set(query.lower().split())

    scored: list[tuple[float, MemoryEntry]] = []
    for entry in all_memories:
        text = f"{entry.name} {entry.description} {entry.content}".lower()
        text_words = set(text.split())
        overlap = len(query_words & text_words)
        if overlap > 0:
            score = overlap / len(query_words)
            scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [entry for _, entry in scored[:max_results]]


def format_memories_for_prompt(memories: list[MemoryEntry]) -> str:
    """Format memories as context for the system prompt."""
    if not memories:
        return ""

    parts = ["[Relevant memories]"]
    for entry in memories:
        parts.append(f"\n### {entry.name} ({entry.type})")
        parts.append(entry.content)

    return "\n".join(parts)
