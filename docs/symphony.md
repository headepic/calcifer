# Symphony + Linear setup

This repository is configured for local Symphony runs against the Linear project
`Calcifer SDK`.

## Linear

- Team: `Johnny7epic`
- Project: `Calcifer SDK`
- Symphony project slug: `calcifer-sdk-5b906d04280f`
- Labels: `Calcifer`, `Symphony`

Symphony only polls issues in `Todo` or `In Progress`. Keep draft or unsafe tasks
in `Backlog`; move an issue to `Todo` when it is ready for an unattended run.

## Start Symphony

Set a Linear personal API key first:

```bash
export LINEAR_API_KEY=lin_api_...
```

Optional overrides:

```bash
export CALCIFER_SOURCE_REPO=/Users/jowang/Documents/github/calcifer
export CALCIFER_REMOTE_URL=git@github.com:headepic/calcifer.git
```

Run the daemon:

```bash
symphony \
  --i-understand-that-this-will-be-running-without-the-usual-guardrails \
  --logs-root ~/code/calcifer-symphony-logs \
  --port 4050 \
  /Users/jowang/Documents/github/calcifer/WORKFLOW.md
```

Dashboard:

```text
http://localhost:4050
```

Workspaces are created under:

```text
~/code/calcifer-symphony-workspaces
```

## Issue template

Use this shape for Calcifer implementation issues:

```md
Repo: /Users/jowang/Documents/github/calcifer

Task:
Implement ...

Acceptance criteria:
- ...
- New or updated mock tests under `tests/test_*.py`
- Default mock test suite passes

Validation:
`.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py`

Codex reference:
- `../Codex-source/path/to/file.ts:line-line`
- Mirror: ...
- Intentionally skip: ...
```

## Expected Symphony behavior

1. Picks up `Todo` / `In Progress` issues from the `Calcifer SDK` project.
2. Creates an isolated workspace.
3. Clones Calcifer, creates `.venv`, and installs `.[dev]`.
4. Starts `codex app-server` in that workspace.
5. Keeps one Linear `## Codex Workpad` comment updated.
6. Runs mock tests and commits changes.
7. Moves the issue to `Done` only after validation passes.
