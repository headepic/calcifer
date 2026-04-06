# Changelog

All notable changes to **calcifer** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
See [`docs/semver.md`](docs/semver.md) for Calcifer's interpretation of semver.

## [Unreleased]

## [0.3.0] - 2026-04-06

First SDK-ready release. Calcifer is now a publishable Python library
with locked public API surface, full PyPI metadata, and PEP 561 type
support.

### Added
- PEP 561 `py.typed` marker so downstream `mypy`/`pyright` see calcifer's
  type hints (sdk-py-typed-marker).
- `[tool.hatch.build.targets.wheel]` config so the marker ships in the wheel.
- Full PyPI metadata: authors, license, classifiers, keywords, project urls
  (sdk-pyproject-metadata).
- `LICENSE` file (MIT) at repo root.
- `Agent.run_sync()` synchronous wrapper for sync scripts and REPLs
  (sdk-agent-run-sync).
- `docs/public-api.md` documenting all 31 public names with stability tiers
  (sdk-public-api-audit).
- `tests/test_packaging.py` snapshot tests that lock the public API
  surface (`test_public_api_surface`, `test_public_api_importable`,
  `test_public_api_documented_in_md`).
- `docs/semver.md` with the canonical semver policy, deprecation rules,
  and the 3-step procedure for changing the public API surface
  (sdk-changelog-semver).
- MCP auth refresh callback (mcp-auth-refresh) — sessions can now recover
  from expired auth tokens via a user-provided async callback.
- Skill `when_to_use` frontmatter field (when-to-use-skill-field) — better
  guidance for the LLM on when to invoke each skill.
- Harness contract review mechanism — fresh-context subagent reviews
  every plan-phase contract before implementation can proceed.
- Append-only review history at `harness/reviews/<id>.jsonl` for
  reviewer calibration.

### Changed
- **BREAKING**: `CalciferConfig.base_url` default changed from
  `http://127.0.0.1:8317/v1` (a private local LLM gateway from early
  development) to `None`. The Agent now resolves it via
  `OPENAI_BASE_URL` env var, falling back to `https://api.openai.com/v1`.
  Users who previously relied on the localhost default must now set
  the env var or pass `base_url=` explicitly. (sdk-config-env-defaults)
- Version bumped from 0.2.0 → 0.3.0.dev0 → 0.3.0.
- `LLMProvider` constructor default `base_url` changed from localhost
  to `https://api.openai.com/v1` (separate from the Agent-layer
  resolver — this is a safety net for users who instantiate the
  transport class directly).

### Fixed
- (none in this release — first SDK-ready cut)

## [0.2.0] - 2026-03-15

Pre-SDK release. See git history for details.
