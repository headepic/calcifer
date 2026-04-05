"""Main TUI application: Interactive chat + Print mode.

Interactive mode: Rich Live display + Prompt Toolkit input
Print mode: Streaming output to stdout (text/json/stream-json)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.text import Text

from ..agent import Agent, AgentResult
from ..config import CalciferConfig
from ..tool import Tool
from ..types.message import Message, StreamEvent, Usage
from .renderer import (
    render_assistant_text,
    render_compact_notification,
    render_spinner,
    render_status_bar,
    render_system_message,
    render_tool_call_start,
    render_tool_result,
    render_user_message,
    render_welcome,
)
from .theme import CALCIFER_THEME


# ===================================================================
# Interactive TUI
# ===================================================================

async def run_tui(
    config: CalciferConfig,
    tools: list[Tool] | None = None,
    *,
    initial_prompt: str | None = None,
) -> None:
    """Run the interactive TUI chat loop.

    Features:
    - Rich Live rendering for streaming output
    - Prompt Toolkit for input (history, multiline with Shift+Enter)
    - Tool call visualization with animated spinners
    - Ctrl+C interrupts current request (not the whole program)
    - Status bar with model/tokens/cost
    - /help, /compact, /cost, /clear, /exit commands
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.patch_stdout import patch_stdout

    console = Console(theme=CALCIFER_THEME)
    history = InMemoryHistory()

    # Key bindings: Shift+Enter inserts newline, Enter submits
    bindings = KeyBindings()

    @bindings.add(Keys.Enter)
    def _submit(event):
        """Enter submits the input."""
        event.current_buffer.validate_and_handle()

    @bindings.add(Keys.Escape, Keys.Enter)  # Alt+Enter as fallback
    def _newline_alt(event):
        """Alt+Enter inserts a newline."""
        event.current_buffer.insert_text("\n")

    session = PromptSession(history=history, key_bindings=bindings, multiline=True)
    conversation: list[Message] = []

    async with Agent(config=config, tools=tools or []) as agent:
        # Connect MCP if configured
        if config.mcp_servers:
            console.print(render_system_message("Connecting MCP servers..."))
            await agent.connect_mcp_servers()

        # Load skills if configured
        if config.skills_dirs:
            agent.load_skills()

        # Enable session persistence
        agent.enable_session_persistence()

        # Welcome
        console.print(render_welcome(config.model))
        console.print()

        # Handle initial prompt (from CLI flag)
        if initial_prompt:
            await _run_one_turn(agent, initial_prompt, conversation, console)

        # Main REPL loop
        while True:
            try:
                with patch_stdout():
                    user_input = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: session.prompt(
                            [("class:user", f" {_prompt_glyph()} ")],
                        ),
                    )
            except EOFError:
                console.print(render_system_message("Goodbye!"))
                break
            except KeyboardInterrupt:
                # Ctrl+C at the prompt → just clear the input, don't exit
                console.print()
                continue

            user_input = user_input.strip()
            if not user_input:
                continue

            # Slash commands
            if user_input.startswith("/"):
                handled = await _handle_command(user_input, agent, conversation, console)
                if handled == "exit":
                    break
                if handled:
                    continue

            await _run_one_turn(agent, user_input, conversation, console)


async def _run_one_turn(
    agent: Agent,
    prompt: str,
    conversation: list[Message],
    console: Console,
) -> None:
    """Execute one user turn with streaming output.

    - Animated spinner during tool execution
    - Ctrl+C aborts the current request (agent.abort())
    """
    console.print(render_user_message(prompt))

    text_buffer = ""
    current_tool: str | None = None
    turn_start = time.monotonic()
    aborted = False

    # Ctrl+C during execution → abort the agent, don't kill the program
    loop = asyncio.get_event_loop()
    original_handler = None

    def _abort_handler():
        nonlocal aborted
        aborted = True
        agent.abort()

    try:
        original_handler = loop.remove_signal_handler
    except NotImplementedError:
        original_handler = None

    try:
        loop.add_signal_handler(2, _abort_handler)  # SIGINT
    except (NotImplementedError, OSError):
        pass  # Windows or non-main thread

    try:
        await _stream_turn(agent, prompt, conversation, console, text_buffer, turn_start)
    except asyncio.CancelledError:
        pass
    finally:
        # Restore default SIGINT handler
        try:
            loop.remove_signal_handler(2)
        except (NotImplementedError, OSError):
            pass

    if aborted:
        console.print(render_system_message("Request interrupted."))

    # Status bar
    if agent.cost_tracker:
        usage = agent.cost_tracker.get_total_usage()
        cost = agent.cost_tracker.get_cost()
        console.print(render_status_bar(
            model=agent._config.model,
            usage=usage,
            cost=cost,
            turn_count=len([m for m in conversation if m.role == "assistant"]),
            cwd=os.getcwd(),
        ))
    console.print()


