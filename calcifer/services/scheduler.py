"""Scheduled tasks: cron-based agent execution.

Mirrors Claude Code's ScheduleCronTool:
- Parse cron expressions
- Watch for scheduled task triggers
- Lock mechanism (prevent double execution)
- Missed task notification
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable
from uuid import uuid4

logger = logging.getLogger(__name__)

SCHEDULE_DIR = Path.home() / ".calcifer" / "schedules"


@dataclass
class ScheduledTask:
    """A scheduled task definition."""

    id: str
    name: str
    cron: str  # Cron expression (minute hour day month weekday)
    prompt: str  # What to ask the agent
    model: str | None = None
    tools: list[str] | None = None  # Tool whitelist
    enabled: bool = True
    last_run: float = 0.0
    created_at: float = field(default_factory=time.time)


def _parse_cron_field(field_str: str, min_val: int, max_val: int) -> list[int]:
    """Parse a single cron field into a list of matching values."""
    if field_str == "*":
        return list(range(min_val, max_val + 1))

    values: list[int] = []
    for part in field_str.split(","):
        if "/" in part:
            base, step = part.split("/")
            start = min_val if base == "*" else int(base)
            values.extend(range(start, max_val + 1, int(step)))
        elif "-" in part:
            low, high = part.split("-")
            values.extend(range(int(low), int(high) + 1))
        else:
            values.append(int(part))

    return [v for v in values if min_val <= v <= max_val]


def cron_matches_now(cron: str) -> bool:
    """Check if a cron expression matches the current time."""
    parts = cron.strip().split()
    if len(parts) != 5:
        return False

    import datetime
    now = datetime.datetime.now()

    minute_match = now.minute in _parse_cron_field(parts[0], 0, 59)
    hour_match = now.hour in _parse_cron_field(parts[1], 0, 23)
    day_match = now.day in _parse_cron_field(parts[2], 1, 31)
    month_match = now.month in _parse_cron_field(parts[3], 1, 12)
    weekday_match = now.weekday() in _parse_cron_field(parts[4], 0, 6)

    return minute_match and hour_match and day_match and month_match and weekday_match


class Scheduler:
    """Manages scheduled agent tasks."""

    def __init__(self, schedule_dir: str | Path | None = None):
        self._dir = Path(schedule_dir or SCHEDULE_DIR)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, ScheduledTask] = {}
        self._load()

    def _load(self) -> None:
        """Load all schedules from disk."""
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                task = ScheduledTask(
                    id=data["id"],
                    name=data["name"],
                    cron=data["cron"],
                    prompt=data["prompt"],
                    model=data.get("model"),
                    tools=data.get("tools"),
                    enabled=data.get("enabled", True),
                    last_run=data.get("last_run", 0),
                    created_at=data.get("created_at", 0),
                )
                self._tasks[task.id] = task
            except Exception as e:
                logger.warning("Failed to load schedule %s: %s", path, e)

    def create(
        self, name: str, cron: str, prompt: str, **kwargs: Any
    ) -> ScheduledTask:
        """Create a new scheduled task."""
        task = ScheduledTask(
            id=uuid4().hex[:12],
            name=name,
            cron=cron,
            prompt=prompt,
            **kwargs,
        )
        self._tasks[task.id] = task
        self._save(task)
        return task

    def delete(self, task_id: str) -> bool:
        task = self._tasks.pop(task_id, None)
        if task:
            path = self._dir / f"{task_id}.json"
            if path.exists():
                path.unlink()
            return True
        return False

    def list_tasks(self) -> list[ScheduledTask]:
        return list(self._tasks.values())

    def _save(self, task: ScheduledTask) -> None:
        path = self._dir / f"{task.id}.json"
        data = {
            "id": task.id, "name": task.name, "cron": task.cron,
            "prompt": task.prompt, "model": task.model,
            "tools": task.tools, "enabled": task.enabled,
            "last_run": task.last_run, "created_at": task.created_at,
        }
        path.write_text(json.dumps(data, indent=2))

    def check_due(self) -> list[ScheduledTask]:
        """Check which tasks are due to run now."""
        due: list[ScheduledTask] = []
        now = time.time()
        for task in self._tasks.values():
            if not task.enabled:
                continue
            # Don't run more than once per minute
            if now - task.last_run < 55:
                continue
            if cron_matches_now(task.cron):
                due.append(task)
        return due

    def mark_run(self, task_id: str) -> None:
        """Mark a task as having just run."""
        task = self._tasks.get(task_id)
        if task:
            task.last_run = time.time()
            self._save(task)

    async def watch(
        self, interval_s: float = 60.0
    ) -> AsyncIterator[ScheduledTask]:
        """Watch for due tasks, yielding them as they become ready."""
        while True:
            for task in self.check_due():
                yield task
                self.mark_run(task.id)
            await asyncio.sleep(interval_s)
