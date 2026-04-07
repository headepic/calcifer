"""Minimal terminal chat UI built on the Calcifer agent SDK.

A single-file TUI that demonstrates how to consume calcifer from an
external project:

    from calcifer import Agent, CalciferConfig
    from calcifer.tool_registry import get_all_builtin_tools
    from calcifer.types.message import Message

The UI is deliberately small: one prompt loop, streaming assistant text,
tool call indicators, a footer with token/cost info, and a handful of
slash commands. Everything else (markdown rendering, multi-pane layout,
session persistence, etc.) is left out — extend as needed.

Run:
    export OPENAI_API_KEY=sk-...
    export OPENAI_BASE_URL=https://api.openai.com/v1   # optional
    export OPENAI_MODEL=gpt-4o-mini                    # optional
    python -m calcifer_tui
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys

from calcifer import Agent, CalciferConfig
from calcifer.tool_registry import get_all_builtin_tools
from calcifer.types.message import Message
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console


BANNER = (
    "[bold orange1]Calcifer TUI[/bold orange1] "
    "[dim]— minimal terminal agent chat[/dim]\n"
    "[dim]/help 查看命令 · Ctrl+D 退出 · Ctrl+C 中断当前回合[/dim]"
)


HELP = """[bold]命令[/bold]
  [cyan]/help[/cyan]    显示帮助
  [cyan]/clear[/cyan]   重置对话历史
  [cyan]/tools[/cyan]   列出已加载的工具
  [cyan]/model[/cyan]   显示当前模型
  [cyan]/cost[/cyan]    显示累计 token 和成本
  [cyan]/exit[/cyan]    退出

[dim]在 agent 运行时按 Ctrl+C 可中断当前回合（对话仍然保留）。
在输入提示处按 Ctrl+D 直接退出。[/dim]"""


class TUI:
    """Stateful terminal chat interface.

    Holds one Agent instance and one conversation list across turns.
    Each user input kicks off `agent.run_stream(...)` and renders the
    event stream to the console.
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        self.conversation: list[Message] = []

        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            console.print(
                "[red]OPENAI_API_KEY is not set.[/red] "
                "请先 export OPENAI_API_KEY=sk-..."
            )
            sys.exit(1)

        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        config = CalciferConfig(
            api_key=api_key,
            base_url=os.environ.get("OPENAI_BASE_URL"),  # None → Agent 解析 env fallback
            model=self.model,
        )
        tools = get_all_builtin_tools()
        self.tool_names = [t.name for t in tools]
        self.agent = Agent(config=config, tools=tools)

        self.session: PromptSession[str] = PromptSession(
            history=InMemoryHistory(),
            multiline=False,
        )

    # -- main loop --------------------------------------------------------

    async def run(self) -> None:
        self.console.print(BANNER)
        while True:
            try:
                line = await self.session.prompt_async(
                    HTML("\n<ansicyan><b>you</b></ansicyan> › ")
                )
            except (EOFError, KeyboardInterrupt):
                self.console.print("\n[dim]bye.[/dim]")
                return

            line = line.strip()
            if not line:
                continue

            if line.startswith("/"):
                if self._slash(line):
                    return  # /exit
                continue

            await self._turn(line)

    # -- slash commands ---------------------------------------------------

    def _slash(self, cmd: str) -> bool:
        """Handle a slash command. Returns True iff the app should exit."""
        head = cmd.lower().split()[0]
        if head in ("/exit", "/quit"):
            self.console.print("[dim]bye.[/dim]")
            return True
        if head == "/help":
            self.console.print(HELP)
        elif head == "/clear":
            self.conversation = []
            self.console.print("[dim]conversation cleared.[/dim]")
        elif head == "/tools":
            self.console.print(
                "[dim]loaded tools:[/dim] "
                + ", ".join(f"[cyan]{n}[/cyan]" for n in self.tool_names)
            )
        elif head == "/model":
            self.console.print(f"[dim]model:[/dim] [cyan]{self.model}[/cyan]")
        elif head == "/cost":
            cost = self.agent.cost_tracker.get_cost()
            self.console.print(f"[dim]cumulative cost:[/dim] ${cost:.6f}")
        else:
            self.console.print(f"[yellow]unknown command:[/yellow] {cmd}")
        return False

    # -- one agent turn ---------------------------------------------------

    async def _turn(self, prompt: str) -> None:
        """Render one call to agent.run_stream()."""
        text_streaming = False
        printed_assistant_header = False

        # Wire Ctrl+C (SIGINT) to the agent's abort event for the duration
        # of this turn only. We use asyncio.add_signal_handler so it cooperates
        # with the running event loop; we restore the previous behavior in the
        # finally block.
        loop = asyncio.get_running_loop()
        aborted = False

        def on_sigint() -> None:
            nonlocal aborted
            aborted = True
            self.agent._abort_event.set()

        try:
            loop.add_signal_handler(signal.SIGINT, on_sigint)
        except (NotImplementedError, RuntimeError):
            # Windows / threaded contexts where signal handlers aren't available
            pass

        try:
            async for event in self.agent.run_stream(prompt, messages=self.conversation):
                t = event.type
                if t == "text_delta":
                    if not printed_assistant_header:
                        self.console.print(
                            "[bold orange1]calcifer[/bold orange1] › ", end=""
                        )
                        printed_assistant_header = True
                    text_streaming = True
                    sys.stdout.write(event.text or "")
                    sys.stdout.flush()

                elif t == "tool_call_start":
                    if text_streaming:
                        sys.stdout.write("\n")
                        text_streaming = False
                    args_preview = (event.tool_call_arguments or "").replace("\n", " ")
                    if len(args_preview) > 120:
                        args_preview = args_preview[:117] + "..."
                    self.console.print(
                        f"  [magenta]→[/magenta] "
                        f"[bold magenta]{event.tool_call_name}[/bold magenta] "
                        f"[dim]{args_preview}[/dim]"
                    )
                    printed_assistant_header = False

                elif t == "tool_call_result":
                    content = (event.tool_result_content or "").replace("\n", " ")
                    if len(content) > 200:
                        content = content[:197] + "..."
                    style = "red" if event.tool_is_error else "green"
                    self.console.print(
                        f"  [magenta]←[/magenta] [{style}]{content}[/{style}]"
                    )

                elif t == "error":
                    if text_streaming:
                        sys.stdout.write("\n")
                        text_streaming = False
                    self.console.print(
                        f"[red]error:[/red] {event.error}"
                        + (f" [dim](code {event.error_code})[/dim]" if event.error_code else "")
                    )

                elif t == "run_complete" and event.result:
                    if text_streaming:
                        sys.stdout.write("\n")
                        text_streaming = False
                    # Persist full conversation so the next turn has context
                    self.conversation = event.result.messages
                    usage = event.result.usage
                    cost = self.agent.cost_tracker.get_cost()
                    self.console.print(
                        f"[dim]turns={event.result.turn_count} "
                        f"tokens={usage.total_tokens} "
                        f"cost=${cost:.6f}[/dim]"
                    )

        except asyncio.CancelledError:
            self.console.print("\n[yellow]aborted.[/yellow]")
        finally:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, ValueError):
                pass
            if aborted:
                # Reset the agent's abort flag so the next turn starts clean
                self.agent._abort_event.clear()
                self.console.print("[yellow]turn aborted.[/yellow]")


# -- entry points ---------------------------------------------------------

async def amain() -> None:
    console = Console()
    tui = TUI(console)
    try:
        await tui.run()
    finally:
        await tui.agent.close()


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
