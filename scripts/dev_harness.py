"""Calcifer self-development harness.

Uses calcifer's own harness workflow to develop new calcifer features reliably.
Pre-configured with calcifer's project structure, test suite, and conventions.

Usage:
    # Plan-then-build a new feature (Pipeline mode — recommended for new features)
    python scripts/dev_harness.py pipeline "Add support for streaming tool results to the TUI"

    # Long-form multi-session work (SessionLoop mode — for larger refactors)
    python scripts/dev_harness.py session "Migrate all tools to use async context managers"

    # Resume an in-progress SessionLoop task
    python scripts/dev_harness.py resume

    # Check progress
    python scripts/dev_harness.py status

Environment:
    CALCIFER_DEV_MODEL      Model override (default: claude-sonnet-4-5 if ANTHROPIC_API_KEY set,
                             else gpt-4o)
    CALCIFER_DEV_MAX_COST    Max cost USD (default: 10.00 for pipeline, 20.00 for session)
    ANTHROPIC_API_KEY       Required (or OPENAI_API_KEY)
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from calcifer import CalciferConfig
from calcifer.harness import (
    Pipeline,
    PipelineConfig,
    SessionConfig,
    SessionLoop,
)
from calcifer.harness.artifacts import FeatureList
from calcifer.tool_registry import get_all_builtin_tools


# ============================================================
# Calcifer project conventions baked into the spec wrapper
# ============================================================

CALCIFER_CONTEXT = """\
You are working on **Calcifer**, a provider-agnostic Python agent runner targeting
OpenAI-compatible APIs. It is a LIBRARY, not an end-user product.

## Project layout

```
calcifer/
├── agent.py              # Unified run/run_stream loop
├── config.py             # CalciferConfig dataclass
├── tool.py               # Tool base class + @tool decorator
├── tool_registry.py      # Built-in tool assembly
├── services/
│   ├── api/provider.py   # LLM provider (httpx, retry, backoff)
│   ├── compact/context.py # 6-layer context compaction
│   ├── tools/orchestrator.py # Tool execution + StreamingToolExecutor
│   ├── mcp/              # MCP client (4 transports)
│   ├── token_estimation.py
│   └── ...
├── tools/                # 8 built-in tools (Bash, File*, Glob, Grep, Skill, ToolSearch)
├── harness/              # Long-running agent workflows (what you're using now)
├── types/                # Message, ToolCall, Usage, etc.
├── coordinator/          # Multi-agent orchestration
├── skills/               # Skill loader + executor
├── memdir/               # Memory directory
├── telemetry/            # OpenTelemetry spans + metrics
├── tui/ web/             # Frontends
└── utils/                # sandbox, recovery, classifier, ...

tests/
├── test_p0.py, test_functional.py, test_integration.py
├── test_context.py, test_tools.py, test_orchestrator.py
├── test_harness.py, test_mcp.py, test_skill.py
└── ...  (479 mock tests; do NOT run e2e_real/e2e_mcp_skill/tui_web)
```

## Hard rules — violations cause PR rejection

1. **All existing tests must continue passing.** Run:
   `.venv/bin/python -m pytest tests/ -x -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py`

2. **Write tests for new functionality.** Add to the relevant `tests/test_*.py` file.
   Follow the existing test style (pytest + AsyncMock, no fixtures beyond tmp_path).

3. **NEVER delete, weaken, or modify existing tests** to make them pass. Fix the code.

4. **Follow existing code style:**
   - Type hints on public APIs
   - `from __future__ import annotations` at top of modules
   - Dataclasses for config/result objects
   - No emojis anywhere
   - Logger via `logger = logging.getLogger(__name__)`
   - Error handling: log warnings, don't swallow exceptions silently

5. **Provider-agnostic constraint:** Do NOT add Anthropic API-specific features
   (cache_control, tool_reference, prompt caching betas, etc.). The target is
   OpenAI-compatible `/v1/chat/completions`.

6. **Commit messages:**
   - First line: imperative, under 70 chars
   - Body: bullet points of what changed and why
   - End with: `Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>`

7. **Run tests BEFORE committing.** Do not commit failing code.

## Testing pattern

Use the venv: `.venv/bin/python -m pytest ...`
Quick verification: `.venv/bin/python -m pytest tests/test_<relevant>.py -x -q`
Full suite: `.venv/bin/python -m pytest tests/ -x -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py`
Expected: 479 tests passing.

