# Feature Contract: sdk-pypi-release-v03

## Motivation

Five previous SDK features (py-typed, pyproject-metadata,
config-env-defaults, agent-run-sync, public-api-audit,
changelog-semver, github-actions-ci) have prepared the repo to
ship v0.3.0 to PyPI. What remains splits cleanly into two halves:

1. **Things this feature ships and the harness can verify**:
   a release runbook (`RELEASING.md`), and a packaging test that
   actually builds the wheel, introspects it, and proves
   `py.typed` ships inside the built artifact (not just the
   editable install), the wheel metadata matches `pyproject.toml`,
   and `twine check` passes.
2. **Things only a human maintainer can do** (out of scope):
   create the PyPI trusted publisher on the web UI, merge
   `sdk-refactor` to `main`, bump `version = "0.3.0.dev0"` →
   `"0.3.0"`, tag `v0.3.0`, push the tag, watch the publish
   workflow, post-publish bump to `0.3.1.dev0`.

The runbook documents half 2; the test + build verification is
half 1. By the time a human reads `RELEASING.md` the repo is
already provably buildable and the wheel is already provably
correct — the runbook only covers the external steps.

## Claude Code Reference

No direct analog — Claude Code is a TypeScript app distributed via
npm. The pattern here is standard PyPA trusted-publishing:
- https://docs.pypi.org/trusted-publishers/
- https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/

## Scope

### 要做

- Create `RELEASING.md` at repo root documenting the end-to-end
  release procedure for a maintainer:
  1. **One-time setup** (first release only): create PyPI
     trusted publisher for `headepic/calcifer` pointing at
     `.github/workflows/publish.yml` with no environment.
     Optionally the same on test.pypi.org if the commented
     TestPyPI step in `publish.yml` is to be used for a rehearsal.
  2. **Per-release checklist**: CI green on `main` → promote
     `[Unreleased]` in `CHANGELOG.md` to `[X.Y.Z] - YYYY-MM-DD` →
     bump `pyproject.toml` version from `X.Y.Z.devN` to `X.Y.Z` →
     commit, push, wait for CI green → `git tag -s vX.Y.Z -m
     "Release vX.Y.Z"` → `git push origin vX.Y.Z` (triggers
     publish.yml) → watch the run → smoke test via
     `pip install --no-cache-dir calcifer==X.Y.Z` in a fresh venv
     and `python -c "from calcifer import Agent; print(Agent.__doc__)"`.
  3. **Post-release**: bump `version` to `X.Y.(Z+1).dev0`, insert
     a fresh `## [Unreleased]` header at the top of
     `CHANGELOG.md`, commit.
  4. **Rollback**: PyPI releases cannot be un-published. Use
     `twine yank` with a reason to hide a broken version, then
     publish X.Y.(Z+1) with the fix. Deleting a git tag does NOT
     un-publish the wheel. Document this explicitly.
  5. Every irreversible step (tag push, twine upload) is marked
     with a `⚠️ human-only` admonition.
- Add `tests/test_wheel_contents.py` with 4 tests:
  - `test_wheel_can_be_built` — runs `python -m build --wheel
    --outdir <tmp>` via subprocess, asserts a `.whl` is produced.
    Uses `pytest.importorskip("build")` so a minimal env skips.
  - `test_wheel_contains_py_typed` — unpacks the freshly-built
    wheel with `zipfile` and asserts `calcifer/py.typed` is inside.
    This closes a gap: the existing `test_py_typed_marker_present`
    only checks the editable source tree, not the built artifact.
  - `test_wheel_metadata_matches_pyproject` — reads the wheel's
    `*.dist-info/METADATA` file, parses it with `email.parser`,
    asserts `Name: calcifer`, `Version` matches `pyproject.toml`'s
    `[project].version`, and the classifiers include
    `Typing :: Typed` and `License :: OSI Approved :: MIT License`.
  - `test_twine_check_passes` — runs `python -m twine check
    <built artifacts>` via subprocess and asserts the output
    contains `PASSED` for each file. Uses
    `pytest.importorskip("twine")`.
- Update `.github/workflows/ci.yml` install line to add `build`
  and `twine` so the new tests run meaningfully in CI (not
  silently skipped). New line:
  `pip install -e . pytest pytest-asyncio PyYAML build twine`

