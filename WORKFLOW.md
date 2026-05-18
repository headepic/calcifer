---
tracker:
  kind: linear
  api_key: $LINEAR_API_KEY
  project_slug: "calcifer-sdk-5b906d04280f"
  active_states:
    - Todo
    - In Progress
  terminal_states:
    - Done
    - Canceled
    - Cancelled
    - Duplicate
polling:
  interval_ms: 10000
workspace:
  root: ~/code/calcifer-symphony-workspaces
hooks:
  timeout_ms: 180000
  after_create: |
    set -eu
    SOURCE_REPO="${CALCIFER_SOURCE_REPO:-/Users/jowang/Documents/github/calcifer}"
    REMOTE_URL="${CALCIFER_REMOTE_URL:-git@github.com:headepic/calcifer.git}"
    git clone "$SOURCE_REPO" .
    git remote set-url origin "$REMOTE_URL" || true
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -e ".[dev]"
  before_run: |
    set -eu
    if [ ! -x .venv/bin/python ]; then
      python3 -m venv .venv
      .venv/bin/python -m pip install --upgrade pip
      .venv/bin/python -m pip install -e ".[dev]"
    fi
agent:
  max_concurrent_agents: 1
  max_turns: 18
codex:
  command: codex --config shell_environment_policy.inherit=all --config 'model="gpt-5.5"' --config model_reasoning_effort=high app-server
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy:
    type: workspaceWrite
---

You are working on a Linear issue for the Calcifer SDK repository.

Issue:
- Identifier: {{ issue.identifier }}
- Title: {{ issue.title }}
- Status: {{ issue.state }}
- Labels: {{ issue.labels }}
- URL: {{ issue.url }}

Description:
{% if issue.description %}
{{ issue.description }}
{% else %}
No description provided.
{% endif %}

Repository contract:
- Work only inside the Symphony-provided workspace.
- Follow `AGENTS.md` exactly.
- Calcifer is provider-agnostic and targets OpenAI-compatible `/v1/chat/completions` APIs.
- Do not add Anthropic-only behavior such as cache_control, beta headers, or prompt caching.
- Do not add a tool permission system.
- All new implementation code needs focused mock tests under `tests/test_*.py`.
- E2E tests need a real LLM and should stay excluded from the default validation run.

Default validation command:

```bash
.venv/bin/python -m pytest tests/ -q \
  --ignore=tests/test_e2e_real.py \
  --ignore=tests/test_e2e_mcp_skill.py
```

Workflow:
1. If the issue is `Todo`, move it to `In Progress` before doing implementation work.
2. Find or create one persistent Linear comment headed `## Codex Workpad`; update that same comment throughout the run.
3. Put a compact plan, acceptance criteria, and validation checklist in the workpad before editing files.
4. Reproduce or inspect the current behavior first, then implement the smallest change that satisfies the issue.
5. Keep the workpad current after each meaningful milestone.
6. Run the default validation command, plus any issue-specific validation.
7. Commit successful work on a branch named from the issue identifier.
8. If all acceptance criteria and validation pass, update the workpad with commit and test evidence, then move the issue to `Done`.
9. If blocked by missing external credentials, unreachable services, or unavailable required tools, update the workpad with the exact blocker and move the issue back to `Backlog`.

Linear access:
- Prefer any available Linear MCP/plugin tools.
- If Symphony exposes a `linear_graphql` tool, use it for issue comments and state changes when higher-level Linear tools are unavailable.
- Do not create extra summary comments; keep progress in the single `## Codex Workpad` comment.

Completion bar:
- Code is committed.
- Workpad checklists are accurate.
- Required tests are run and recorded.
- The Linear issue state reflects reality.
