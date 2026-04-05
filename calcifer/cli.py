"""CLI entry point for Calcifer agent runner.

Usage:
    calcifer                          # Interactive TUI
    calcifer "prompt"                 # Interactive TUI with initial prompt
    calcifer -p "prompt"              # Print mode (non-interactive)
    calcifer -p "prompt" -f json      # JSON output
    calcifer -p "prompt" -f stream    # Stream-JSON output (for piping)
    calcifer --web                    # Web GUI (opens browser)

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


def main() -> None:
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


if __name__ == "__main__":
    main()
