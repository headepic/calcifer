"""Tool result disk persistence: offload oversized results to disk.

When a tool result exceeds PERSIST_THRESHOLD, the full content is written
to a temp file and the in-context content is replaced with a truncated
head+tail summary plus the file path. This prevents context blowup from
large MCP results, bash outputs, or file reads.

Mirrors Claude Code's persistBinaryContent pattern.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Results larger than this get persisted to disk
PERSIST_THRESHOLD = 50_000  # chars

# How much head/tail to keep inline
INLINE_HEAD = 2_000
INLINE_TAIL = 1_000

# Directory for persisted results (lazily created)
_persist_dir: Path | None = None


def _get_persist_dir() -> Path:
    """Get or create the persistence directory."""
    global _persist_dir
    if _persist_dir is None:
        _persist_dir = Path(tempfile.mkdtemp(prefix="calcifer-results-"))
    _persist_dir.mkdir(parents=True, exist_ok=True)
    return _persist_dir


def persist_if_needed(
    content: str,
    tool_name: str,
    tool_call_id: str,
    threshold: int = PERSIST_THRESHOLD,
) -> tuple[str, str | None]:
    """Persist content to disk if it exceeds threshold.

    Returns (inline_content, persisted_path_or_None).
    If content is small enough, returns (content, None) unchanged.
    """
    if len(content) <= threshold:
        return content, None

    # Write full content to disk
    persist_dir = _get_persist_dir()
    safe_name = tool_name.replace("/", "_").replace("\\", "_")
    filename = f"{safe_name}_{tool_call_id[:12]}.txt"
    path = persist_dir / filename

    try:
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to persist tool result to %s: %s", path, e)
        # Fall back to just truncating
        return _truncate(content, len(content)), None

    # Build inline summary
    total = len(content)
    inline = (
        content[:INLINE_HEAD]
        + f"\n\n... [{total - INLINE_HEAD - INLINE_TAIL:,} chars persisted to {path}] ...\n\n"
        + content[-INLINE_TAIL:]
    )

    logger.debug(
        "Persisted %s result (%d chars) to %s, inline %d chars",
        tool_name, total, path, len(inline),
    )
    return inline, str(path)


def read_persisted(path: str) -> str | None:
    """Read a persisted result back from disk."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def cleanup() -> None:
    """Remove all persisted result files."""
    global _persist_dir
    if _persist_dir and _persist_dir.exists():
        import shutil
        try:
            shutil.rmtree(_persist_dir)
        except OSError:
            pass
        _persist_dir = None


def _truncate(content: str, total: int) -> str:
    """Simple head+tail truncation."""
    return (
        content[:INLINE_HEAD]
        + f"\n\n... [truncated {total - INLINE_HEAD - INLINE_TAIL:,} chars] ...\n\n"
        + content[-INLINE_TAIL:]
    )
