"""Tests for memory system."""

import tempfile
from pathlib import Path

import pytest

from calcifer.memdir import (
    MemoryEntry,
    MemoryStore,
    find_relevant_memories,
    format_memories_for_prompt,
)


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


def test_save_and_load(store):
    entry = MemoryEntry(
        name="test-memory",
        description="A test memory",
        type="user",
        content="The user prefers Python over JavaScript.",
    )

    path = store.save(entry)
    assert Path(path).exists()

    loaded = store.load(path)
    assert loaded is not None
    assert loaded.name == "test-memory"
    assert loaded.type == "user"
    assert "Python" in loaded.content


def test_list_memories(store):
    for i in range(3):
        store.save(
            MemoryEntry(
                name=f"mem-{i}",
                description=f"Memory {i}",
                type="project",
                content=f"Content {i}",
            )
        )

    memories = store.list_memories()
    assert len(memories) == 3


def test_list_memories_filter_type(store):
    store.save(MemoryEntry(name="a", description="", type="user", content=""))
    store.save(MemoryEntry(name="b", description="", type="project", content=""))
    store.save(MemoryEntry(name="c", description="", type="user", content=""))

    users = store.list_memories(memory_type="user")
    assert len(users) == 2

    projects = store.list_memories(memory_type="project")
    assert len(projects) == 1


def test_delete(store):
    entry = MemoryEntry(name="del", description="", type="user", content="temp")
    path = store.save(entry)
    assert store.delete(path) is True
    assert store.load(path) is None


def test_search(store):
    store.save(
        MemoryEntry(
            name="py-pref",
            description="Language preference",
            type="feedback",
            content="User prefers Python for backend work.",
        )
    )
    store.save(
        MemoryEntry(
            name="js-note",
            description="Frontend note",
            type="project",
            content="The frontend uses React with TypeScript.",
        )
    )

    results = store.search("Python")
    assert len(results) == 1
    assert results[0].name == "py-pref"


def test_memory_index_created(store, tmp_path):
    store.save(MemoryEntry(name="idx", description="test", type="user", content="c"))

    index = tmp_path / "MEMORY.md"
    assert index.exists()
    content = index.read_text()
    assert "idx" in content


def test_find_relevant_memories(store):
    store.save(
        MemoryEntry(
            name="deploy",
            description="Deploy process",
            type="project",
            content="We deploy to AWS using Terraform.",
        )
    )
    store.save(
        MemoryEntry(
            name="testing",
            description="Testing preference",
            type="feedback",
            content="Always run pytest before committing.",
        )
    )

    results = find_relevant_memories(store, "how do we deploy?")
    assert len(results) >= 1
    assert any("deploy" in r.name for r in results)


def test_format_memories_for_prompt():
    entries = [
        MemoryEntry(name="A", description="", type="user", content="Content A"),
        MemoryEntry(name="B", description="", type="project", content="Content B"),
    ]

    text = format_memories_for_prompt(entries)
    assert "Content A" in text
    assert "Content B" in text
    assert "[Relevant memories]" in text