async def _stream_turn(
    agent: Agent,
    prompt: str,
    conversation: list[Message],
    console: Console,
    text_buffer: str,
    turn_start: float,
) -> None:
    """Inner streaming loop — separated so Ctrl+C abort is clean."""
    current_tool: str | None = None
    spinner_task: asyncio.Task | None = None

    with Live(console=console, refresh_per_second=15, transient=False) as live:
        async for event in agent.run_stream(prompt, messages=conversation or None):
            if event.type == "text_delta" and event.text:
                # Cancel spinner if it was showing
                if spinner_task and not spinner_task.done():
                    spinner_task.cancel()
                    spinner_task = None
                text_buffer += event.text
                live.update(render_assistant_text(text_buffer, streaming=True))

            elif event.type == "turn_start":
                pass

            elif event.type == "tool_call_start":
                # Flush accumulated text as final markdown
                if text_buffer.strip():
                    live.update(render_assistant_text(text_buffer, streaming=False))
                    text_buffer = ""
                current_tool = event.tool_call_name
                console.print(render_tool_call_start(
                    event.tool_call_name or "unknown",
                    event.tool_call_arguments or "{}",
                ))
                # Start spinner for tool execution
                spinner_task = asyncio.create_task(
                    _run_spinner(live, turn_start, current_tool)
                )

            elif event.type == "tool_call_result":
                # Cancel spinner
                if spinner_task and not spinner_task.done():
                    spinner_task.cancel()
                    spinner_task = None
                    live.update(Text(""))  # Clear spinner
                content = event.tool_result_content or ""
                console.print(render_tool_result(content, is_error=event.tool_is_error))
                current_tool = None

            elif event.type == "turn_end":
                pass

            elif event.type == "run_complete":
                result = event.result
                if result:
                    conversation.clear()
                    conversation.extend(result.messages)

            elif event.type == "error":
                console.print(render_system_message(f"Error: {event.error}"))

        # Cancel any lingering spinner
        if spinner_task and not spinner_task.done():
            spinner_task.cancel()

        # Before Live exits, replace streaming text with final Markdown render
        if text_buffer.strip():
            live.update(render_assistant_text(text_buffer, streaming=False))


async def _run_spinner(live: Live, start_time: float, tool_name: str | None) -> None:
    """Animate a spinner in the Live display while a tool runs."""
    try:
        while True:
            elapsed = time.monotonic() - start_time
            live.update(render_spinner(elapsed, tool_name))
            await asyncio.sleep(0.12)
    except asyncio.CancelledError:
        pass


async def _handle_command(
    command: str,
    agent: Agent,
    conversation: list[Message],
    console: Console,
) -> str | bool:
    """Handle slash commands. Returns 'exit', True (handled), or False (not a command)."""
    cmd = command.lower().strip()

    if cmd in ("/exit", "/quit", "/q"):
        console.print(render_system_message("Goodbye!"))
        return "exit"

    if cmd == "/help":
        console.print(render_system_message(
            "Commands:\n"
            "  /help     — Show this help\n"
            "  /clear    — Clear conversation\n"
            "  /cost     — Show token usage & cost\n"
            "  /model    — Show current model\n"
            "  /compact  — Compress context\n"
            "  /exit     — Quit (or Ctrl+D)\n"
            "\n"
            "Tips:\n"
            "  Alt+Enter — Insert newline (multiline input)\n"
            "  Ctrl+C    — Interrupt current request"
        ))
        return True

    if cmd == "/clear":
        conversation.clear()
        console.clear()
        console.print(render_welcome(agent._config.model))
        console.print(render_system_message("Conversation cleared."))
        return True

    if cmd == "/cost":
        cost = agent.cost_tracker.get_cost()
        summary = agent.cost_tracker.summary()
        console.print(render_system_message(f"Total cost: ${cost:.6f}"))
        for model, info in summary.items():
            console.print(render_system_message(
                f"  {model}: {info['input_tokens']}↓ {info['output_tokens']}↑ "
                f"({info['api_calls']} calls) ${info['cost_usd']:.4f}"
            ))
        return True

    if cmd == "/model":
        console.print(render_system_message(f"Current model: {agent._config.model}"))
        return True

    if cmd == "/compact":
        console.print(render_system_message("Compacting conversation..."))
        if conversation:
            conversation[:] = await agent._maybe_compact(conversation)
            console.print(render_system_message("Conversation compacted."))
        return True

    # Unknown command — let agent handle it
    return False


