# Feature Contract: sdk-pyproject-metadata

## Motivation

`pyproject.toml` currently has only `name`, `version`, `description`,
`requires-python`, and `dependencies`. PyPI uses additional metadata
(authors, license, classifiers, urls, README) to render the project
page, populate search filters, and let `pip` show useful info via
`pip show calcifer`. Without this metadata:

1. `python -m build` may emit warnings about missing fields
2. The PyPI project page is bare and unprofessional
3. Search by classifier (Python version, license, topic) doesn't surface calcifer
4. There's no LICENSE file at the repo root, which violates standard OSS hygiene
5. The version is still `0.2.0` from before the SDK refactor work began

This feature is a prerequisite for `sdk-pypi-release-v03` — without
proper metadata the publish step will either fail validation or ship
an embarrassing-looking package.

## Claude Code Reference

No direct analog. Claude Code is not on PyPI. The conventions followed
here are PEP 621 (project metadata in pyproject.toml) and the standard
PyPA classifier list at https://pypi.org/classifiers/.

## Scope

### 要做

- Add to `[project]` table in `pyproject.toml`:
  - `authors = [{name = "...", email = "..."}]`
  - `license = {text = "MIT"}` (or `license = "MIT"` if hatchling supports SPDX)
  - `readme = "README.md"`
  - `keywords = ["agent", "llm", "openai", "claude", "agent-runner", "tool-calling", "mcp"]`
  - `classifiers = [...]` covering: Development Status, Intended Audience, License, Operating System, Programming Language (3.11/3.12/3.13), Topic
- Add `[project.urls]` table:
  - `Homepage = "https://github.com/headepic/calcifer"`
  - `Repository = "https://github.com/headepic/calcifer"`
  - `Issues = "https://github.com/headepic/calcifer/issues"`
  - `Changelog = "https://github.com/headepic/calcifer/blob/main/CHANGELOG.md"`
- Bump `version` to `"0.3.0.dev0"` (PEP 440 pre-release marker)
- Create `LICENSE` file at repo root with MIT license text
- Add 3 new tests in `tests/test_packaging.py`:
  - `test_pyproject_has_required_metadata` — parse with tomllib, assert each required field is present and non-empty
  - `test_pyproject_classifiers_cover_python_versions` — Python 3.11/3.12/3.13 listed
  - `test_license_file_exists` — `LICENSE` file at repo root exists and is non-empty

### 不做 (non-goals)

- Not running `python -m build` as part of verify (the `build` package
  may not be installed in the harness env). The acceptance criterion is
  metadata presence + license file existence; actual wheel building is
  validated as a separate step in `sdk-github-actions-ci`.
- Not creating `CHANGELOG.md` here — that is `sdk-changelog-semver`'s job.
  Just adding the URL placeholder.
- Not adding maintainer info beyond a single author entry. The author
  field uses placeholder values that the user can update.
- Not adding `dynamic = [...]` for any field; everything is static.
- Not removing the `description` field redundancy (currently both
  `description` and `[project] description` would be the same — keep as is).

## Design

### `pyproject.toml` additions

Insert these fields into the existing `[project]` table:

```toml
[project]
name = "calcifer"
version = "0.3.0.dev0"  # ← bumped from 0.2.0
description = "..."
readme = "README.md"
license = {text = "MIT"}
authors = [{name = "headepic", email = "noreply@example.com"}]
requires-python = ">=3.11"
keywords = ["agent", "llm", "openai", "claude", "agent-runner", "tool-calling", "mcp"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Software Development :: Libraries :: Python Modules",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
    "Typing :: Typed",
]
dependencies = [...]
```

And new section:

```toml
[project.urls]
Homepage = "https://github.com/headepic/calcifer"
Repository = "https://github.com/headepic/calcifer"
Issues = "https://github.com/headepic/calcifer/issues"
Changelog = "https://github.com/headepic/calcifer/blob/main/CHANGELOG.md"
```

### `LICENSE` file

Standard MIT license text at repo root (`LICENSE`, no extension).
Use the canonical SPDX MIT template with copyright line
"Copyright (c) 2026 headepic".

### Test additions in `tests/test_packaging.py`

The file already exists from `sdk-py-typed-marker`. Append three new
tests that parse `pyproject.toml` via `tomllib` and assert:

1. `[project]` has non-empty `authors`, `license`, `readme`, `keywords`,
   `classifiers`, AND `[project.urls]` exists with `Homepage`,
   `Repository`, `Issues`, `Changelog` keys.
2. The classifiers list includes
   `"Programming Language :: Python :: 3.11"`,
   `"Programming Language :: Python :: 3.12"`, and
   `"Programming Language :: Python :: 3.13"`.
3. `LICENSE` file exists at the repo root and is at least 500 bytes.

## Acceptance Criteria

- [ ] `pyproject.toml` `[project]` table has `authors`, `license`, `readme`, `keywords`, `classifiers` fields, all non-empty
- [ ] `[project.urls]` section exists with `Homepage`, `Repository`, `Issues`, `Changelog` keys
- [ ] Version is bumped from `0.2.0` to `0.3.0.dev0`
- [ ] Classifiers list includes Python 3.11, 3.12, 3.13
- [ ] Classifiers list includes `"License :: OSI Approved :: MIT License"`
- [ ] Classifiers list includes `"Typing :: Typed"` (paired with the py.typed marker shipped earlier)
- [ ] `LICENSE` file exists at repo root, contains MIT license text
- [ ] New test `test_pyproject_has_required_metadata` exists and passes
- [ ] New test `test_pyproject_classifiers_cover_python_versions` exists and passes
- [ ] New test `test_license_file_exists` exists and passes
- [ ] All 465 existing mock tests still pass

## Verification Commands

```
.venv/bin/python -c "import tomllib; from pathlib import Path; d = tomllib.loads(Path('pyproject.toml').read_text()); p = d['project']; assert p['version'] == '0.3.0.dev0', f'version is {p[\"version\"]}'; assert p['license'], 'license missing'; assert p.get('authors'), 'authors missing'; assert p.get('keywords'), 'keywords missing'; assert p.get('classifiers'), 'classifiers missing'; assert p.get('readme'), 'readme missing'; assert d['project'].get('urls'), 'project.urls missing'"
.venv/bin/python -c "from pathlib import Path; p = Path('LICENSE'); assert p.exists(), 'LICENSE file missing'; assert p.stat().st_size >= 500, f'LICENSE is too small: {p.stat().st_size} bytes'"
.venv/bin/python -m pytest tests/test_packaging.py -q -k 'pyproject_has_required_metadata or pyproject_classifiers_cover_python_versions or license_file_exists'
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

Must match `features.json` verification array verbatim.

## Rollback Plan

`git revert` is trivial — pyproject.toml additions and the LICENSE file
are independent of runtime code. No schema changes, no behavior changes.
The only risk is breaking the wheel build if the license declaration
form is wrong for hatchling; if that happens, fall back to
`license = {text = "MIT"}` (the table form) instead of the SPDX string
form.
