"""Task manager: background task state machine.

State machine: pending → running → completed/failed/killed
Modeled on Claude Code's Task System.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable
from uuid import uuid4

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"


TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.KILLED}


@dataclass
class Task:
    """A managed background task."""

    id: str
    name: str
    status: TaskStatus = TaskStatus.PENDING
    result: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


class TaskManager:
    """Manages background tasks with state tracking."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._async_tasks: dict[str, asyncio.Task[None]] = {}

    def create_task(
        self,
        name: str,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        """Create a new task in pending state."""
        task_id = f"t_{uuid4().hex[:8]}"
        task = Task(id=task_id, name=name, metadata=metadata or {})
        self._tasks[task_id] = task
        return task

    async def run_task(
        self,
        task_id: str,
        coro: Callable[[Task], Awaitable[str]],
    ) -> None:
        """Run a task coroutine, managing state transitions."""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Unknown task: {task_id}")

        task.status = TaskStatus.RUNNING
        task.started_at = time.time()

        async def _run() -> None:
            try:
                result = await coro(task)
                if task.status == TaskStatus.RUNNING:
                    task.status = TaskStatus.COMPLETED
                    task.result = result
                    task.completed_at = time.time()
            except asyncio.CancelledError:
                task.status = TaskStatus.KILLED
                task.completed_at = time.time()
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error = str(e)
                task.completed_at = time.time()
                logger.exception("Task %s failed", task_id)

        async_task = asyncio.create_task(_run())
        self._async_tasks[task_id] = async_task

    def kill_task(self, task_id: str) -> bool:
        """Kill a running task."""
        task = self._tasks.get(task_id)
        if not task or task.status in TERMINAL_STATUSES:
            return False

        async_task = self._async_tasks.get(task_id)
        if async_task:
            async_task.cancel()

        task.status = TaskStatus.KILLED
        task.completed_at = time.time()
        task._cancel_event.set()
        return True

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def list_tasks(
        self, status: TaskStatus | None = None
    ) -> list[Task]:
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    async def wait_for_task(self, task_id: str, timeout: float | None = None) -> Task:
        """Wait for a task to reach a terminal state."""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Unknown task: {task_id}")

        async_task = self._async_tasks.get(task_id)
        if async_task:
            await asyncio.wait_for(async_task, timeout=timeout)

        return task