def _prompt_glyph() -> str:
    return "❯"


# ===================================================================
# Print Mode (Non-Interactive)
# ===================================================================

async def run_print_mode(
    config: CalciferConfig,
    prompt: str,
    tools: list[Tool] | None = None,
    *,
    output_format: str = "text",  # text | json | stream-json
) -> None:
    """Run a single prompt in non-interactive print mode.

    Output formats:
    - text: Human-readable streaming text to stdout
    - json: Single JSON result object at the end
    - stream-json: Newline-delimited JSON events (for piping)
    """
    async with Agent(config=config, tools=tools or []) as agent:
        if config.mcp_servers:
            await agent.connect_mcp_servers()
        if config.skills_dirs:
            agent.load_skills()

        if output_format == "text":
            await _print_text(agent, prompt)
        elif output_format == "stream-json":
            await _print_stream_json(agent, prompt)
        elif output_format == "json":
            await _print_json(agent, prompt)
        else:
            raise ValueError(f"Unknown output format: {output_format}")


async def _print_text(agent: Agent, prompt: str) -> None:
    """Stream text to stdout."""
    async for event in agent.run_stream(prompt):
        if event.type == "text_delta" and event.text:
            sys.stdout.write(event.text)
            sys.stdout.flush()
        elif event.type == "tool_call_start":
            sys.stderr.write(f"\n[tool: {event.tool_call_name}]\n")
            sys.stderr.flush()
        elif event.type == "tool_call_result":
            if event.tool_is_error:
                sys.stderr.write(f"[error: {event.tool_result_content}]\n")
                sys.stderr.flush()
        elif event.type == "error":
            sys.stderr.write(f"\n[error: {event.error}]\n")
            sys.stderr.flush()
    sys.stdout.write("\n")
    sys.stdout.flush()


async def _print_stream_json(agent: Agent, prompt: str) -> None:
    """Stream newline-delimited JSON events."""
    async for event in agent.run_stream(prompt):
        obj: dict[str, Any] = {"type": event.type}
        if event.type == "text_delta":
            obj["text"] = event.text
        elif event.type == "tool_call_start":
            obj["tool_name"] = event.tool_call_name
            obj["tool_input"] = event.tool_call_arguments
        elif event.type == "tool_call_result":
            obj["tool_call_id"] = event.tool_call_id
            obj["output"] = event.tool_result_content
            obj["is_error"] = event.tool_is_error
        elif event.type == "run_complete" and event.result:
            obj["final_text"] = event.result.final_text
            obj["turn_count"] = event.result.turn_count
            obj["tokens"] = event.result.usage.total_tokens
        elif event.type == "error":
            obj["error"] = event.error
        else:
            continue
        print(json.dumps(obj, ensure_ascii=False), flush=True)


async def _print_json(agent: Agent, prompt: str) -> None:
    """Run and output a single JSON result."""
    result = await agent.run(prompt)
    output = {
        "final_text": result.final_text,
        "turn_count": result.turn_count,
        "usage": {
            "prompt_tokens": result.usage.prompt_tokens,
            "completion_tokens": result.usage.completion_tokens,
            "total_tokens": result.usage.total_tokens,
        },
        "cost_usd": agent.cost_tracker.get_cost(),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
