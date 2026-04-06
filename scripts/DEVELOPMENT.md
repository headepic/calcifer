# Calcifer Self-Development with Harness

Use calcifer's own harness to develop new calcifer features reliably. The workflow
ensures tests pass, code style is consistent, and no Anthropic-specific regressions
slip in.

## Prerequisites

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # or OPENAI_API_KEY
.venv/bin/python -m pytest tests/ -q  # baseline: 479 tests pass
```

## Two modes, same goal

### Pipeline mode — RECOMMENDED for new features

Single command, plan → build → evaluate, quality-gated.

```bash
python scripts/dev_harness.py pipeline "Add Pydantic v2 strict mode support to @tool decorator"
```

Runs:
1. **Planner** reads the spec + calcifer conventions, writes `plan.md` and `feature_list.json`
2. **Generator** implements features, runs tests, commits
3. **Evaluator** grades on 4 calcifer-specific criteria:
   - `functionality` — does it actually work?
   - `test_coverage` — new tests added + all 479 existing tests still pass?
   - `code_quality` — calcifer style (type hints, no emojis, dataclasses, logging)?
   - `provider_agnostic` — no Anthropic-specific features introduced?
4. If any criterion < 7, loops back to generator with the evaluator's feedback

### Session mode — for larger refactors spanning multiple context windows

Each session is a full context reset. State lives in files.

```bash
python scripts/dev_harness.py session "Refactor the compact context manager into separate modules per layer"
```

Resume at any time:

```bash
python scripts/dev_harness.py status       # see where you are
python scripts/dev_harness.py resume        # continue from the last session
```

## Pre-baked calcifer context

The script automatically injects these rules into every agent prompt:

- **Tests:** Must run `.venv/bin/python -m pytest ... --ignore=...e2e_real --ignore=...e2e_mcp_skill --ignore=...tui_web`
- **Never delete or weaken tests** — fix the code instead
- **Provider-agnostic constraint** — no `cache_control`, `tool_reference`, beta headers
- **Code style** — `from __future__ import annotations`, type hints, dataclasses, no emojis, `logger = logging.getLogger(__name__)`
- **Commit style** — imperative first line, bullet body, `Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>`

You do NOT need to repeat these in your spec.

## Cost control

Defaults:
- Pipeline: `$10` max
- Session: `$20` max

Override:
```bash
export CALCIFER_DEV_MAX_COST=5.00
python scripts/dev_harness.py pipeline "..."
```

Kill at any time with Ctrl+C. Session mode resumes; pipeline mode restarts the current round.

## Model override

```bash
export CALCIFER_DEV_MODEL=claude-opus-4-5
```

Defaults:
- If `ANTHROPIC_API_KEY` set → `claude-sonnet-4-5`
- Else → `gpt-4o`

## Typical workflow

```bash
# 1. Clean any previous harness state
python scripts/dev_harness.py clean

# 2. Describe the feature in natural language
python scripts/dev_harness.py pipeline "Add a --dry-run flag to the Agent.run() method \
that prints what would be executed without actually calling the LLM or running tools"

# 3. Watch the event stream. When done, verify:
.venv/bin/python -m pytest tests/ -x -q --ignore=tests/test_e2e_real.py \
  --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py

# 4. Review the commits
git log --oneline -10
git diff main

# 5. If you like it, push. If not, git reset --hard and try again with a refined spec.
```

## Writing good specs

Good specs are specific about WHAT, not HOW:

**Bad:**
```
"Improve error handling"
```

**Good:**
```
"When LLMProviderError with error_type=RATE_LIMITED is raised and the retry-after
header is present, the provider should wait the specified time before the next retry
instead of using exponential backoff. Add a test that mocks the response with
retry-after=5 and verifies the sleep call uses that value."
```

The pipeline evaluator is skeptical by default — vague specs lead to vague
implementations that fail grading.

## When to use which mode

| Your task | Mode | Why |
|-----------|------|-----|
| Add a single feature or method | Pipeline | Fast iteration, built-in QA |
| Fix a specific bug | Pipeline | One shot with verification |
| Refactor a module | Session | May span hours, needs checkpoints |
| Implement a whole subsystem | Session | Multi-feature, multi-file |
| Experimental/uncertain scope | Session | Can pause and inspect between sessions |

## Observing what the agent is doing

The script prints a real-time event stream. Key events:

```
[generator-r1] → file_read({"file_path": "calcifer/agent.py"})
[generator-r1] → file_edit({"file_path": ..., "old_string": ..., ...})
[generator-r1] → bash({"command": ".venv/bin/python -m pytest tests/test_p0.py -x -q"})
```

Each round ends with `turn_end` markers. When the pipeline finishes, the final
`EvalResult.summary()` prints pass/fail + scores + issues.

## Debugging a failed run

1. **Check `eval_result.json`** — what issues did the evaluator find?
2. **Check `claude-progress.txt`** — session-by-session log of what happened
3. **Check `sprint_result.md`** — what the generator claimed to build
4. **Check `git log`** — actual commits made during the run
5. **Re-run failing tests** manually to confirm the state

If the feature list got corrupted, restore from a `.bak` file:

```bash
ls feature_list.*.bak
cp feature_list.20260406-143022.bak feature_list.json
python scripts/dev_harness.py resume
```

## Skipping the harness

For trivial changes (typo fixes, docstring tweaks, one-line bug fixes), skip the
harness entirely — it's overhead. Use the harness when you want:
- Automated test verification
- Independent QA grading
- Multi-session continuity
- Structured progress tracking across context windows
