"""Tests for tool orchestration (parallel/serial dispatch)."""

import asyncio
import time

import pytest

from calcifer import ToolCall, tool
from calcifer.services.tools.orchestrator import partition_tool_calls, run_tools
from calcifer.types.tools import ToolContext


def test_partition_concurrent_tools():
    @tool(name="read1", description="r1", is_concurrency_safe=True)
    def r1() -> str:
        return ""

    @tool(name="read2", description="r2", is_concurrency_safe=True)
    def r2() -> str:
        return ""

    tools_by_name = {r1.name: r1, r2.name: r2}
    calls = [
        ToolCall(id="1", function_name="read1", arguments="{}"),
        ToolCall(id="2", function_name="read2", arguments="{}"),
    ]

    batches = partition_tool_calls(calls, tools_by_name)
    assert len(batches) == 1
    assert batches[0].is_concurrent is True
    assert len(batches[0].tool_calls) == 2


def test_partition_serial_tool_breaks_batch():
    @tool(name="read", description="r", is_concurrency_safe=True)
    def read() -> str:
        return ""

    @tool(name="write", description="w", is_concurrency_safe=False)
    def write() -> str:
        return ""

    tools_by_name = {read.name: read, write.name: write}
    calls = [
        ToolCall(id="1", function_name="read", arguments="{}"),
        ToolCall(id="2", function_name="write", arguments="{}"),
        ToolCall(id="3", function_name="read", arguments="{}"),
    ]

    batches = partition_tool_calls(calls, tools_by_name)
    assert len(batches) == 3
    assert batches[0].is_concurrent is True
    assert batches[1].is_concurrent is False
    assert batches[2].is_concurrent is True


@pytest.mark.asyncio
async def test_run_tools_parallel_actually_parallel():
    """Concurrent-safe tools should run in parallel (faster than serial)."""

    @tool(name="slow", description="slow", is_concurrency_safe=True)
    async def slow() -> str:
        await asyncio.sleep(0.1)
        return "done"

    tools_by_name = {slow.name: slow}
    calls = [
        ToolCall(id=str(i), function_name="slow", arguments="{}")
        for i in range(5)
    ]

    start = time.monotonic()
    results = await run_tools(calls, tools_by_name, ToolContext())
    elapsed = time.monotonic() - start

    assert len(results) == 5
    assert all(m.content == "done" for m in results)
    # 5 tasks at 0.1s each: parallel should be ~0.1s, serial would be ~0.5s
    assert elapsed < 0.3


@pytest.mark.asyncio
async def test_run_tools_unknown_tool():
    results = await run_tools(
        [ToolCall(id="1", function_name="missing", arguments="{}")],
        {},
        ToolContext(),
    )
    assert len(results) == 1
    assert "No such tool available" in results[0].content
