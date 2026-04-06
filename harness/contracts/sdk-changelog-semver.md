# Feature Contract: sdk-changelog-semver

## Motivation

There is no `CHANGELOG.md` at the repo root. Publishing a v0.3.0 to
PyPI without one is amateurish — users have no way to learn what
changed between 0.2.x and 0.3.0, what's new, what broke, what was
deprecated. The pyproject.toml `[project.urls]` already advertises
`Changelog = ".../CHANGELOG.md"` (added in `sdk-pyproject-metadata`),
so the link is dangling.

`docs/public-api.md` (from `sdk-public-api-audit`) documents the
semver tiers but not the policy itself: what counts as a breaking
change, when deprecation warnings apply, what versioning scheme
the project uses.

This feature adds:
1. `CHANGELOG.md` in keep-a-changelog format with a real `[0.3.0]`
   section listing every shipped change since the rename to
   "Calcifer SDK"
2. `docs/semver.md` with the complete semver policy

## Claude Code Reference

No direct analog. Both files follow generic OSS conventions:
- [keep a changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)
- [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html)

## Scope

### 要做

- Create `CHANGELOG.md` at repo root with:
  - Header explaining the format and version source
  - `[0.3.0] - 2026-04-06` section enumerating every change since v0.2.0
  - Categories: Added, Changed, Deprecated, Removed, Fixed, Security
- Create `docs/semver.md` with:
  - Calcifer's interpretation of semver (what triggers major/minor/patch)
  - The relationship to `docs/public-api.md` stability tiers
  - Deprecation policy (one minor version warning minimum)
  - The 3-step procedure for changing the public API surface
- Add 2 new tests in `tests/test_packaging.py`:
  - `test_changelog_exists_and_has_v030_entry` — file exists,
    contains `## [0.3.0]` heading, and the v0.3.0 section has at
    least 5 bullet (`- `) entries spanning at least 2 distinct
    `### ` category headings (e.g. Added + Changed)
  - `test_semver_policy_doc_exists` — `docs/semver.md` exists and
    references the public-api.md document
- Trim `docs/public-api.md`'s existing "## Semver policy" and
  "## How to propose changes" sections down to one-line pointers
  at `docs/semver.md` (single source of truth — `docs/semver.md`
  is now canonical, public-api.md just links to it)

### 不做 (non-goals)

- Not generating CHANGELOG entries automatically from git log. The
  v0.3.0 entry is hand-curated for accuracy.
- Not adopting Conventional Commits. Calcifer's commit history is
  free-form and that's fine.
- Not setting up `release-drafter` or any other GitHub Action.
  That's `sdk-github-actions-ci`'s scope.
- Not back-filling history before v0.2.0.

## Design

### `CHANGELOG.md` content (per keep-a-changelog 1.1.0)

```markdown
# Changelog

All notable changes to **calcifer** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
See `docs/semver.md` for Calcifer's interpretation of semver.

## [Unreleased]

## [0.3.0] - 2026-04-06

First SDK-ready release. Calcifer is now a publishable Python library
with locked public API surface, full PyPI metadata, and PEP 561 type
support.

### Added
- PEP 561 `py.typed` marker so downstream `mypy`/`pyright` see calcifer's
  type hints (sdk-py-typed-marker)
- `[tool.hatch.build.targets.wheel]` config so the marker ships in the wheel
- Full PyPI metadata: authors, license, classifiers, keywords, project urls
  (sdk-pyproject-metadata)
- `LICENSE` file (MIT) at repo root
- `Agent.run_sync()` synchronous wrapper for sync scripts and REPLs
  (sdk-agent-run-sync)
- `docs/public-api.md` documenting all 31 public names with stability tiers
  (sdk-public-api-audit)
- `tests/test_packaging.py` snapshot tests that lock the public API
  surface (test_public_api_surface, test_public_api_importable,
  test_public_api_documented_in_md)
- MCP auth refresh callback (mcp-auth-refresh) — sessions can now recover
  from expired auth tokens via a user-provided async callback
- Skill `when_to_use` frontmatter field (when-to-use-skill-field) — better
  guidance for the LLM on when to invoke each skill
- Harness contract review mechanism — fresh-context subagent reviews
  every plan-phase contract before implementation can proceed
- Append-only review history at `harness/reviews/<id>.jsonl` for
  reviewer calibration

### Changed
- **BREAKING**: `CalciferConfig.base_url` default changed from
  `http://127.0.0.1:8317/v1` (a private local LLM gateway from early
  development) to `None`. The Agent now resolves it via
  `OPENAI_BASE_URL` env var → `https://api.openai.com/v1` fallback.
  Users who previously relied on the localhost default must now set
  the env var or pass `base_url=` explicitly. (sdk-config-env-defaults)
