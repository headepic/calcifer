"""Session persistence: save/load conversation transcripts to disk.

Mirrors Claude Code's sessionStorage.ts — enables --resume and crash recovery.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..types.message import Message, ToolCall, Usage

logger = logging.getLogger(__name__)

DEFAULT_SESSION_DIR = Path.home() / ".calcifer" / "sessions"


@dataclass
class SessionRecord:
    """A persisted session."""

    session_id: str
    created_at: float
    updated_at: float
    messages: list[dict[str, Any]]
    usage: dict[str, int]
    turn_count: int
    model: str = ""
    cwd: str = ""
    # Session metadata
    git_branch: str = ""
    agent_name: str = ""
    tags: list[str] = field(default_factory=list)
    parent_session_id: str = ""  # For forked sessions


class SessionStorage:
    """File-backed session persistence."""

    def __init__(self, session_dir: str | Path | None = None):
        self._dir = Path(session_dir or DEFAULT_SESSION_DIR)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._session_id: str = uuid4().hex
        self._path = self._dir / f"{self._session_id}.json"

    @property
    def session_id(self) -> str:
        return self._session_id

    def _message_to_dict(self, msg: Message) -> dict[str, Any]:
        d: dict[str, Any] = {"role": msg.role, "uuid": msg.uuid}
        if msg.content is not None:
            d["content"] = msg.content
        if msg.tool_calls:
            d["tool_calls"] = [
                {"id": tc.id, "function_name": tc.function_name, "arguments": tc.arguments}
                for tc in msg.tool_calls
            ]
        if msg.tool_call_id:
            d["tool_call_id"] = msg.tool_call_id
        if msg.is_meta:
            d["is_meta"] = True
        return d

    def _dict_to_message(self, d: dict[str, Any]) -> Message:
        tool_calls = [
            ToolCall(id=tc["id"], function_name=tc["function_name"], arguments=tc["arguments"])
            for tc in d.get("tool_calls", [])
        ]
        return Message(
            role=d["role"],
            content=d.get("content"),
            tool_calls=tool_calls,
            tool_call_id=d.get("tool_call_id"),
            uuid=d.get("uuid", uuid4().hex),
            is_meta=d.get("is_meta", False),
        )

    def save(
        self,
        messages: list[Message],
        usage: Usage,
        turn_count: int,
        model: str = "",
        cwd: str = "",
    ) -> Path:
        """Save current session state to disk."""
        record = SessionRecord(
            session_id=self._session_id,
            created_at=self._path.stat().st_ctime if self._path.exists() else time.time(),
            updated_at=time.time(),
            messages=[self._message_to_dict(m) for m in messages],
            usage={
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            },
            turn_count=turn_count,
            model=model,
            cwd=cwd,
        )

        self._path.write_text(json.dumps(asdict(record), indent=2))
        return self._path

    def load(self, session_id: str | None = None) -> tuple[list[Message], Usage, int] | None:
        """Load a session from disk. Returns (messages, usage, turn_count) or None."""
        path = self._dir / f"{session_id or self._session_id}.json"
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load session %s: %s", path, e)
            return None

        messages = [self._dict_to_message(d) for d in data.get("messages", [])]
        raw_usage = data.get("usage", {})
        usage = Usage(
            prompt_tokens=raw_usage.get("prompt_tokens", 0),
            completion_tokens=raw_usage.get("completion_tokens", 0),
            total_tokens=raw_usage.get("total_tokens", 0),
        )
        turn_count = data.get("turn_count", 0)
        return messages, usage, turn_count

    def get_last_session_id(self) -> str | None:
        """Get the most recently updated session ID."""
        sessions = sorted(
            self._dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if sessions:
            return sessions[0].stem
        return None

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """List recent sessions."""
        sessions = sorted(
            self._dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]

        results: list[dict[str, Any]] = []
        for path in sessions:
            try:
                data = json.loads(path.read_text())
                results.append({
                    "session_id": data.get("session_id", path.stem),
                    "updated_at": data.get("updated_at", 0),
                    "turn_count": data.get("turn_count", 0),
                    "model": data.get("model", ""),
                })
            except Exception:
                continue
        return results

    def fork(self, from_message_index: int | None = None) -> SessionStorage:
        """Fork a new session from the current one.

        Creates a new session with a copy of messages up to from_message_index,
        with fresh UUIDs. Links back to parent via parent_session_id.
        """
        result = self.load()
        if result is None:
            raise ValueError("Cannot fork: no current session")

        messages, usage, turn_count = result

        # Truncate if index specified
        if from_message_index is not None:
            messages = messages[:from_message_index]

        # Create forked session
        forked = SessionStorage(self._dir)
        forked.save(
            messages, usage, turn_count,
            model="", cwd="",
        )

        # Update the record with parent link
        path = forked._path
        if path.exists():
            data = json.loads(path.read_text())
            data["parent_session_id"] = self._session_id
            path.write_text(json.dumps(data, indent=2))

        return forked
