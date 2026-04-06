# Feature Contract: when-to-use-skill-field

## Motivation

Calcifer's skill definitions only have `description` — a short phrase shown
to the LLM in the skill list. Claude Code skills also have `when_to_use`,
which explains **when** each skill is relevant (trigger conditions, keywords,
typical user phrasings).

Without `when_to_use`, the model often picks the wrong skill or skips skills
that would help. Adding it is low-cost and improves skill selection quality.

## Claude Code Reference

- `src/skills/loadSkillsDir.ts:185-265` — `parseSkillFrontmatterFields()`
  - Parses `when_to_use` from frontmatter (camelCase or snake_case accepted)
  - Stored in Command object and surfaced in skill list

## Scope

### 要做

- Add `when_to_use: str = ""` field to `SkillDefinition` dataclass
- `load_skill_file` parses `when-to-use` (or `when_to_use`) from frontmatter
- When present, include it in the token budget entry
- Format in the skill list: `## <name>\n<description>\n<when_to_use>` (both inline)
- Update `apply_token_budget` to include when_to_use in char accounting
- New test verifies parsing and inclusion in the formatted list

### 不做 (non-goals)

- No separate budget for when_to_use (shared with description)
- No retroactive when_to_use generation for existing skills
- No template validation (any string is fine)
- No UI changes in TUI/web — only the LLM-facing list

## Design

Changes to `calcifer/skills/loader.py`:

1. Add `when_to_use: str = ""` to `SkillDefinition`
2. In `load_skill_file`, parse from frontmatter:
   ```python
   when_to_use = frontmatter.get("when-to-use") or frontmatter.get("when_to_use") or ""
   ```
3. Add to `known_keys` set
4. Pass to `SkillDefinition(...)` constructor

Changes to `apply_token_budget`:
- The entry tuple stays `(name, desc)` for backward compat
- When description has room, append when_to_use:
  ```python
  desc = skill.description[:SKILL_DESCRIPTION_MAX_CHARS]
  if skill.when_to_use:
      desc = f"{desc}\n(use when: {skill.when_to_use[:SKILL_DESCRIPTION_MAX_CHARS]})"
  entry_chars = len(skill.name) + len(desc) + 10
  ```

Tests:
- Load a fixture skill with `when-to-use` — verify field populated
- Load a fixture skill without — verify field empty
- `apply_token_budget` includes when_to_use in output when present

## Acceptance Criteria

- [ ] `SkillDefinition.when_to_use` field added (str, default "")
- [ ] `load_skill_file` parses both `when-to-use` and `when_to_use` from frontmatter
- [ ] `apply_token_budget` includes when_to_use in the entry text when set
- [ ] Token char count includes when_to_use length
- [ ] Skills without when_to_use still load and budget correctly
- [ ] New test `test_skill_when_to_use_parsing` — loads a fixture with the field set
- [ ] New test `test_skill_budget_includes_when_to_use` — verifies output format
- [ ] Existing skill tests still pass
- [ ] All 429 mock tests still pass

## Verification Commands

```
.venv/bin/python -c "from calcifer.skills.loader import SkillDefinition; import dataclasses; assert 'when_to_use' in {f.name for f in dataclasses.fields(SkillDefinition)}"
.venv/bin/python -m pytest tests/ -q -k 'when_to_use'
.venv/bin/python -m pytest tests/test_skill.py tests/test_skill_full.py -q
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

Must match `features.json` verification exactly.

## Rollback Plan

If the budget format change breaks downstream consumers (e.g., TUI that
parses the skill list text), keep the format unchanged and instead expose
when_to_use as a separate field returned by a new helper function.

Trivially revert: `git revert <commit>`.
