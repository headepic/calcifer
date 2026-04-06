# Feature Contract: sdk-public-api-audit

## Motivation

`calcifer/__init__.py` already has an `__all__` list (31 names). But:

1. There's no test that locks down the public API surface — anyone
   can add a new export tomorrow without realizing it's now part of
   the semver contract.
2. There's no document explaining which names are stable, what each
   does, and what the semver rules are for changing them.
3. Before publishing v0.3.0, this is the LAST chance to adjust the
   surface without semver penalty. After publish, every name in
   `__all__` becomes a commitment.

This feature locks the surface with a snapshot test and writes
`docs/public-api.md` documenting each name with a stability tier.

## Claude Code Reference

No direct analog. Claude Code is a TypeScript app, not a published
library. The pattern is standard Python SDK practice: explicit
`__all__` + a snapshot test + a documented stability policy.

## Scope

### 要做

- Audit current `__all__` (31 names). Decide stability tier for each.
  No removals this pass — just document and lock.
- Create `docs/public-api.md` with three sections:
  - **Stable** — semver guaranteed; breaking changes only in major versions
  - **Provisional** — exported but may change in any minor version
  - **Internal helpers** — visible in `dir(calcifer)` but not in `__all__`
- Add `test_public_api_surface` in `tests/test_packaging.py`:
  - Hardcodes `_EXPECTED_PUBLIC_API` frozenset
  - Asserts `set(calcifer.__all__) == _EXPECTED_PUBLIC_API`
  - On any change to `__all__`, the test fails with a clear diff message
- Add `test_public_api_importable`:
  - Loops over `calcifer.__all__`
  - Asserts `getattr(calcifer, name)` is non-None for each

### 不做 (non-goals)

- Not removing/renaming any existing exports. The audit may surface
  candidates but actual removals are a separate breaking change for
  v0.4.0 and need their own contracts.
- Not adding new public APIs.
- Not generating Sphinx-style API reference. That's `sdk-docs-structure`.
- Not adding a `_internal/` subpackage (sdk-core-layout).
- Not adding deprecation machinery.

## Design

### Audit decisions (current 31 names — verified via `import calcifer; sorted(calcifer.__all__)`)

The full sorted list of 31 names:

```
APIErrorType, Agent, AgentResult, CalciferConfig, ContextManager,
Coordinator, CoordinatorConfig, CostTracker, FunctionTool, HookConfig,
HookEvent, HookManager, LLMProvider, LLMProviderError, MCPServerConfig,
Message, MetricsManager, StreamEvent, Tool, ToolCall, ToolContext,
ToolResult, Usage, ValidationResult, assemble_tool_pool,
find_tool_by_name, get_all_builtin_tools, get_tools, load_settings,
run_tools, tool
```

**Stable (semver guaranteed) — 20 names:**
- Core: `Agent`, `AgentResult`, `CalciferConfig`, `MCPServerConfig`
- Tool: `Tool`, `FunctionTool`, `ToolContext`, `ToolResult`,
  `ValidationResult`, `tool`, `find_tool_by_name`
- Tool registry: `get_all_builtin_tools`, `get_tools`, `assemble_tool_pool`
- Messages: `Message`, `ToolCall`, `Usage`, `StreamEvent`, `APIErrorType`
- Errors: `LLMProviderError`
- Settings: `load_settings`

**Provisional (may change in minor versions) — 8 names:**
- `Coordinator`, `CoordinatorConfig` — multi-agent API still evolving
- `ContextManager` — likely to gain knobs
- `HookManager`, `HookConfig`, `HookEvent` — hook system not yet
  wired into orchestrator (see backlog)
- `LLMProvider` — exposed for advanced replacement, interface not pinned

**Lower priority but kept this version — 3 names:**
- `CostTracker`, `MetricsManager` — telemetry helpers
- `run_tools` — low-level orchestration helper

Total: 20 + 8 + 3 = 31, matches the actual set.

**Final EXPECTED_PUBLIC_NAMES** = the 31 names listed above verbatim.
This pass is freeze-and-document, no removals.

### `docs/public-api.md` structure

Single file. Three sections (Stable / Provisional / Internal helpers).
Each name gets a one-line description and (for Provisional) a note
explaining what may change.

### Tests in `tests/test_packaging.py`

Append THREE new tests at the end:

1. `test_public_api_surface` — hardcodes `_EXPECTED_PUBLIC_API` as a
   frozenset literal of all 31 names; asserts
   `set(calcifer.__all__) == _EXPECTED_PUBLIC_API` with a clear
   added/removed diff message on failure.

2. `test_public_api_importable` — loops over `calcifer.__all__` and
   asserts `getattr(calcifer, name)` is non-None for each.

3. `test_public_api_documented_in_md` — reads `docs/public-api.md`
   as text and asserts EVERY name in `calcifer.__all__` appears as
   a substring (case-sensitive) in the file. This closes the
   reviewer's footgun: a doc with just the three section headers
   passes the section grep but fails this content check.

## Acceptance Criteria

- [ ] `docs/public-api.md` exists with three sections (Stable, Provisional, Internal)
- [ ] Every name in `calcifer.__all__` (all 31) appears as a substring in `docs/public-api.md`
- [ ] New test `test_public_api_surface` exists and passes
- [ ] New test `test_public_api_importable` exists and passes
- [ ] New test `test_public_api_documented_in_md` exists and passes (closes the doc-stub footgun)
- [ ] `_EXPECTED_PUBLIC_API` constant lists exactly the current 31 names
- [ ] No existing exports are removed or renamed
- [ ] `test_public_api_surface` fails with a useful diff message if `__all__` drifts
- [ ] All 470 existing mock tests still pass

## Verification Commands

```
.venv/bin/python -c "from pathlib import Path; p = Path('docs/public-api.md'); assert p.exists(), 'docs/public-api.md missing'; t = p.read_text(); assert 'Stable' in t and 'Provisional' in t and 'Internal' in t, 'public-api.md missing required sections'"
.venv/bin/python -c "import calcifer; assert hasattr(calcifer, '__all__') and len(calcifer.__all__) == 31, f'__all__ should have exactly 31 names, got {len(calcifer.__all__)}'"
.venv/bin/python -m pytest tests/test_packaging.py -q -k 'public_api_surface or public_api_importable or public_api_documented_in_md'
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

Must match `features.json` verification array verbatim.

## Rollback Plan

`git revert` is trivial. The test is additive; if `__all__` is
changed in a future commit, the test fails loudly and forces an
intentional update. No runtime behavior changes.
