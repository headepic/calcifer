"""Ask your codebase one question, get one answer.

This is a deliberately small Calcifer SDK consumer that exercises the
parts of the SDK the TUI doesn't:

- `Agent.run_sync()` — one-shot batch usage (not streaming)
- `@tool` decorator for a custom tool (`git_log`)
- `register_stop_hook` to cap total turns
- `calcifer.testing.MockProvider` for unit tests (see tests/)

It builds an agent with read-only tools (Glob, Grep, FileRead) plus a
`git_log` custom tool, feeds the user's question, and prints the final
answer. Perfect for `ask "how does X work in this codebase?"`.

Run:
    export OPENAI_API_KEY=sk-...
    export OPENAI_BASE_URL=https://api.openai.com/v1    # optional
    export OPENAI_MODEL=gpt-4o-mini                     # optional
    ask "how does the Agent loop handle 429 errors?"
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Any

from calcifer import Agent, CalciferConfig, tool
from calcifer.services.hooks import HookInput
from calcifer.tool_registry import get_all_builtin_tools
from calcifer.types.message import Message
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel


SYSTEM_PROMPT = """你是一个代码库问答助手。用户会问一个关于当前仓库的问题，
你需要用提供的只读工具（glob / grep / file_read / git_log）去调研，然后给出
一个准确、简洁、带文件路径和行号引用的答案。

规则：
- 回答用 Markdown 格式
- 引用代码位置时用 `file.py:L42` 格式
- 不确定时说"不确定"，不要编造
- 至多调用 8 次工具就必须给出最终答案
- 不要修改任何文件
"""


MAX_TURNS = 8
READ_ONLY_TOOL_NAMES = {"glob", "grep", "file_read", "git_log"}


# -- Custom tool ------------------------------------------------------------

@tool(
    name="git_log",
    description=(
        "查看最近的 git 提交历史。返回最近 N 条 commit 的 hash、作者、日期和 "
        "subject。用于快速了解一个模块/文件最近的变更历史。"
    ),
)
def git_log(path: str = ".", limit: int = 10) -> str:
    """Return the last `limit` git commits touching `path`."""
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                f"--max-count={limit}",
                "--pretty=format:%h  %ai  %an  %s",
                "--",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"git_log error: {e}"
    if result.returncode != 0:
        return f"git_log error (exit {result.returncode}): {result.stderr.strip()}"
    return result.stdout.strip() or "(no commits)"


# -- Agent construction -----------------------------------------------------

def build_agent(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> tuple[Agent, dict[str, int]]:
    """Build an Agent configured for codebase Q&A.

    Returns (agent, tool_call_counter). The counter is a mutable dict the
    stop hook writes into so the caller can see how many tool calls
    happened.
    """
    config = CalciferConfig(
        api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
        base_url=base_url or os.environ.get("OPENAI_BASE_URL"),
        model=model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        system_prompt=SYSTEM_PROMPT,
    )
    if not config.api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    # Filter built-in tools to read-only set, then add git_log
    builtins = [t for t in get_all_builtin_tools() if t.name in READ_ONLY_TOOL_NAMES]
    tools = builtins + [git_log]

    agent = Agent(config=config, tools=tools)

    # Register a stop hook that counts tool calls across all turns and
    # forces the loop to end once we hit MAX_TURNS. `register_stop_hook`
    # is called after every tool turn with (conversation, context).
    counter = {"turns": 0, "tool_calls": 0}

    def cap_turns(conversation: list[Message], context: Any) -> bool:
        counter["turns"] += 1
        counter["tool_calls"] += sum(
            1 for m in conversation if m.role == "tool"
        )
        return counter["turns"] >= MAX_TURNS

    agent.register_stop_hook(cap_turns)
    return agent, counter


# -- Public entry -----------------------------------------------------------

def ask(question: str, *, agent: Agent | None = None) -> str:
    """Run one question through the agent and return the final answer.

    If `agent` is None, a fresh one is built from environment variables.
    This is the function unit tests target with a MockProvider-injected
    Agent.
    """
    if agent is None:
        agent, _counter = build_agent()
    result = agent.run_sync(question)
    return result.final_text


# -- CLI --------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ask",
        description="Ask a one-shot question about the current codebase.",
    )
    parser.add_argument(
        "question",
        nargs="+",
        help="The question to ask. Quote it if it has spaces.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override OPENAI_MODEL for this run.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print raw text output instead of rendered Markdown.",
    )
    args = parser.parse_args()

    question = " ".join(args.question)
    console = Console()

    try:
        agent, counter = build_agent(model=args.model)
    except RuntimeError as e:
        console.print(f"[red]error:[/red] {e}")
        sys.exit(1)

    console.print(
        Panel(
            f"[bold]{question}[/bold]",
            title="[dim]question[/dim]",
            border_style="cyan",
        )
    )

    try:
        result = agent.run_sync(question)
    except KeyboardInterrupt:
        console.print("\n[yellow]aborted.[/yellow]")
        sys.exit(130)

    cost = agent.cost_tracker.get_cost()
    footer = (
        f"[dim]turns={result.turn_count}  "
        f"tool_calls={counter['tool_calls']}  "
        f"tokens={result.usage.total_tokens}  "
        f"cost=${cost:.6f}[/dim]"
    )

    if args.raw:
        print(result.final_text)
    else:
        console.print(
            Panel(
                Markdown(result.final_text or "(no answer)"),
                title="[dim]answer[/dim]",
                border_style="orange1",
            )
        )
    console.print(footer)


if __name__ == "__main__":
    main()