## Before marking a feature as passing
- Run the full mock test suite and confirm all pass
- Run the specific test file for your changes
- Verify the feature actually works (not just that tests pass)
- Commit the changes
"""


# ============================================================
# Calcifer-specific init.sh — sets up the test environment
# ============================================================

CALCIFER_INIT_SH = """\
#!/bin/bash
# Calcifer development environment — idempotent setup
set -e

if [ ! -d .venv ]; then
    python3 -m venv .venv
    .venv/bin/pip install -e ".[all,dev]"
fi

echo "Calcifer dev environment ready."
echo "Test command: .venv/bin/python -m pytest tests/ -x -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py"
"""


# ============================================================
# Defaults for calcifer development
# ============================================================

def _pick_model() -> str:
    """Pick a sensible default model."""
    if override := os.environ.get("CALCIFER_DEV_MODEL"):
        return override
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude-sonnet-4-5"
    return "gpt-4o"


def _pick_base_url() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "https://api.anthropic.com/v1"
    return "https://api.openai.com/v1"


def _build_config() -> CalciferConfig:
    """Build CalciferConfig for development runs."""
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("ERROR: set ANTHROPIC_API_KEY or OPENAI_API_KEY", file=sys.stderr)
        sys.exit(1)
    return CalciferConfig(
        api_key=api_key,
        base_url=_pick_base_url(),
        model=_pick_model(),
        max_tokens=8192,
        temperature=0.0,
        max_context_tokens=200_000,
    )


def _wrap_spec(user_spec: str) -> str:
    """Wrap the user's spec with calcifer project context."""
    return f"{CALCIFER_CONTEXT}\n\n## Feature request\n\n{user_spec}"


def _print_event(phase: str, event) -> None:
    """Minimal event printer."""
    if event.type == "text_delta" and event.text:
        print(event.text, end="", flush=True)
    elif event.type == "tool_call_start":
        args = (event.tool_call_arguments or "")[:100]
        print(f"\n[{phase}] → {event.tool_call_name}({args})", flush=True)
    elif event.type == "turn_end":
        print("", flush=True)
    elif event.type == "error":
        print(f"\n[ERROR] {event.error}", flush=True)


def _ensure_init_script() -> None:
    """Create init.sh if missing."""
    p = Path("init.sh")
    if not p.exists():
        p.write_text(CALCIFER_INIT_SH)
        p.chmod(0o755)


# ============================================================
# Commands
# ============================================================

async def cmd_pipeline(spec: str) -> None:
    """Run the full pipeline (plan → generate → evaluate)."""
    max_cost = float(os.environ.get("CALCIFER_DEV_MAX_COST", "10.00"))

    pipeline = Pipeline(
        agent_config=_build_config(),
        tools=get_all_builtin_tools(),
        spec=_wrap_spec(spec),
        pipeline_config=PipelineConfig(
            max_rounds=5,
            pass_threshold=7,
            max_cost_usd=max_cost,
            max_turns_per_agent=150,  # generous for calcifer-sized changes
            criteria={
                "functionality": (
                    "Does the feature work as specified? Can you demonstrate it working "
                    "with a quick manual test or by running the new tests?"
                ),
                "test_coverage": (
                    "Are there tests for the new functionality? Do ALL existing tests still pass? "
                    "Run: .venv/bin/python -m pytest tests/ -x -q --ignore=tests/test_e2e_real.py "
                    "--ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py"
                ),
                "code_quality": (
                    "Does the code follow calcifer conventions? Type hints, no emojis, "
                    "dataclasses, proper logging, __future__ annotations? No dead code?"
                ),
                "provider_agnostic": (
                    "Are there any Anthropic-specific features (cache_control, tool_reference, "
                    "beta headers)? If yes, this is a rejection."
                ),
            },
        ),
        on_event=_print_event,
    )

    print("=" * 60)
    print(f"Model: {pipeline._agent_config.model}")
    print(f"Max cost: ${max_cost}")
    print(f"Spec: {spec[:200]}...")
    print("=" * 60)

    result = await pipeline.run()

    print("\n\n" + "=" * 60)
    print("RESULT")
    print("=" * 60)
    print(f"Passed: {result.passed}")
    print(f"Rounds: {len(result.rounds)}")
    print(f"Total cost: ${result.total_cost_usd:.4f}")
    if result.final_eval:
        print(f"\n{result.final_eval.summary()}")

    if not result.passed:
        print("\nPipeline did NOT pass. Review eval_result.json and claude-progress.txt.")
        sys.exit(1)