- Version bumped from 0.2.0 → 0.3.0.dev0 → 0.3.0
- `LLMProvider` constructor default `base_url` changed from localhost
  to `https://api.openai.com/v1` (separate from the Agent-layer
  resolver — this is a safety net for users who instantiate the
  transport class directly)

### Fixed
- (none in this release — first SDK-ready cut)

## [0.2.0] - 2026-03-15

Pre-SDK release. See git history for details.
```

### `docs/semver.md` content

```markdown
# Calcifer Semver Policy

Calcifer follows Semantic Versioning 2.0.0 with the following
project-specific interpretations.

## What is the public API?

The public API is exactly the set of names listed in
`docs/public-api.md` AND in `calcifer.__all__`. The
`tests/test_packaging.py::test_public_api_surface` snapshot test
ensures these two stay in sync.

Anything else — submodules, helpers, internal symbols — has NO
stability guarantee. Use at your own risk.

## What triggers each version bump

### Major (X.0.0)
- Removing or renaming any name in `docs/public-api.md`
- Changing the type signature of any **Stable** name in a way that
  breaks existing call sites
- Changing the runtime behavior of a Stable name in a way that
  existing tests would fail

### Minor (0.X.0)
- Adding new public API names
- Changing **Provisional** names (with a CHANGELOG entry)
- Deprecating Stable names (warning persists for at least one minor
  version before removal in the next major)
- New features that are additive
- Performance improvements that don't change observable behavior

### Patch (0.0.X)
- Bug fixes that don't change documented behavior
- Documentation-only changes
- Internal refactoring with no public API impact
- Test additions

## Deprecation policy

Before removing a Stable name in a major release:
1. Mark it deprecated in at least one preceding minor release
2. The deprecation must emit a `DeprecationWarning` at runtime
3. The CHANGELOG must list the deprecation under "Deprecated"
4. The replacement (if any) must be documented in the same release

## Procedure for changing the public API

To add or remove a name from `__all__`, edit THREE files in the
same commit:
1. `calcifer/__init__.py` — the `__all__` list
2. `docs/public-api.md` — the documentation
3. `tests/test_packaging.py` — the `_EXPECTED_PUBLIC_API` constant

The snapshot test will fail unless all three are in sync. This is
intentional — three coordinated edits force the change to be
deliberate and reviewable.
```

## Acceptance Criteria

- [ ] `CHANGELOG.md` exists at repo root
- [ ] CHANGELOG follows keep-a-changelog format (header references the spec)
- [ ] CHANGELOG has a `## [0.3.0]` section with date
- [ ] `[0.3.0]` section lists at least 5 entries across at least 2 categories (Added/Changed/etc.)
- [ ] `docs/public-api.md`'s old semver section is reduced to a one-line pointer at `docs/semver.md` (no duplicate policy text)
- [ ] CHANGELOG flags the breaking change to `base_url` default explicitly
- [ ] `docs/semver.md` exists
- [ ] `docs/semver.md` mentions all three of: major triggers, minor triggers, patch triggers
- [ ] `docs/semver.md` references `docs/public-api.md` and the snapshot test
- [ ] New test `test_changelog_exists_and_has_v030_entry` passes
- [ ] New test `test_semver_policy_doc_exists` passes
- [ ] All 473 existing mock tests still pass

## Verification Commands

```
.venv/bin/python -c "from pathlib import Path; p = Path('CHANGELOG.md'); assert p.exists(), 'CHANGELOG.md missing'; t = p.read_text(); assert '## [0.3.0]' in t, 'CHANGELOG missing v0.3.0 section'; assert 'keepachangelog' in t.lower() or 'keep a changelog' in t.lower(), 'CHANGELOG header should reference keep-a-changelog'"
.venv/bin/python -c "from pathlib import Path; p = Path('docs/semver.md'); assert p.exists(), 'docs/semver.md missing'; t = p.read_text(); assert 'public-api.md' in t, 'semver.md should reference public-api.md'; assert 'Major' in t and 'Minor' in t and 'Patch' in t, 'semver.md missing version-bump triggers'"
.venv/bin/python -m pytest tests/test_packaging.py -q -k 'changelog_exists_and_has_v030_entry or semver_policy_doc_exists'
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

Must match `features.json` verification array verbatim.

## Rollback Plan

`git revert` is trivial. Both files are pure documentation; no
runtime code changes. The added tests are independent of any
existing tests.
