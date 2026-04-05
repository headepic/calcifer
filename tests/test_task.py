"""Tests for task system."""

import asyncio

import pytest

from calcifer.tasks import Task, TaskManager, TaskOutput, TaskStatus


@pytest.mark.asyncio
async def test_task_lifecycle():
    mgr = TaskManager()
    task = mgr.create_task("test-task")
    assert task.status == TaskStatus.PENDING

    async def worker(t: Task) -> str:
        return "done"

    await mgr.run_task(task.id, worker)
    result = await mgr.wait_for_task(task.id, timeout=5.0)
    assert result.status == TaskStatus.COMPLETED
    assert result.result == "done"


@pytest.mark.asyncio
async def test_task_failure():
    mgr = TaskManager()
    task = mgr.create_task("fail-task")

    async def worker(t: Task) -> str:
        raise ValueError("boom")

    await mgr.run_task(task.id, worker)
    result = await mgr.wait_for_task(task.id, timeout=5.0)
    assert result.status == TaskStatus.FAILED
    assert "boom" in (result.error or "")


@pytest.mark.asyncio
async def test_task_kill():
    mgr = TaskManager()
    task = mgr.create_task("long-task")

    async def worker(t: Task) -> str:
        await asyncio.sleep(100)
        return "never"

    await mgr.run_task(task.id, worker)
    await asyncio.sleep(0.05)  # Let it start

    killed = mgr.kill_task(task.id)
    assert killed is True

    result = await mgr.wait_for_task(task.id, timeout=5.0)
    assert result.status == TaskStatus.KILLED


def test_task_list():
    mgr = TaskManager()
    mgr.create_task("a")
    mgr.create_task("b")
    mgr.create_task("c")

    tasks = mgr.list_tasks()
    assert len(tasks) == 3


def test_task_output():
    output = TaskOutput("test_output_123")

    output.write("hello ")
    output.write("world\n")

    content = output.read_all()
    assert content == "hello world\n"

    # Incremental reading
    data, offset = output.read_delta(0)
    assert data == "hello world\n"
    assert offset == 12

    output.write("more data\n")
    data2, offset2 = output.read_delta(offset)
    assert data2 == "more data\n"
    assert offset2 > offset

    output.cleanup()
