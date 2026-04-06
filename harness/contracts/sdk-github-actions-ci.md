# Feature Contract: sdk-github-actions-ci

## Motivation

Calcifer is about to be published to PyPI as v0.3.0 (see
`sdk-pypi-release-v03`). Before that tag goes out, the repo needs:

1. **Continuous integration** — every push and PR must run the mock
   test suite on a Python matrix so regressions are caught before
   they reach `main`. Right now there is zero CI: `.github/` does
   not exist.
2. **Tag-triggered publish** — the v0.3.0 release (and every release
   after it) must build sdist + wheel and upload to PyPI from a
   trusted, reproducible environment, not from a maintainer's
   laptop with a long-lived API token. The current PyPA best
   practice is OIDC trusted publishing — no API key in the repo,
   no secret in GitHub.

This feature creates the two workflows.

## Claude Code Reference

No direct analog. Claude Code is a TypeScript app distributed via
npm with bespoke release tooling. The pattern here is standard
PyPA-recommended Python library publishing:
- [pypa/gh-action-pypi-publish](https://github.com/pypa/gh-action-pypi-publish)
- [Trusted Publishing](https://docs.pypi.org/trusted-publishers/)

## Scope

### 要做

- Create `.github/workflows/ci.yml`:
  - Triggers: `push` to `main` and `sdk-refactor`, plus `pull_request`
  - Job `test`: matrix over `python-version: ["3.11", "3.12", "3.13"]`
    and `os: [ubuntu-latest, macos-latest]` (6 cells total)
  - Steps: checkout, setup-python with the matrix version, install
    package via `pip install -e .[dev]` (or fall back to
    `pip install -e .` + `pip install pytest pytest-asyncio` if
    `[project.optional-dependencies].dev` doesn't exist), then run
    the mock test suite with the same exclusions used locally:
    `pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py`
  - The install step **must** include `PyYAML` explicitly. The new
    `tests/test_ci_workflows.py` imports `yaml`, and PyYAML is not
    a calcifer runtime dep — without this, CI would ImportError on
    its own workflow tests. The workflow install line should be:
    `pip install -e . pytest pytest-asyncio PyYAML`
  - Job `build`: runs after `test`, on ubuntu-latest only, builds
    sdist + wheel via `python -m build` and uploads them as a
    workflow artifact (named `dist`) so a maintainer can sanity-
    check the artifacts before tagging.
- Create `.github/workflows/publish.yml`:
  - Trigger: `push` of tags matching `v*`
  - `permissions: id-token: write` (required for OIDC)
  - Job `build-and-publish`: checkout, setup-python 3.11, install
    `build`, run `python -m build`, then upload to PyPI via
    `pypa/gh-action-pypi-publish@release/v1` with no `password:`
    field (OIDC) — TestPyPI step is **commented out** with a note
    explaining how to enable it for dry-runs (we don't want every
    real tag double-uploading).
- Add a CI status badge to `README.md` (top of file, right after the
  title): `[![CI](https://github.com/headepic/calcifer/actions/workflows/ci.yml/badge.svg)](https://github.com/headepic/calcifer/actions/workflows/ci.yml)`
- Add a `tests/test_ci_workflows.py` file with three tests:
  - `test_ci_workflow_exists_and_parses` — file exists, valid YAML,
    has a `jobs.test.strategy.matrix` with `python-version`
    containing `"3.11"`, `"3.12"`, `"3.13"` and `os` containing
    `ubuntu-latest` and `macos-latest`
  - `test_publish_workflow_exists_and_uses_oidc` — file exists,
    valid YAML, triggered on `tags: ['v*']`, has
    `permissions.id-token: write`, references
    `pypa/gh-action-pypi-publish` action
  - `test_readme_has_ci_badge` — `README.md` contains the badge
    URL `actions/workflows/ci.yml/badge.svg`

### 不做 (non-goals)

- Not adding a TestPyPI publish step that runs on every tag — left
  as a commented-out template. Maintainers can uncomment for a
  release-candidate dry run.
- Not adding lint/format jobs (ruff, mypy, etc.) — Calcifer doesn't
  currently configure those tools and adding them is its own
  feature.
- Not running E2E tests (`test_e2e_real.py`, `test_e2e_mcp_skill.py`,
  `test_tui_web.py`) — they require live LLM credentials that don't
  belong in CI for an open-source repo.
- Not adding Windows to the OS matrix — calcifer's path/process
  handling has not been audited for Windows; adding it would mean
  fixing all the resulting failures, which is out of scope.
- Not adding a `release-drafter` or auto-changelog action — humans
  hand-write the CHANGELOG (see `sdk-changelog-semver`).
- Not configuring the PyPI trusted publisher itself (that's a
  one-time setup on pypi.org by the project owner; cannot be done
  from the repo). The contract for `sdk-pypi-release-v03` will
  document the manual one-time setup steps.
- Not running `actionlint` from inside the workflow — the test file
  parses the YAML with PyYAML, which catches syntax errors and the
  specific shape we care about. Running actionlint would require
  installing it in CI itself.

## Design

### `.github/workflows/ci.yml` (sketch)

```yaml
name: CI

on:
  push:
    branches: [main, sdk-refactor]
  pull_request:

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
        os: [ubuntu-latest, macos-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -e . pytest pytest-asyncio PyYAML
      - run: pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
  build:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install build && python -m build
      - uses: actions/upload-artifact@v4
        with: { name: dist, path: dist/ }
```

### `.github/workflows/publish.yml` (sketch)

```yaml
name: Publish

on:
  push:
    tags: ["v*"]

jobs:
  build-and-publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install build && python -m build
      # To dry-run on TestPyPI first, uncomment and run BEFORE the
      # PyPI step. Requires a separate trusted publisher on TestPyPI.
      # - uses: pypa/gh-action-pypi-publish@release/v1
      #   with:
      #     repository-url: https://test.pypi.org/legacy/
      - uses: pypa/gh-action-pypi-publish@release/v1
```

### `tests/test_ci_workflows.py`

Standard PyYAML parse + dict drill-downs. PyYAML is already
installed in the local `.venv` (verified: `yaml.__version__ ==
6.0.3`) and is added explicitly to the CI workflow's install line
above so CI runs of the test will not ImportError. The test should
import `yaml` at module top — no `importorskip` needed because
PyYAML is now guaranteed in both environments.

### Reviewer comment hygiene

Verification cmd 3 substring-checks `'password:' not in
publish.yml` to enforce OIDC. To avoid a future false-positive, do
**not** mention the literal string `password:` anywhere in
`publish.yml`, including comments. Reference it as "API token" or
"the legacy password field" instead.

## Acceptance Criteria

- [ ] `.github/workflows/ci.yml` exists and is valid YAML
- [ ] `ci.yml` matrix has Python 3.11, 3.12, 3.13
- [ ] `ci.yml` matrix has ubuntu-latest and macos-latest
- [ ] `ci.yml` runs pytest with the same `--ignore` exclusions used locally
- [ ] `.github/workflows/publish.yml` exists and is valid YAML
- [ ] `publish.yml` triggers on tag `v*`
- [ ] `publish.yml` has `permissions.id-token: write` (OIDC)
- [ ] `publish.yml` references `pypa/gh-action-pypi-publish`
- [ ] `publish.yml` does NOT contain a `password:` field anywhere
      (would mean an API token is in use instead of OIDC)
- [ ] `README.md` has a CI badge linking to the workflow
- [ ] New test file `tests/test_ci_workflows.py` exists with all
      three tests passing
- [ ] All 475 existing mock tests still pass

## Verification Commands

```
.venv/bin/python -c "from pathlib import Path; assert Path('.github/workflows/ci.yml').exists() and Path('.github/workflows/publish.yml').exists(), 'workflow files missing'"
.venv/bin/python -c "import yaml; from pathlib import Path; ci=yaml.safe_load(Path('.github/workflows/ci.yml').read_text()); m=ci['jobs']['test']['strategy']['matrix']; assert set(['3.11','3.12','3.13']).issubset(set(m['python-version'])), m['python-version']; assert 'ubuntu-latest' in m['os'] and 'macos-latest' in m['os'], m['os']"
.venv/bin/python -c "import yaml; from pathlib import Path; raw=Path('.github/workflows/publish.yml').read_text(); pub=yaml.safe_load(raw); assert pub['jobs']['build-and-publish']['permissions']['id-token']=='write'; assert 'password:' not in raw, 'publish.yml must not contain a password field (OIDC required)'; assert 'pypa/gh-action-pypi-publish' in raw, 'publish.yml must use pypa/gh-action-pypi-publish'"
.venv/bin/python -c "from pathlib import Path; t=Path('README.md').read_text(); assert 'actions/workflows/ci.yml/badge.svg' in t, 'README missing CI badge'"
.venv/bin/python -m pytest tests/test_ci_workflows.py -q
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

Must match `features.json` verification array verbatim.

## Rollback Plan

`git revert` is trivial. The workflows live entirely under
`.github/`; deleting the directory removes all CI behavior. The
test file is independent of the rest of the suite. No runtime code
in `calcifer/` is touched. The README badge is one line.
