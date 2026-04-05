"""Task output: persistent, incrementally-readable output storage.

Like Claude Code's DiskTaskOutput — writes output to files,
supports incremental reading via offset.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

TASK_OUTPUT_DIR = Path(tempfile.gettempdir()) / "calcifer-tasks"
MAX_OUTPUT_BYTES = 5 * 1024 * 1024 * 1024  # 5GB


class TaskOutput:
    """File-backed task output with incremental reading."""

    def __init__(self, task_id: str):
        self._task_id = task_id
        self._dir = TASK_OUTPUT_DIR / task_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / "output.txt"
        self._offset = 0

    def write(self, data: str) -> None:
        """Append data to output file."""
        current_size = self._file.stat().st_size if self._file.exists() else 0
        if current_size + len(data.encode()) > MAX_OUTPUT_BYTES:
            return  # Silently drop if exceeding limit

        with open(self._file, "a") as f:
            f.write(data)

    def read_all(self) -> str:
        """Read entire output."""
        if not self._file.exists():
            return ""
        return self._file.read_text()

    def read_delta(self, offset: int = 0) -> tuple[str, int]:
        """Read output from offset. Returns (content, new_offset)."""
        if not self._file.exists():
            return "", 0

        with open(self._file) as f:
            f.seek(offset)
            content = f.read()
            new_offset = f.tell()

        return content, new_offset

    def cleanup(self) -> None:
        """Remove output files."""
        if self._file.exists():
            self._file.unlink()
        if self._dir.exists():
            try:
                self._dir.rmdir()
            except OSError:
                pass
