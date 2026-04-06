"""Agent: the core loop that drives LLM <-> tool interaction.

Mirrors Claude Code's QueryEngine.ts + query.ts while(true) loop with:
- Cascade error recovery (prompt_too_long → reactive compact → autocompact)
- max_output_tokens → 64K escalation (up to 3x) with "Resume" injection
- Model fallback on persistent overload (529)
- Token budget with diminishing threshold (stop if delta < 500 for 3+ turns)
- StreamingToolExecutor integration (start tools while model streams)
- Multi-layer context compaction before each API call
- Abort controller for graceful cancellation
- Session persistence (save/resume transcripts)
- Query chain tracking (chainId + depth)
- MCP server connection and tool discovery
- Skill loading and application
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable
from uuid import uuid4

from .config import CalciferConfig, MCPServerConfig
from .services.compact.context import ContextManager
from .types.message import APIErrorType, Message, StreamEvent, ToolCall, Usage
from .services.tools.orchestrator import StreamingToolExecutor, run_tools
from .services.api.provider import LLMProvider, LLMProviderError
from .tool import Tool
from .types.tools import ToolContext, ToolResult
from .utils.cost_tracker import CostTracker
from .telemetry.spans import (
    start_interaction_span, end_interaction_span,
    start_llm_span, end_llm_span,
)
from .telemetry.metrics import MetricsManager

logger = logging.getLogger(__name__)

# Stop hook type
StopHookFn = Callable[
    [list[Message], "ToolContext"],
    Awaitable[bool] | bool,
]

# Error recovery constants (from Claude Code)
MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3
ESCALATED_MAX_TOKENS = 64_000
DEFAULT_MAX_TOKENS = 8_192

# Token budget: stop if completion delta is below this for N turns
DIMINISHING_THRESHOLD = 500
DIMINISHING_TURNS = 3
COMPLETION_THRESHOLD = 0.9

# Canonical OpenAI public endpoint, used as the final fallback when neither
# an explicit base_url nor OPENAI_BASE_URL env var is set.
_OPENAI_FALLBACK_BASE_URL = "https://api.openai.com/v1"


def _resolve_base_url(explicit: str | None) -> str:
    """Resolve the LLM base URL using the standard SDK precedence chain.

    Order:
      1. Explicit value from constructor / config (if truthy)
      2. OPENAI_BASE_URL environment variable (if set and truthy)
      3. Canonical OpenAI public endpoint

    The returned value is always a non-empty string. Used by Agent.__init__
    to write a real URL into self._config.base_url before LLMProvider is
    constructed.
    """
    if explicit:
        return explicit
    return os.environ.get("OPENAI_BASE_URL") or _OPENAI_FALLBACK_BASE_URL


@dataclass
class AgentResult:
    """Result of an agent run."""

    messages: list[Message]
    final_text: str
    usage: Usage
    turn_count: int


class Agent:
    """The core agent loop.

    Usage:
        agent = Agent(config=CalciferConfig(...), tools=[my_tool])
        result = await agent.run("Hello")
    """

    def __init__(
        self,
        config: CalciferConfig | None = None,
        *,
        api_key: str = "",
        base_url: str | None = None,
        model: str = "gpt-4o",
        max_tokens: int = 8192,
        temperature: float = 0.0,
        max_turns: int = 100,
        system_prompt: str = "",
        tools: list[Tool] | None = None,
    ):
        if config is not None:
            self._config = config
        else:
            self._config = CalciferConfig(
                api_key=api_key,
                base_url=base_url,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                max_turns=max_turns,
                system_prompt=system_prompt,
            )

        # Resolve base_url via the env-fallback chain BEFORE constructing
        # LLMProvider. After this point, self._config.base_url is always a
        # real string (never None) — any later code reading the config sees
        # the resolved value.
        self._config.base_url = _resolve_base_url(self._config.base_url)

        self._tools: list[Tool] = list(tools or [])
        self._tools_by_name: dict[str, Tool] = {t.name: t for t in self._tools}
        # Merge thinking config into extra API params
        extra_params = dict(self._config.extra_api_params)
        if self._config.thinking_mode != "disabled":
            from .utils.thinking import ThinkingConfig, ThinkingMode
            thinking = ThinkingConfig(
                mode=ThinkingMode(self._config.thinking_mode),
                budget_tokens=self._config.thinking_budget_tokens,
            )
            extra_params.update(thinking.to_api_params())

        self._provider = LLMProvider(
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            model=self._config.model,
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
            extra_params=extra_params,
        )
        self._context_mgr = ContextManager(
            max_context_tokens=self._config.max_context_tokens,
            compact_threshold=self._config.compact_threshold,
        )
        self._mcp_clients: list[Any] = []
        self._skills: dict[str, Any] = {}

        # Abort controller
        self._abort_event = asyncio.Event()

        # Query guard: prevents concurrent runs on same agent
        from .utils.concurrency import QueryGuard
        self._query_guard = QueryGuard()

        # Session persistence
        self._session: Any = None  # Lazy init

        # Query chain tracking
        self._chain_id = uuid4().hex
        self._chain_depth = 0

        # Stop hooks: checked after each tool execution turn
        self._stop_hooks: list[StopHookFn] = []

        # Cost tracking
        self.cost_tracker = CostTracker()

        # Telemetry
        self._metrics = MetricsManager()

        # Profiling
        from .utils.profiling import QueryProfiler
        self._profiler = QueryProfiler()

        # Autocompact circuit breaker
        self._autocompact_failures: int = 0

        # In-progress tool IDs
        self._in_progress_tool_ids: set[str] = set()

    @property
    def session_id(self) -> str | None:
        return self._session.session_id if self._session else None

    def abort(self) -> None:
        """Signal the agent to stop after the current tool completes."""
        self._abort_event.set()

    def register_stop_hook(self, hook: StopHookFn) -> None:
        """Register a stop hook. Called after each tool turn.

        If any hook returns True, the agent loop stops.
        Use for: max cost budget, external stop signals, content filters.
        """
        self._stop_hooks.append(hook)

    @property
    def in_progress_tools(self) -> set[str]:
        """Currently executing tool IDs."""
        return set(self._in_progress_tool_ids)

    def enable_session_persistence(self, session_dir: str | None = None) -> None:
        """Enable saving conversation transcripts to disk."""
        from .services.session import SessionStorage
        self._session = SessionStorage(session_dir)

    async def resume_session(self, session_id: str | None = None) -> list[Message] | None:
        """Resume a previous session with conversation repair.

        Applies recovery passes to handle interrupted conversations:
        - Removes orphaned thinking-only and whitespace-only messages
        - Synthesizes missing tool results for unresolved tool_use blocks
        - Appends a resume message if the conversation was interrupted mid-turn
        """
        if not self._session:
            return None
        result = self._session.load(session_id or self._session.get_last_session_id())
        if not result:
            return None

        messages, usage, turn_count = result

        from .utils.recovery import detect_interruption, repair_conversation, build_resume_message
        messages = repair_conversation(messages)
        interruption = detect_interruption(messages)
        resume_msg = build_resume_message(interruption)
        if resume_msg:
            messages.append(resume_msg)

        return messages

    # -- Tool management --

    def add_tool(self, tool: Tool) -> None:
        self._tools.append(tool)
        self._tools_by_name[tool.name] = tool

    def add_tools(self, tools: list[Tool]) -> None:
        for t in tools:
            self.add_tool(t)

    # -- MCP integration --

    async def connect_mcp_servers(
        self, servers: list[MCPServerConfig] | None = None
    ) -> None:
        from .services.mcp.client import MCPClient
        from .services.mcp.tool_adapter import create_mcp_tools
        from .services.mcp.transport import SSETransport, StdioTransport

        server_configs = servers or self._config.mcp_servers
        for cfg in server_configs:
            try:
                if cfg.transport == "stdio" and cfg.command:
                    transport = StdioTransport(command=cfg.command, args=cfg.args, env=cfg.env)
                elif cfg.transport == "sse" and cfg.url:
                    transport = SSETransport(url=cfg.url)
                else:
                    logger.warning("Invalid MCP config for %s", cfg.name)
                    continue

                client = MCPClient(name=cfg.name, transport=transport)
                await client.connect()
                schemas = await client.discover_tools()
                mcp_tools = create_mcp_tools(schemas, client)
                self.add_tools(mcp_tools)
                self._mcp_clients.append(client)
                logger.info("MCP %s: connected, %d tools", cfg.name, len(mcp_tools))
            except Exception as e:
                logger.error("Failed to connect MCP %s: %s", cfg.name, e)

    # -- Skill support --

    def load_skills(self, dirs: list[str] | None = None) -> None:
        from .skills import load_all_skills
        skill_dirs = dirs or self._config.skills_dirs
        if not skill_dirs:
            return
        self._skills = load_all_skills(skill_dirs)
        logger.info("Loaded %d skills", len(self._skills))

    def apply_skill(
        self, skill_name: str, messages: list[Message]
    ) -> tuple[list[Message], list[Tool]]:
        from .skills import apply_skill
        skill = self._skills.get(skill_name)
        if not skill:
            raise ValueError(f"Unknown skill: {skill_name}")
        return apply_skill(skill, messages, self._tools)

    # -- Internal helpers --

    def _build_initial_messages(self, prompt: str) -> list[Message]:
        messages: list[Message] = []
        if self._config.system_prompt:
            messages.append(Message(role="system", content=self._config.system_prompt))
        messages.append(Message(role="user", content=prompt))
        return messages

    def _get_tool_schemas(self) -> list[dict[str, Any]] | None:
        if not self._tools:
            return None
        return [t.to_openai_schema() for t in self._tools]

    async def _execute_tools(
        self, tool_calls: list[ToolCall], context: ToolContext
    ) -> list[Message]:
        return await run_tools(
            tool_calls, self._tools_by_name, context,
            max_concurrency=self._config.max_tool_concurrency,
        )

    async def _maybe_compact(self, conversation: list[Message], context: ToolContext | None = None) -> list[Message]:
        """Apply multi-layer compaction pipeline."""
        # Sync file read state from tool context to context manager
        if context:
            for path, mtime in context.read_file_state.items():
                self._context_mgr.track_file_read(path, mtime)

        # Layer 1-3: non-LLM compaction
        conversation = self._context_mgr.apply_all_layers(conversation)

        # Layer 4: autocompact (LLM summarization) if still over threshold
        if not self._context_mgr.needs_compaction(conversation):
            return conversation

        # Circuit breaker: stop after consecutive failures
        if self._autocompact_failures >= 3:
            logger.warning("Autocompact circuit breaker open (%d consecutive failures)", self._autocompact_failures)
            return conversation

        logger.info("Context approaching limit, triggering autocompact...")
        compact_prompt = self._context_mgr.build_compact_prompt(conversation)
        try:
            summary_msg, _ = await self._provider.chat_completion(messages=compact_prompt)
            raw_summary = summary_msg.content or ""
            summary = self._context_mgr.extract_summary(raw_summary)
            self._autocompact_failures = 0  # Reset on success
            compacted = self._context_mgr.compact_messages(conversation, summary)

            # Post-compact restoration: re-inject skills + MCP instructions
            mcp_names = [t.name for t in self._tools if getattr(t, "is_mcp", False)]
            attachments = self._context_mgr.create_post_compact_attachments(
                agent_id=self._chain_id,
                mcp_tool_names=mcp_names if mcp_names else None,
            )
            compacted.extend(attachments)
            if attachments:
                logger.info("Post-compact: restored %d attachments", len(attachments))

            return compacted
        except Exception as e:
            self._autocompact_failures += 1
            logger.error("Autocompact failed (%d/%d): %s", self._autocompact_failures, 3, e)
            return conversation

    def _check_token_budget(
        self, turn_count: int, deltas: list[int]
    ) -> bool:
        """Check if we should stop based on diminishing output.

        Returns True if we should stop.
        Like Claude Code's COMPLETION_THRESHOLD + DIMINISHING_THRESHOLD.
        """
        if turn_count < DIMINISHING_TURNS:
            return False
        recent = deltas[-DIMINISHING_TURNS:]
        if all(d < DIMINISHING_THRESHOLD for d in recent):
            logger.info("Stopping: diminishing output (%s)", recent)
            return True
        return False

    # -- Main run method --

    async def run(
        self,
        prompt: str,
        *,
        messages: list[Message] | None = None,
    ) -> AgentResult:
        """Run the agent loop (non-streaming). Delegates to _run_loop().

        Uses the same unified loop as run_stream() but with non-streaming
        LLM calls. All control flow, error recovery, and state management
        is shared — only the LLM call path differs.
        """
        result: AgentResult | None = None
        async for event in self._run_loop(prompt, messages=messages, streaming=False):
            if event.type == "run_complete" and event.result:
                result = event.result
        if result is None:
            raise RuntimeError("Agent loop ended without producing a result")
        return result

    # -- Streaming run --

    async def run_stream(
        self,
        prompt: str,
        *,
        messages: list[Message] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Run the agent loop with streaming + full error recovery.

        Uses the same unified loop as run() but with streaming LLM calls.
        Yields StreamEvents in real-time for UI rendering.

        Lifecycle events: turn_start, turn_end, tool_call_start,
        tool_call_result, run_complete.
        """
        async for event in self._run_loop(prompt, messages=messages, streaming=True):
            yield event

    # -- Unified agent loop --

    async def _run_loop(
        self,
        prompt: str,
        *,
        messages: list[Message] | None = None,
        streaming: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        """Unified agent loop used by both run() and run_stream().

        When streaming=True, uses chat_completion_stream for real-time output.
        When streaming=False, uses chat_completion and synthesizes events.
        All other logic (error recovery, compaction, tools, hooks) is shared.
        """
        import json
        import time as _time

        self._abort_event.clear()
        self._chain_depth = 0
        generation = self._query_guard.begin()

        try:
            async for event in self._run_loop_inner(
                prompt, messages=messages, generation=generation,
                streaming=streaming,
            ):
                yield event
        finally:
            self._query_guard.end(generation)

    async def _run_loop_inner(
        self,
        prompt: str,
        *,
        messages: list[Message] | None = None,
        generation: int = 0,
        streaming: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        import json
        import time as _time

        self._query_guard.mark_running(generation)
        self._profiler.reset()
        self._profiler.checkpoint("run_start")

        # Start interaction span
        interaction_span = start_interaction_span(
            prompt[:200], session_id=self.session_id or "",
            chain_id=self._chain_id, model=self._config.model,
        )

        if messages is not None:
            conversation = list(messages)
            conversation.append(Message(role="user", content=prompt))
        else:
            conversation = self._build_initial_messages(prompt)

        tool_schemas = self._get_tool_schemas()
        total_usage = Usage()
        turn_count = 0
        context = ToolContext(
            messages=conversation,
            max_concurrency=self._config.max_tool_concurrency,
            chain_id=self._chain_id,
            chain_depth=0,
        )
        max_output_recovery_count = 0
        has_attempted_reactive_compact = False
        completion_deltas: list[int] = []
        current_max_tokens: int | None = None

        while turn_count < self._config.max_turns:
            turn_count += 1
            self._chain_depth += 1
            context.chain_depth = self._chain_depth
            self._profiler.checkpoint(f"turn_{turn_count}_start")

            # Check abort
            if self._abort_event.is_set():
                logger.info("Agent aborted by user")
                break

            yield StreamEvent(type="turn_start", turn=turn_count)

            # Multi-layer compaction
            conversation = await self._maybe_compact(conversation, context)
            context.messages = conversation

            # Set up streaming tool executor (used in streaming mode)
            streaming_executor = StreamingToolExecutor(
                self._tools_by_name, context, self._config.max_tool_concurrency,
            ) if streaming else None

            _llm_start = _time.monotonic()
            llm_span = start_llm_span(self._config.model, attempt=turn_count)
            assistant_msg: Message | None = None
            turn_usage: Usage | None = None
            llm_error: LLMProviderError | None = None

            if streaming:
                # -- Streaming LLM call --
                text_parts: list[str] = []
                tool_call_accum: dict[int, dict[str, str]] = {}
                turn_finish_reason: str | None = None

                try:
                    async for event in self._provider.chat_completion_stream(
                        messages=conversation, tools=tool_schemas,
                        max_tokens_override=current_max_tokens,
                    ):
                        if event.type == "text_delta":
                            text_parts.append(event.text or "")
                            yield event

                        elif event.type == "thinking_delta":
                            yield event

                        elif event.type == "tool_call_delta":
                            idx = event.tool_call_index or 0
                            if idx not in tool_call_accum:
                                tool_call_accum[idx] = {"id": "", "name": "", "arguments": ""}
                            acc = tool_call_accum[idx]
                            if event.tool_call_id:
                                acc["id"] = event.tool_call_id
                            if event.tool_call_name:
                                acc["name"] = event.tool_call_name
                            if event.tool_call_arguments:
                                acc["arguments"] += event.tool_call_arguments

                            # Check if this tool call is complete
                            if acc["id"] and acc["name"] and acc["arguments"] and not acc.get("_submitted"):
                                try:
                                    json.loads(acc["arguments"])
                                    tc = ToolCall(id=acc["id"], function_name=acc["name"], arguments=acc["arguments"])
                                    assert streaming_executor is not None
                                    streaming_executor.add_tool(tc)
                                    acc["_submitted"] = True  # type: ignore
                                except json.JSONDecodeError:
                                    pass

                        elif event.type == "usage" and event.usage:
                            turn_usage = event.usage
                            yield event

                        elif event.type == "finish":
                            turn_finish_reason = event.finish_reason
                            yield event

                        elif event.type == "error":
                            # Check if recoverable (prompt_too_long)
                            if event.error_code and event.error_code in (413, 400):
                                error_type = APIErrorType.PROMPT_TOO_LONG
                            else:
                                error_type = None

                            if error_type == APIErrorType.PROMPT_TOO_LONG:
                                if not has_attempted_reactive_compact:
                                    logger.warning("Prompt too long → reactive compact...")
                                    conversation = self._context_mgr.reactive_compact(conversation)
                                    has_attempted_reactive_compact = True
                                    yield StreamEvent(type="turn_end", turn=turn_count)
                                    continue  # retry the turn
                            yield event

                except LLMProviderError as e:
                    llm_error = e

                if not llm_error:
                    # Build assistant message from streamed parts
                    tool_calls = [
                        ToolCall(id=acc["id"], function_name=acc["name"], arguments=acc["arguments"])
                        for acc in (tool_call_accum[i] for i in sorted(tool_call_accum))
                        if acc.get("id")
                    ]
                    assistant_msg = Message(
                        role="assistant",
                        content="".join(text_parts) or None,
                        tool_calls=tool_calls,
                    )
                    if turn_finish_reason == "length":
                        assistant_msg.metadata["api_error"] = "max_output_tokens"

            else:
                # -- Non-streaming LLM call --
                try:
                    assistant_msg, turn_usage = await self._provider.chat_completion(
                        messages=conversation,
                        tools=tool_schemas,
                        max_tokens_override=current_max_tokens,
                    )
                except LLMProviderError as e:
                    llm_error = e

            # -- Error recovery (shared by both modes) --
            if llm_error:
                _llm_elapsed = (_time.monotonic() - _llm_start) * 1000
                end_llm_span(llm_span, success=False, error=str(llm_error))
                if llm_error.error_type == APIErrorType.PROMPT_TOO_LONG:
                    if not has_attempted_reactive_compact:
                        logger.warning("Prompt too long → reactive compact...")
                        conversation = self._context_mgr.reactive_compact(conversation)
                        has_attempted_reactive_compact = True
                        yield StreamEvent(type="turn_end", turn=turn_count)
                        continue
                    # Try autocompact
                    logger.warning("Still too long → autocompact...")
                    compact_prompt = self._context_mgr.build_compact_prompt(conversation)
                    try:
                        summary_msg, _ = await self._provider.chat_completion(messages=compact_prompt)
                        conversation = self._context_mgr.compact_messages(
                            conversation, summary_msg.content or ""
                        )
                        has_attempted_reactive_compact = False
                        yield StreamEvent(type="turn_end", turn=turn_count)
                        continue
                    except Exception:
                        yield StreamEvent(type="error", error=str(llm_error), error_code=llm_error.status_code)
                        break
                yield StreamEvent(type="error", error=str(llm_error), error_code=llm_error.status_code)
                break

            assert assistant_msg is not None

            # -- Record LLM telemetry (shared) --
            _llm_elapsed = (_time.monotonic() - _llm_start) * 1000
            if turn_usage:
                end_llm_span(
                    llm_span, input_tokens=turn_usage.prompt_tokens,
                    output_tokens=turn_usage.completion_tokens,
                    cache_read_tokens=turn_usage.cache_read_input_tokens,
                    success=True, has_tool_calls=bool(assistant_msg.tool_calls),
                )
                self._metrics.record_llm_request(
                    self._config.model, input_tokens=turn_usage.prompt_tokens,
                    output_tokens=turn_usage.completion_tokens,
                    cache_read_tokens=turn_usage.cache_read_input_tokens,
                    latency_ms=_llm_elapsed,
                )
                total_usage += turn_usage
                self._context_mgr.update_usage(turn_usage)
                self.cost_tracker.record(self._config.model, turn_usage)
            else:
                end_llm_span(llm_span, success=True)

            has_attempted_reactive_compact = False
            conversation.append(assistant_msg)

            # Session persistence
            if self._session:
                try:
                    self._session.save(
                        conversation, total_usage, turn_count,
                        model=self._config.model, cwd=".",
                    )
                except Exception as e:
                    logger.debug("Session save failed: %s", e)

            # Check max_output_tokens (finish_reason: "length")
            # Two-phase recovery (mirrors Claude Code):
            #   Phase 1: escalate token cap only (no resume message)
            #   Phase 2+: escalate cap + inject resume message
            if assistant_msg.metadata.get("api_error") == "max_output_tokens":
                max_output_recovery_count += 1
                if max_output_recovery_count <= MAX_OUTPUT_TOKENS_RECOVERY_LIMIT:
                    current_max_tokens = ESCALATED_MAX_TOKENS
                    if max_output_recovery_count == 1:
                        # Phase 1: just escalate cap, retry same request
                        logger.warning(
                            "max_output_tokens hit (1/%d), escalating to %d",
                            MAX_OUTPUT_TOKENS_RECOVERY_LIMIT, ESCALATED_MAX_TOKENS,
                        )
                    else:
                        # Phase 2+: inject resume message
                        logger.warning(
                            "max_output_tokens hit (%d/%d), injecting resume",
                            max_output_recovery_count, MAX_OUTPUT_TOKENS_RECOVERY_LIMIT,
                        )
                        conversation.append(
                            Message(role="user", content=(
                                "Output token limit hit. Resume directly — no apology, no recap of what you were doing. "
                                "Pick up mid-thought if that is where the cut happened. "
                                "Break remaining work into smaller pieces."
                            ), is_meta=True)
                        )
                    yield StreamEvent(type="turn_end", turn=turn_count)
                    continue

            # No tool calls → done
            tool_calls = assistant_msg.tool_calls
            if not tool_calls:
                yield StreamEvent(type="turn_end", turn=turn_count)
                break

            # Check abort before tools
            if self._abort_event.is_set():
                logger.info("Agent aborted before tool execution")
                yield StreamEvent(type="turn_end", turn=turn_count)
                break

            # Emit tool_call_start events
            for tc in tool_calls:
                yield StreamEvent(
                    type="tool_call_start",
                    turn=turn_count,
                    tool_call_id=tc.id,
                    tool_call_name=tc.function_name,
                    tool_call_arguments=tc.arguments,
                )

            # Execute tools
            self._in_progress_tool_ids = {tc.id for tc in tool_calls}
            if streaming_executor is not None:
                # Streaming mode: collect from executor + run any not started
                tool_results = await streaming_executor.get_results()
                submitted_ids = {t.id for t in streaming_executor._tracked}
                remaining = [tc for tc in tool_calls if tc.id not in submitted_ids]
                if remaining:
                    extra = await self._execute_tools(remaining, context)
                    tool_results.extend(extra)
            else:
                # Non-streaming mode: run tools via orchestrator
                context.abort_signal = self._abort_event.is_set()
                tool_results = await self._execute_tools(tool_calls, context)

            self._in_progress_tool_ids.clear()

            # Emit tool_call_result events
            for result_msg in tool_results:
                content = result_msg.content or ""
                yield StreamEvent(
                    type="tool_call_result",
                    tool_call_id=result_msg.tool_call_id,
                    tool_result_content=content[:5000],
                    tool_is_error=result_msg.metadata.get("is_error", False),
                )

            conversation.extend(tool_results)

            # Record tool metrics
            for tc in tool_calls:
                self._metrics.record_tool_call(tc.function_name, success=True)

            # Stop hooks
            should_stop = False
            for hook in self._stop_hooks:
                try:
                    result = hook(conversation, context)
                    if asyncio.iscoroutine(result):
                        result = await result
                    if result:
                        logger.info("Stop hook triggered, ending loop")
                        should_stop = True
                        break
                except Exception as e:
                    logger.warning("Stop hook failed: %s", e)
            if should_stop:
                yield StreamEvent(type="turn_end", turn=turn_count)
                break

            # Token budget check
            if turn_usage:
                completion_deltas.append(turn_usage.completion_tokens)
            if self._check_token_budget(turn_count, completion_deltas):
                yield StreamEvent(type="turn_end", turn=turn_count)
                break

            yield StreamEvent(type="turn_end", turn=turn_count)

        # Final session save
        if self._session:
            try:
                self._session.save(
                    conversation, total_usage, turn_count,
                    model=self._config.model, cwd=".",
                )
            except Exception:
                pass

        # Extract final text
        final_text = ""
        for msg in reversed(conversation):
            if msg.role == "assistant" and msg.content:
                final_text = msg.content
                break

        # End interaction span + record metrics
        end_interaction_span(
            turn_count=turn_count, total_tokens=total_usage.total_tokens,
            cost_usd=self.cost_tracker.get_cost(), success=True,
        )
        self._metrics.record_agent_run(
            turns=turn_count, cost_usd=self.cost_tracker.get_cost(),
        )

        self._profiler.checkpoint("run_end")
        logger.debug("Profiling: %s (total %.0fms)", self._profiler.summary(), self._profiler.total_ms())

        # Yield final aggregated result
        yield StreamEvent(
            type="run_complete",
            result=AgentResult(
                messages=conversation,
                final_text=final_text,
                usage=total_usage,
                turn_count=turn_count,
            ),
        )

    async def close(self) -> None:
        for client in self._mcp_clients:
            try:
                await client.close()
            except Exception:
                pass
        await self._provider.close()
        # Force query guard idle on close
        self._query_guard.force_idle()

    async def __aenter__(self) -> Agent:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
