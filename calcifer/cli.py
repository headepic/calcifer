"""CLI entry point for Calcifer agent runner.

Usage:
    calcifer                          # Interactive TUI
    calcifer "prompt"                 # Interactive TUI with initial prompt
    calcifer -p "prompt"              # Print mode (non-interactive)
    calcifer -p "prompt" -f json      # JSON output
    calcifer -p "prompt" -f stream    # Stream-JSON output (for piping)
    calcifer --web                    # Web GUI (opens browser)

    calcifer harness init "spec"      # Initialize a session loop project
    calcifer harness run              # Run session loop until complete
    calcifer harness status           # Show current harness progress
    calcifer harness pipeline "spec"  # Run planner→generator→evaluator pipeline

Options:
    -m, --model MODEL         LLM model name
    -k, --api-key KEY         API key (or set OPENAI_API_KEY / ANTHROPIC_API_KEY)
    -b, --base-url URL        API base URL
    -s, --system-prompt TEXT   System prompt
    -t, --max-turns N         Maximum agent loop turns
    -p, --print               Print mode (non-interactive)
    -f, --format FORMAT       Output format: text, json, stream (print mode only)
    --web                     Start Web GUI
    --port PORT               Web GUI port (default: 8422)
    --tools                   Include built-in tools (bash, file_read, etc.)
    --no-tools                Disable built-in tools
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


def main() -> None:
    # Handle harness subcommand separately (preserves existing CLI behavior)
    if len(sys.argv) > 1 and sys.argv[1] == "harness":
        _harness_main(sys.argv[2:])
        return

    parser = argparse.ArgumentParser(
        prog="calcifer",
        description="Calcifer Agent Runner — LLM agent with tool calling",
    )
    parser.add_argument("prompt", nargs="?", default=None, help="Initial prompt")
    parser.add_argument("-m", "--model", default=None, help="Model name")
    parser.add_argument("-k", "--api-key", default=None, help="API key")
    parser.add_argument("-b", "--base-url", default=None, help="API base URL")
    parser.add_argument("-s", "--system-prompt", default=None, help="System prompt")
    parser.add_argument("-t", "--max-turns", type=int, default=None, help="Max turns")
    parser.add_argument("-p", "--print", dest="print_mode", action="store_true", help="Print mode")
    parser.add_argument("-f", "--format", dest="output_format", default="text",
                        choices=["text", "json", "stream"], help="Output format (print mode)")
    parser.add_argument("--tools", dest="use_tools", action="store_true", default=True,
                        help="Include built-in tools")
    parser.add_argument("--no-tools", dest="use_tools", action="store_false",
                        help="Disable built-in tools")
    parser.add_argument("--thinking", default=None, choices=["disabled", "adaptive", "enabled"],
                        help="Thinking mode")
    parser.add_argument("--web", action="store_true", help="Start Web GUI")
    parser.add_argument("--port", type=int, default=8422, help="Web GUI port")

    args = parser.parse_args()

    # Build config
    from .config import CalciferConfig
    from .utils.settings import load_settings

    config = load_settings()

    # CLI overrides
    if args.api_key:
        config.api_key = args.api_key
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.system_prompt:
        config.system_prompt = args.system_prompt
    if args.max_turns:
        config.max_turns = args.max_turns
    if args.thinking:
        config.thinking_mode = args.thinking

    # Env var fallbacks
    if not config.api_key:
        config.api_key = (
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )

    # Default system prompt
    if not config.system_prompt:
        config.system_prompt = (
            "You are a helpful coding assistant. Use tools when needed. "
            "Be concise and precise."
        )

    # Tools
    tools = []
    if args.use_tools:
        from .tool_registry import get_all_builtin_tools
        tools = get_all_builtin_tools()

    # Output format mapping
    format_map = {"text": "text", "json": "json", "stream": "stream-json"}
    output_format = format_map.get(args.output_format, "text")

    # Run
    if args.web:
        from .web import run_server
        run_server(config, tools, port=args.port)
    elif args.print_mode:
        if not args.prompt:
            parser.error("Print mode requires a prompt: calcifer -p \"prompt\"")
        from .tui import run_print_mode
        asyncio.run(run_print_mode(config, args.prompt, tools, output_format=output_format))
    else:
        from .tui import run_tui
        asyncio.run(run_tui(config, tools, initial_prompt=args.prompt))


def _build_harness_config_from_args(args: argparse.Namespace):
    """Build CalciferConfig for harness subcommands."""
    from .config import CalciferConfig
    from .utils.settings import load_settings

    config = load_settings()
    if args.api_key:
        config.api_key = args.api_key
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if not config.api_key:
        config.api_key = (
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
    return config


def _harness_main(argv: list[str]) -> None:
    """Handle `calcifer harness <subcommand>`."""
    parser = argparse.ArgumentParser(
        prog="calcifer harness",
        description="Long-running agent harness workflows",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # Common options
    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("-m", "--model", default=None)
        p.add_argument("-k", "--api-key", default=None)
        p.add_argument("-b", "--base-url", default=None)
        p.add_argument("--work-dir", default=".", help="Working directory")
        p.add_argument("--feature-list", default="feature_list.json")
        p.add_argument("--progress", default="claude-progress.txt")
        p.add_argument("--max-cost", type=float, default=None, help="Max cost in USD")

    # harness init
    p_init = sub.add_parser("init", help="Initialize a new session loop project")
    add_common(p_init)
    p_init.add_argument("spec", help="Project specification")
    p_init.add_argument("--init-script", default="init.sh")

    # harness run
    p_run = sub.add_parser("run", help="Run coding sessions until complete")
    add_common(p_run)
    p_run.add_argument("spec", nargs="?", default="", help="Spec (if not initialized yet)")
    p_run.add_argument("--max-sessions", type=int, default=50)
    p_run.add_argument("--init-script", default="init.sh")

    # harness status
    p_status = sub.add_parser("status", help="Show current harness progress")
    p_status.add_argument("--work-dir", default=".")
    p_status.add_argument("--feature-list", default="feature_list.json")

    # harness pipeline
    p_pipe = sub.add_parser("pipeline", help="Run planner→generator→evaluator pipeline")
    add_common(p_pipe)
    p_pipe.add_argument("spec", help="Project specification")
    p_pipe.add_argument("--max-rounds", type=int, default=5)
    p_pipe.add_argument("--pass-threshold", type=int, default=7)
    p_pipe.add_argument("--plan", default="plan.md")

    args = parser.parse_args(argv)

    if args.cmd == "status":
        _harness_status(args)
        return

    # Build agent config
    config = _build_harness_config_from_args(args)
    if not config.api_key:
        print("Error: API key required (use -k or set ANTHROPIC_API_KEY/OPENAI_API_KEY)", file=sys.stderr)
        sys.exit(1)

    from .tool_registry import get_all_builtin_tools
    tools = get_all_builtin_tools()

    if args.cmd == "init":
        asyncio.run(_harness_init(config, tools, args))
    elif args.cmd == "run":
        asyncio.run(_harness_run(config, tools, args))
    elif args.cmd == "pipeline":
        asyncio.run(_harness_pipeline(config, tools, args))


def _print_event(phase: str, event) -> None:
    """Event callback: prints text deltas and tool calls inline."""
    if event.type == "text_delta" and event.text:
        print(event.text, end="", flush=True)
    elif event.type == "tool_call_start":
        print(f"\n[{phase}] → {event.tool_call_name}", flush=True)
    elif event.type == "turn_end":
        print("", flush=True)


async def _harness_init(config, tools, args) -> None:
    from .harness import SessionConfig, SessionLoop

    sc = SessionConfig(
        work_dir=args.work_dir,
        feature_list_path=args.feature_list,
        progress_path=args.progress,
        init_script_path=args.init_script,
        max_cost_usd=args.max_cost,
    )
    loop = SessionLoop(
        config, tools, spec=args.spec,
        session_config=sc,
        on_event=lambda e: _print_event("init", e),
    )
    print(f"Initializing project in {args.work_dir}...")
    await loop.initialize()
    print(f"\n\nDone. Progress: {loop.get_progress()}")


async def _harness_run(config, tools, args) -> None:
    from .harness import SessionConfig, SessionLoop

    sc = SessionConfig(
        work_dir=args.work_dir,
        feature_list_path=args.feature_list,
        progress_path=args.progress,
        init_script_path=args.init_script,
        max_sessions=args.max_sessions,
        max_cost_usd=args.max_cost,
    )
    loop = SessionLoop(
        config, tools, spec=args.spec,
        session_config=sc,
        on_event=lambda e: _print_event("coding", e),
    )

    if not loop.is_initialized():
        if not args.spec:
            print("Error: not initialized. Provide spec or run `harness init` first.", file=sys.stderr)
            sys.exit(1)
        print("Not initialized yet — running initializer first.")
        await loop.initialize()

    print(f"Running until complete (max {args.max_sessions} sessions)...")
    result = await loop.run_until_complete()
    print(f"\n\nFinal: {result}")


async def _harness_pipeline(config, tools, args) -> None:
    from .harness import Pipeline, PipelineConfig

    pc = PipelineConfig(
        work_dir=args.work_dir,
        max_rounds=args.max_rounds,
        pass_threshold=args.pass_threshold,
        max_cost_usd=args.max_cost,
        plan_path=args.plan,
        feature_list_path=args.feature_list,
        progress_path=args.progress,
    )
    pipeline = Pipeline(
        config, tools, spec=args.spec,
        pipeline_config=pc,
        on_event=_print_event,
    )
    print(f"Running pipeline (max {args.max_rounds} rounds, threshold {args.pass_threshold})...")
    result = await pipeline.run()
    print(f"\n\nPassed: {result.passed}")
    print(f"Rounds: {len(result.rounds)}")
    print(f"Total cost: ${result.total_cost_usd:.4f}")
    if result.final_eval:
        print(f"\n{result.final_eval.summary()}")


def _harness_status(args) -> None:
    from .harness.artifacts import FeatureList

    fl_path = Path(args.work_dir) / args.feature_list
    if not fl_path.exists():
        print(f"Not initialized (no {fl_path})")
        return

    fl = FeatureList.load(fl_path)
    total = len(fl.features)
    done = len(fl.done)
    pending = len(fl.pending)

    print(f"Feature list: {fl_path}")
    print(f"Progress: {done}/{total} ({fl.progress_ratio:.0%})")
    print(f"Pending: {pending}")
    if fl.pending:
        print("\nNext features:")
        for f in fl.pending[:5]:
            print(f"  - [{f.category}] {f.description}")
        if len(fl.pending) > 5:
            print(f"  ... and {len(fl.pending) - 5} more")


if __name__ == "__main__":
    main()
