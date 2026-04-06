# Feature Contract: sdk-pyproject-metadata

> NEW FEATURE — fill in every section before starting implementation.
> See `harness/contracts/README.md` for the full template and guidance.

## Motivation

TODO: one paragraph — what problem does this solve and why now?

## Claude Code Reference

TODO: concrete file paths + line numbers in
`/Users/jowang/Documents/github/claude-code-source/` that this feature maps to.
If no direct analog exists, say so explicitly.

## Scope

### 要做

- TODO

### 不做 (non-goals)

- TODO

## Design

TODO: what files change, what interfaces are added/modified, how it integrates
with existing code. No final code — just enough for a reviewer to sanity-check.

## Acceptance Criteria

- [ ] TODO: verifiable assertion 1
- [ ] TODO: verifiable assertion 2

## Verification Commands

Update `features.json` verification list to match. Prefer:
- `.venv/bin/python -c "from X import Y; assert ..."` for import/attribute checks
- `.venv/bin/python -m pytest tests/test_foo.py -q -k 'new_test_name'` for behavior
- Full mock suite at the end to catch regressions

## Rollback Plan

TODO: what to do if this turns out to be wrong scope or infeasible.
