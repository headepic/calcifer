"""Tool orchestration: parallel/serial dispatch + streaming tool executor.

Mirrors Claude Code's services/tools/:
- partitionToolCalls: consecutive concurrency-safe tools → parallel batch
- runTools: execute batches with asyncio.gather + semaphore
- StreamingToolExecutor: start tool execution as soon as tool_use block completes
- Full execution pipeline: schema validate → check_input → call
- Error classification for telemetry
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ...tool import Tool, find_tool_by_name
from ...types.message import Message, ToolCall
from ...types.tools import (
    ToolContext,
    ToolProgress,
    ToolResult,
    ValidationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENCY = 10


# -- Tool execution pipeline --

async def execute_tool_call(
    tc: ToolCall,
    tools_by_name: dict[str, Tool],
    context: ToolContext,
    on_progress: Callable[[ToolProgress], None] | None = None,
) -> Message:
    """Execute a single tool call through the full pipeline.

    Pipeline (mirrors Claude Code's toolExecution.ts):
    1. Find tool by name
    2. Parse JSON arguments
    3. Validate input (schema + tool-specific)
    4. Check permissions
    5. Execute tool.call()
    6. Truncate result if needed
    """
    start_time = time.monotonic()

    # 0. Security classification (log-only, does not block)
    try:
        from ...utils.classifier import classify_tool_call, SecurityLevel
        raw_args = {}
        try:
            raw_args = json.loads(tc.arguments)
        except (json.JSONDecodeError, TypeError):
            pass
        classification = classify_tool_call(tc.function_name, raw_args, tools_by_name)
        if classification.level == SecurityLevel.DANGEROUS:
            logger.warning(
                "SECURITY: dangerous tool call %s: %s",
                tc.function_name, classification.reason,
            )
        elif classification.level == SecurityLevel.SUSPICIOUS:
            logger.info(
                "SECURITY: suspicious tool call %s: %s",
                tc.function_name, classification.reason,
            )
    except Exception:
        pass  # Classification is best-effort, never blocks execution

    # 1. Find tool
    tool = tools_by_name.get(tc.function_name)
    if tool is None:
        return Message(
            role="tool",
            content=f"Error: No such tool available: {tc.function_name}",
            tool_call_id=tc.id,
        )

    # 2. Parse arguments
    try:
        raw_args = json.loads(tc.arguments)
    except json.JSONDecodeError as e:
        return Message(
            role="tool",
            content=f"Error parsing arguments: {e}",
            tool_call_id=tc.id,
        )

    # 3. Validate input (schema)
    try:
        validated = tool.validate_input(raw_args)
    except Exception as e:
        return Message(
            role="tool",
            content=f"Invalid arguments: {e}",
            tool_call_id=tc.id,
        )

    # 3b. Tool-specific validation
    validation = await tool.check_input(raw_args, context)
    if not validation.valid:
        return Message(
            role="tool",
            content=f"Validation error: {validation.message}",
            tool_call_id=tc.id,
        )

    progress_callback = None
    if on_progress:
        def progress_callback(progress: ToolProgress) -> None:
            if not progress.tool_use_id:
                progress.tool_use_id = tc.id
            on_progress(progress)

    # 4. Execute
    try:
        result = await tool.call(validated, context, on_progress=progress_callback)
    except asyncio.CancelledError:
        return Message(
            role="tool",
            content="Tool execution was cancelled.",
            tool_call_id=tc.id,
        )
    except Exception as e:
        error_class = classify_tool_error(e)
        logger.exception("Tool %s raised %s", tool.name, error_class)
        result = ToolResult(content=f"Tool execution error: {e}", is_error=True)

    # 5. Apply context modifier if provided
    if result.context_modifier:
        try:
            modified = result.context_modifier(context)
            if modified is not None:
                # Update context in place (fields only, not the object reference)
                context.read_file_state.update(modified.read_file_state)
                context.metadata.update(modified.metadata)
        except Exception as e:
            logger.warning("Context modifier for %s failed: %s", tool.name, e)

    # 6. Truncate via tool's own limit
    content = tool.truncate_result(result.content)

    # 7. Persist oversized results to disk
    from ...utils.persist import persist_if_needed
    content, persisted_path = persist_if_needed(content, tool.name, tc.id)

    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    logger.debug("Tool %s completed in %dms", tool.name, elapsed_ms)

    metadata: dict[str, Any] = dict(result.metadata)
    metadata["is_error"] = result.is_error
    if persisted_path:
        metadata["persisted_path"] = persisted_path

    return Message(
        role="tool",
        content=content,
        tool_call_id=tc.id,
        tool_use_result=content[:200] if len(content) > 200 else content,
        metadata=metadata,
    )


def classify_tool_error(error: Exception) -> str:
    """Classify a tool execution error for telemetry."""
    if isinstance(error, asyncio.TimeoutError):
        return "timeout"
    if isinstance(error, PermissionError):
        return "permission_denied"
    if isinstance(error, FileNotFoundError):
        return "file_not_found"
    if isinstance(error, OSError):
        code = getattr(error, "errno", None)
        if code:
            return f"os_error_{code}"
        return "os_error"
    return type(error).__name__


# -- Batch partitioning --

@dataclass
class Batch:
    """A batch of tool calls to execute together."""

    is_concurrent: bool
    tool_calls: list[ToolCall]


def partition_tool_calls(
    tool_calls: list[ToolCall],
    tools_by_name: dict[str, Tool],
) -> list[Batch]:
    """Partition tool calls into batches.

    Same logic as Claude Code's partitionToolCalls() in toolOrchestration.ts:
    - Consecutive concurrency-safe tools form one parallel batch
    - Non-safe tools each form their own serial batch
    """
    batches: list[Batch] = []

    for tc in tool_calls:
        tool = tools_by_name.get(tc.function_name)
        is_safe = False
        if tool:
            try:
                is_safe = tool.is_concurrency_safe
            except Exception:
                is_safe = False

        if is_safe and batches and batches[-1].is_concurrent:
            batches[-1].tool_calls.append(tc)
        else:
            batches.append(Batch(is_concurrent=is_safe, tool_calls=[tc]))

    return batches


# -- Run tools with orchestration --

async def run_tools(
    tool_calls: list[ToolCall],
    tools_by_name: dict[str, Tool],
    context: ToolContext,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    on_progress: Callable[[ToolProgress], None] | None = None,
) -> list[Message]:
    """Execute tool calls with partitioned orchestration.

    Fast path: single tool call skips batch partitioning entirely.
    """
    # Fast path: single tool call (most common case, ~70% of turns)
    if len(tool_calls) == 1:
        result = await execute_tool_call(tool_calls[0], tools_by_name, context, on_progress)
        return [result]

    batches = partition_tool_calls(tool_calls, tools_by_name)
    results: list[Message] = []

    for batch in batches:
        if batch.is_concurrent:
            sem = asyncio.Semaphore(max_concurrency)

            async def _run(tc: ToolCall) -> Message:
                async with sem:
                    return await execute_tool_call(tc, tools_by_name, context, on_progress)

            batch_results = await asyncio.gather(
                *[_run(tc) for tc in batch.tool_calls]
            )
            results.extend(batch_results)
        else:
            for tc in batch.tool_calls:
                if context.abort_signal:
                    tool = tools_by_name.get(tc.function_name)
                    blocks = tool and tool.interrupt_behavior() == "block"
                    if not blocks:
                        results.append(Message(
                            role="tool",
                            content="Cancelled: agent was interrupted",
                            tool_call_id=tc.id,
                        ))
                        continue
                result = await execute_tool_call(tc, tools_by_name, context, on_progress)
                results.append(result)

    return results


# -- Streaming Tool Executor --

class StreamingToolExecutor:
    """Execute tools as they stream in, with concurrency control.

    Mirrors Claude Code's StreamingToolExecutor:
    - Concurrent-safe tools can execute in parallel
    - Non-concurrent tools must execute alone (exclusive access)
    - Tools start executing as soon as their tool_use block is complete
    - Results are buffered and emitted in order
    """

    @dataclass
    class _TrackedTool:
        id: str
        tool_call: ToolCall
        is_concurrent: bool
        status: str = "queued"  # queued | executing | completed
        result: Message | None = None
        task: asyncio.Task[None] | None = None

    def __init__(
        self,
        tools_by_name: dict[str, Tool],
        context: ToolContext,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        on_progress: Callable[[ToolProgress], None] | None = None,
    ):
        self._tools_by_name = tools_by_name
        self._context = context
        self._max_concurrency = max_concurrency
        self._on_progress = on_progress
        self._tracked: list[StreamingToolExecutor._TrackedTool] = []
        self._has_errored = False

    def add_tool(self, tool_call: ToolCall) -> None:
        """Add a tool to execute. Starts immediately if conditions allow."""
        tool = self._tools_by_name.get(tool_call.function_name)
        is_safe = False
        if tool:
            try:
                is_safe = tool.is_concurrency_safe
            except Exception:
                is_safe = False

        tracked = self._TrackedTool(
            id=tool_call.id,
            tool_call=tool_call,
            is_concurrent=is_safe,
        )
        self._tracked.append(tracked)
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon(lambda: asyncio.ensure_future(self._process_queue()))
        except RuntimeError:
            pass  # No running loop; get_results will process queue

    def _can_execute(self, is_concurrent: bool) -> bool:
        executing = [t for t in self._tracked if t.status == "executing"]
        return (
            len(executing) == 0
            or (is_concurrent and all(t.is_concurrent for t in executing))
        )

    async def _process_queue(self) -> None:
        for tracked in self._tracked:
            if tracked.status != "queued":
                continue
            if self._can_execute(tracked.is_concurrent):
                await self._execute(tracked)
            elif not tracked.is_concurrent:
                break

    async def _execute(self, tracked: _TrackedTool) -> None:
        tracked.status = "executing"

        async def _run() -> None:
            tool = self._tools_by_name.get(tracked.tool_call.function_name)
            blocks = tool and tool.interrupt_behavior() == "block"
            if self._has_errored and not blocks:
                tracked.result = Message(
                    role="tool",
                    content="Cancelled: parallel tool call errored",
                    tool_call_id=tracked.id,
                )
            else:
                tracked.result = await execute_tool_call(
                    tracked.tool_call, self._tools_by_name,
                    self._context, self._on_progress,
                )
                if tracked.result.content and tracked.result.content.startswith("Error"):
                    self._has_errored = True
            tracked.status = "completed"
            await self._process_queue()

        tracked.task = asyncio.create_task(_run())

    async def get_results(self) -> list[Message]:
        """Wait for all tools to complete and return results in order."""
        # Ensure any queued tools are started before we wait
        await self._process_queue()
        tasks = [t.task for t in self._tracked if t.task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return [t.result for t in self._tracked if t.result]
