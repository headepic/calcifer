# Feature Contract: sdk-py-typed-marker

## Motivation

Calcifer has full type annotations throughout the codebase, but downstream
users who `pip install calcifer` and run `mypy` or `pyright` against their
code will get `Any` for every Calcifer symbol. That's because PEP 561
requires a `py.typed` marker file inside the package to signal "this package
ships type info".

Without this marker:
- `mypy` treats `from calcifer import Agent` as `Agent: Any`
- IDE autocomplete loses precision on calcifer types
- Users who care about type safety have to write stubs themselves

This is the smallest possible SDK-readiness fix. An empty file + one
pyproject.toml change.

## Claude Code Reference

No analog — this is pure Python packaging convention per
[PEP 561](https://peps.python.org/pep-0561/). Claude Code is TypeScript.

## Scope

### 要做

- Create empty `calcifer/py.typed` file
- Update `pyproject.toml` to include `py.typed` in the wheel package data
  via `[tool.hatch.build.targets.wheel]`
- Add a smoke test that verifies the marker is reachable at runtime

### 不做 (non-goals)

- Not fixing any actual type errors (there may be none, or many — out of scope)
- Not adding `mypy` to CI (that's a separate feature)
- Not pinning a minimum mypy version

## Design

Two file changes:

1. Create `calcifer/py.typed` — an empty file. Its mere presence is the signal.

2. Edit `pyproject.toml`. Current `[build-system]` uses hatchling. Need to
   tell hatch to include the `py.typed` marker in the built wheel. Add:
   ```toml
   [tool.hatch.build.targets.wheel]
   packages = ["calcifer"]
   ```
   Hatchling by default picks up all files inside a listed package, so the
   explicit `packages` line is usually enough. If the built wheel turns out
   to be missing the marker, also add:
   ```toml
   [tool.hatch.build.targets.wheel.force-include]
   "calcifer/py.typed" = "calcifer/py.typed"
   ```

3. Add a test in `tests/test_packaging.py` (new file) that imports calcifer
   and asserts `py.typed` exists alongside `calcifer/__init__.py`.

## Acceptance Criteria

- [ ] File `calcifer/py.typed` exists (size 0 is fine)
- [ ] `pyproject.toml` has `[tool.hatch.build.targets.wheel]` with
      `packages = ["calcifer"]` (or equivalent that includes py.typed)
- [ ] Runtime import check: `Path(calcifer.__file__).parent / "py.typed"` exists
- [ ] New test `test_py_typed_marker_present` in `tests/test_packaging.py`
- [ ] All 434 existing mock tests still pass

## Verification Commands

```
.venv/bin/python -c "from pathlib import Path; import calcifer; p = Path(calcifer.__file__).parent / 'py.typed'; assert p.exists(), f'py.typed missing at {p}'"
.venv/bin/python -m pytest tests/test_packaging.py -q -k 'py_typed_marker_present'
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

Must match the `verification` array in `harness/features.json`.

## Rollback Plan

Trivial. `rm calcifer/py.typed` and revert the pyproject.toml change with
`git revert`. No runtime behavior depends on this.