async def cmd_session(spec: str) -> None:
    """Run a session loop for longer work."""
    max_cost = float(os.environ.get("CALCIFER_DEV_MAX_COST", "20.00"))

    _ensure_init_script()

    loop = SessionLoop(
        agent_config=_build_config(),
        tools=get_all_builtin_tools(),
        spec=_wrap_spec(spec),
        session_config=SessionConfig(
            max_sessions=30,
            max_turns_per_session=150,
            max_cost_usd=max_cost,
        ),
        on_event=lambda e: _print_event("session", e),
    )

    if not loop.is_initialized():
        print("Initializing project structure...")
        await loop.initialize()
        print(f"\n\nInitialized: {loop.get_progress()}\n")

    print("=" * 60)
    print(f"Model: {loop._agent_config.model}")
    print(f"Max cost: ${max_cost}")
    print(f"Starting sessions from: {loop._session_count}")
    print("=" * 60)

    result = await loop.run_until_complete()

    print("\n\n" + "=" * 60)
    print("RESULT")
    print("=" * 60)
    print(result)


async def cmd_resume() -> None:
    """Resume an in-progress SessionLoop (no new spec — just continue)."""
    if not Path("feature_list.json").exists():
        print("ERROR: no feature_list.json — nothing to resume")
        sys.exit(1)

    max_cost = float(os.environ.get("CALCIFER_DEV_MAX_COST", "20.00"))
    loop = SessionLoop(
        agent_config=_build_config(),
        tools=get_all_builtin_tools(),
        spec="",  # not used — already initialized
        session_config=SessionConfig(
            max_sessions=30,
            max_turns_per_session=150,
            max_cost_usd=max_cost,
        ),
        on_event=lambda e: _print_event("session", e),
    )

    print(f"Resuming from: {loop.get_progress()}")
    result = await loop.run_until_complete()
    print(f"\n\n{result}")


def cmd_status() -> None:
    """Show current harness progress without running anything."""
    fl_path = Path("feature_list.json")
    if not fl_path.exists():
        print("No harness task in progress (no feature_list.json)")
        return

    fl = FeatureList.load(fl_path)
    total = len(fl.features)
    done = len(fl.done)

    print(f"Feature list: {fl_path.absolute()}")
    print(f"Progress: {done}/{total} ({fl.progress_ratio:.0%})")

    if Path("plan.md").exists():
        print("Mode: Pipeline (plan.md exists)")
    else:
        print("Mode: SessionLoop")

    if fl.pending:
        print(f"\nPending ({len(fl.pending)}):")
        for f in fl.pending[:10]:
            print(f"  - [{f.category}] {f.description}")
        if len(fl.pending) > 10:
            print(f"  ... and {len(fl.pending) - 10} more")

    if fl.done:
        print(f"\nRecent done ({len(fl.done)}):")
        for f in fl.done[-3:]:
            print(f"  ✓ {f.description}")


def cmd_clean() -> None:
    """Remove harness artifacts from current directory."""
    import shutil

    artifacts = [
        "feature_list.json",
        "claude-progress.txt",
        "plan.md",
        "sprint_result.md",
        "eval_result.json",
        "init.sh",
    ]
    for a in artifacts:
        p = Path(a)
        if p.exists():
            p.unlink()
            print(f"removed {a}")

    for bak in Path(".").glob("feature_list.*.bak"):
        bak.unlink()
        print(f"removed {bak.name}")

    print("Harness state cleaned.")


# ============================================================
# Main
# ============================================================

def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    spec = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""

    if cmd == "pipeline":
        if not spec:
            print("ERROR: pipeline requires a spec")
            sys.exit(1)
        asyncio.run(cmd_pipeline(spec))
    elif cmd == "session":
        if not spec:
            print("ERROR: session requires a spec")
            sys.exit(1)
        asyncio.run(cmd_session(spec))
    elif cmd == "resume":
        asyncio.run(cmd_resume())
    elif cmd == "status":
        cmd_status()
    elif cmd == "clean":
        cmd_clean()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