### 不做 (non-goals)

- **Not bumping version to `0.3.0`**. Version stays `0.3.0.dev0`
  until the human follows `RELEASING.md`.
- **Not creating the PyPI trusted publisher** (pypi.org UI only).
- **Not merging `sdk-refactor` → `main`**. User asked to stay on
  the feature branch.
- **Not tagging `v0.3.0`** or pushing any tag from this session.
- **Not running `twine upload`** against real PyPI or TestPyPI.
- **Not adding signing / attestations**. `pypa/gh-action-pypi-publish`
  v1 emits PEP 740 attestations automatically via OIDC.

## Design

### `RELEASING.md` structure

Single Markdown file. Five sections matching the checklist above.
Every shell command copy-paste-ready with explicit version numbers
(no `<PLACEHOLDER>` handwaves in the command blocks — use `0.3.0`
as the concrete first-release example). Every irreversible step
prefixed with `⚠️ human only`.

### `tests/test_wheel_contents.py`

Single pytest module. Module-scoped fixture builds the wheel once
into `tmp_path_factory.mktemp("wheel")` and returns the wheel path
— avoids rebuilding for every test.

```python
import subprocess, sys, zipfile, tomllib
from email.parser import Parser
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent

@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory):
    pytest.importorskip("build")
    out = tmp_path_factory.mktemp("wheel")
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out)],
        cwd=ROOT, check=True, capture_output=True,
    )
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, f"expected 1 wheel, got {wheels}"
    return wheels[0]
```

Subsequent tests depend on `built_wheel`. `test_twine_check_passes`
runs `twine check` against the same `built_wheel`.

### `ci.yml` install line update

Add `build twine` to the existing install. That's the only CI
workflow change. Publish workflow is untouched.

## Acceptance Criteria

- [ ] `RELEASING.md` exists at repo root
- [ ] `RELEASING.md` contains the one-time trusted publisher section (matches substring "trusted publisher")
- [ ] `RELEASING.md` contains the rollback / yank section (matches substring "yank")
- [ ] `RELEASING.md` references `publish.yml`
- [ ] `tests/test_wheel_contents.py` exists with 4 tests
- [ ] `test_wheel_contains_py_typed` proves `calcifer/py.typed` is inside the built wheel
- [ ] `test_wheel_metadata_matches_pyproject` verifies wheel METADATA `Name`, `Version`, and key classifiers
- [ ] All 4 new tests pass locally
- [ ] `.github/workflows/ci.yml` install line contains both `build` and `twine`
- [ ] `pyproject.toml` version remains `0.3.0.dev0` (NOT bumped)
- [ ] All existing mock tests still pass after adding the new test module

## Verification Commands

```
.venv/bin/python -c "from pathlib import Path; p=Path('RELEASING.md'); assert p.exists(), 'RELEASING.md missing'; t=p.read_text(); assert 'trusted publisher' in t.lower(), 'RELEASING.md missing trusted publisher section'; assert 'yank' in t.lower(), 'RELEASING.md missing rollback/yank section'; assert 'publish.yml' in t, 'RELEASING.md should reference publish.yml'"
.venv/bin/python -c "import tomllib; d=tomllib.loads(open('pyproject.toml').read()); assert d['project']['version']=='0.3.0.dev0', f\"version should stay 0.3.0.dev0, got {d['project']['version']!r}\""
.venv/bin/python -c "import yaml; from pathlib import Path; ci=yaml.safe_load(Path('.github/workflows/ci.yml').read_text()); steps=ci['jobs']['test']['steps']; install_run=next(s['run'] for s in steps if 'run' in s and 'pip install' in s['run']); assert 'build' in install_run and 'twine' in install_run, f'ci.yml install line missing build/twine: {install_run!r}'"
.venv/bin/python -m pytest tests/test_wheel_contents.py -q
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

Must match `features.json` verification array verbatim.

## Rollback Plan

`git revert` removes `RELEASING.md`, the new test file, and the
one-line `ci.yml` install-line change. No runtime code in
`calcifer/` is touched. No external state is affected (no tags,
no uploads, no trusted publishers). This feature is fully local.
