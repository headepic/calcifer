"""Harness usage examples.

Demonstrates both harness patterns:
1. SessionLoop — cross-session continuity (initializer → coding cycle)
2. Pipeline — planner → generator → evaluator

Run with a real LLM (requires API key):

    export OPENAI_API_KEY=sk-...
    python examples/harness_example.py session
    python examples/harness_example.py pipeline
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from calcifer import CalciferConfig
from calcifer.harness import (
    Pipeline,
    PipelineConfig,
    SessionConfig,
    SessionLoop,
)
from calcifer.tool_registry import get_all_builtin_tools


def _print_event(phase: str, event) -> None:
    """Simple event callback: shows text output and tool calls."""
    if event.type == "text_delta" and event.text:
        print(event.text, end="", flush=True)
    elif event.type == "tool_call_start":
        print(f"\n[{phase}] → {event.tool_call_name}({(event.tool_call_arguments or '')[:80]})", flush=True)
    elif event.type == "turn_end":
        print("", flush=True)


async def session_loop_example() -> None:
    """Run a SessionLoop on a small demo spec."""
    with tempfile.TemporaryDirectory(prefix="calcifer-harness-demo-") as tmpdir:
        print(f"Working in {tmpdir}\n")
        os.chdir(tmpdir)

        config = CalciferConfig(
            api_key=os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", ""),
            model=os.environ.get("CALCIFER_MODEL", "gpt-4o-mini"),
        )
        if not config.api_key:
            print("Set OPENAI_API_KEY or ANTHROPIC_API_KEY")
            return

        tools = get_all_builtin_tools()

        spec = (
            "Create a Python CLI tool that reads a CSV file, "
            "computes summary statistics (count, mean, min, max per numeric column), "
            "and prints them as a formatted table. "
            "Use only the standard library."
        )

        loop = SessionLoop(
            agent_config=config,
            tools=tools,
            spec=spec,
            session_config=SessionConfig(
                max_sessions=5,
                max_turns_per_session=30,
                max_cost_usd=2.00,  # cap at $2
            ),
            on_event=lambda e: _print_event("session", e),
        )

        # Phase 1: Initialize
        print("=" * 60)
        print("Phase 1: Initializer")
        print("=" * 60)
        await loop.initialize()
        print(f"\n\nProgress: {loop.get_progress()}")

        # Phase 2: Run coding sessions until done
        print("\n" + "=" * 60)
        print("Phase 2: Coding sessions")
        print("=" * 60)
        final = await loop.run_until_complete()
        print(f"\n\nFinal: {final}")


async def pipeline_example() -> None:
    """Run a Pipeline (planner → generator → evaluator)."""
    with tempfile.TemporaryDirectory(prefix="calcifer-pipeline-demo-") as tmpdir:
        print(f"Working in {tmpdir}\n")
        os.chdir(tmpdir)

        config = CalciferConfig(
            api_key=os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", ""),
            model=os.environ.get("CALCIFER_MODEL", "gpt-4o-mini"),
        )
        if not config.api_key:
            print("Set OPENAI_API_KEY or ANTHROPIC_API_KEY")
            return

        tools = get_all_builtin_tools()

        spec = (
            "Build a simple HTTP-based note-taking API with FastAPI. "
            "Endpoints: POST /notes (create), GET /notes (list), "
            "GET /notes/{id} (read), DELETE /notes/{id} (delete). "
            "Store in memory. Include automated tests."
        )

        pipeline = Pipeline(
            agent_config=config,
            tools=tools,
            spec=spec,
            pipeline_config=PipelineConfig(
                max_rounds=3,
                pass_threshold=7,
                max_cost_usd=5.00,
            ),
            on_event=_print_event,
        )

        print("=" * 60)
        print("Running pipeline...")
        print("=" * 60)
        result = await pipeline.run()

        print("\n\n" + "=" * 60)
        print("Pipeline Result")
        print("=" * 60)
        print(f"Passed: {result.passed}")
        print(f"Rounds: {len(result.rounds)}")
        print(f"Total cost: ${result.total_cost_usd:.4f}")
        if result.final_eval:
            print(f"\n{result.final_eval.summary()}")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("session", "pipeline"):
        print("Usage: python examples/harness_example.py [session|pipeline]")
        sys.exit(1)

    if sys.argv[1] == "session":
        asyncio.run(session_loop_example())
    else:
        asyncio.run(pipeline_example())


if __name__ == "__main__":
    main()
