# Feature Contract: harness-contract-review

## Motivation

The harness currently has no gate between "contract written" and
"implementation started". The author can write any contract, commit it,
and start coding. This is the exact failure mode that caused round-1
reviewer to catch a live instance:

> The original `pre-post-tool-hooks` contract proposed adding
> `HookEvent.PRE_TOOL_USE`, which **already existed** in
> `calcifer/services/hooks.py:27`. The author wrote the contract without
> reading the source. A contract review step would have caught this in
> minutes. Instead it was caught hours later after implementation was
> half-designed.

Contracts are the **cheapest** and **highest-value** place to catch
mistakes, because:

1. Wrong contract → wrong implementation (wasted effort)
2. Contracts are short (easier to review than code)
3. Contract errors are harder to roll back than code errors — if you
   realize the whole scope is wrong after implementing, you've lost
   everything

Anthropic's "Harness Design for Long-Running Apps" article makes this
the core mechanic:

> "Before each sprint, the generator and evaluator negotiated a sprint
> contract: agreeing on what 'done' looked like for that chunk of work
> before any code was written. The generator proposed what it would
> build and how success would be verified, and the evaluator reviewed
> that proposal to make sure the generator was building the right thing.
> The two iterated until they agreed."

We replicate this with one addition: since harness.py can't spawn an
LLM itself (no API key at runtime, and no external dependencies
beyond httpx/pydantic/pyyaml), the review is driven by the **caller**
(me — Claude — or a human). harness.py does three things: generate
the review packet, record the verdict, and gate verify/complete on
an approved verdict.

## Claude Code Reference

**Primary reference**: the Anthropic article
https://www.anthropic.com/engineering/harness-design-long-running-apps
— specifically the sprint contract negotiation pattern.

**No direct Claude Code source analog.** Claude Code's harness runs in
TypeScript and spawns sub-agents via its own agent SDK. Our approach
inverts control: the caller (Claude Opus, via the Agent tool) reads
the packet, evaluates, and writes back the verdict using
`harness.py review-record`. This decouples the harness CLI from any
specific LLM or agent framework.

## Scope

### 要做

- New subcommand `harness.py review <id>`:
  - Loads contract file + features.json entry for the feature
  - Runs machine sanity checks (fields present, commands parseable,
    referenced files exist, no TODO placeholders)
  - Prints a structured review packet to stdout
  - Exits 0 (the packet is informational — the verdict comes later)

- New subcommand `harness.py review-record <id> --status <s> --notes TEXT`:
  - `<s>` in {`approved`, `changes_requested`, `blocking`}
  - Updates features.json with: `review_status`, `review_notes`,
    `reviewed_at` (ISO 8601 UTC timestamp), `reviewed_contract_sha`
    (sha256 of contract file bytes, first 16 hex chars)
  - Rejects approved status if contract file is missing OR contains
    literal `TODO:` marker strings
  - Rejects any non-approved status with empty notes (you must justify
    rejection)

- Gate in `cmd_verify` (and therefore `cmd_complete`):
  - Load feature from features.json
  - If `review_status != "approved"`: print error + suggested command
    (`harness.py review <id>` or `review-record`), exit 1
  - If `review_status == "approved"` but
    `reviewed_contract_sha != sha256(contract_file)`: print
    "contract has been edited since review — re-review required",
    exit 1
  - Escape hatch: `--skip-review REASON` on verify AND complete,
    requires non-empty reason, prints warning to stderr

- New `Feature` dataclass fields (all default `""`):
  - `review_status: str`
  - `review_notes: str`
  - `reviewed_at: str`
  - `reviewed_contract_sha: str`

- `cmd_reset` clears these fields alongside `verified_sha`/`verified_tree`

- Machine sanity checks built into the review packet as reviewer
  context (NOT as a separate gate):
  - All file paths extracted from the "Claude Code Reference" and
    "Design" sections exist (with absent/present marker per path)
  - Every verification command in features.json passes
    `validate_and_parse_verify_command`
  - Required contract sections are non-empty
  - No literal `TODO:` placeholder strings in the contract

- Review packet format (plain text, human-readable):
  ```
  ===== REVIEW PACKET: <feature-id> =====

  [METADATA]
  title:    ...
  category: ...
  priority: ...
  status:   pending

  [CONTRACT FILE]
  path: harness/contracts/<id>.md
  sha:  <sha16>

  [MACHINE SANITY]
  OK   Contract file exists (<N> bytes)
  OK   All required sections present
  WARN TODO marker found on line 47
  OK   All 5 verification commands validate
  OK   2/3 referenced files exist
  FAIL Referenced file calcifer/foo/bar.py does NOT exist

  [CONTRACT CONTENT]
  <full contract text>

  [FEATURES.JSON ENTRY]
  <pretty JSON>

  [REVIEWER CHECKLIST]
  1. Motivation: is the problem real? is urgency justified?
  2. Reference: does it accurately describe Claude Code source OR
     honestly say "no analog"? Check a few line numbers exist.
  3. Scope: are non-goals explicit and reasonable? Is it one feature
     or secretly three?
  4. Design: does it match current Calcifer state? Does it propose
     adding symbols that already exist? (Read the referenced Calcifer
     files to check.)
  5. Acceptance Criteria: is every item yes/no verifiable? >= 3 items?
  6. Verification Commands: will they FAIL before implementation and
     PASS after? (Mentally simulate: if I ran them now, which fail?)
     Do they use import/attribute checks or grep-on-source?
  7. Rollback Plan: is it concrete?
  8. Will implementing this take more than 1 session? If yes, should
     be broken up.
  9. Are there dependencies on other not-yet-done features?
  10. Any footguns in the verification commands? (would they accept
      a no-op implementation?)

  [HOW TO RECORD YOUR VERDICT]
  python harness/harness.py review-record <id> \
    --status {approved|changes_requested|blocking} \
    --notes "specific feedback, cite line numbers"

  changes_requested: issues that must be fixed; author edits contract
  and re-runs review (SHA changes invalidate prior approval).
  blocking: fundamental problem (wrong scope, already done, infeasible).
  approved: ready to implement.
  ```

### 不做 (non-goals)

- harness.py does NOT itself call an LLM. No httpx calls to OpenAI or
  Anthropic. The review agent is invoked by the caller via their own
  tool (Agent subagent for Claude, or a human reading the packet).
  Rationale: keeps harness dependency-free, works without credentials.
- No separate "lint" subcommand. Machine checks are embedded in the
  review packet as context. The article explicitly has no lint step —
  the evaluator's judgment IS the review.
- No auto-prompt for reviewer calibration. Article acknowledges
  "out of the box, Claude is a poor QA agent" and recommends manual
  iteration. We start with the static checklist above and iterate
  after seeing real reviews.
- No multi-reviewer consensus. Single reviewer, single verdict.
- No review history (just the latest verdict). If re-reviewed, the new
  result overwrites.

## Design

### Changes to `calcifer-sdk/harness/harness.py`

1. **New imports**: `hashlib`, `datetime` (already imported in cmd_log).

2. **Feature dataclass** (around line 55): add four fields with defaults.

3. **from_dict / to_dict**: include the new fields with `.get(..., "")`.

4. **New helper functions** near `_progress_edits_status`:
   - `_contract_sha(feature_id: str) -> str` — sha256 of contract file
     bytes (first 16 hex chars), or `""` if file missing.
   - `_extract_referenced_paths(contract_text: str) -> list[str]` —
     simple regex to find paths that look like `calcifer/foo.py` or
     `claude-code-source/src/...` inside the contract text.
   - `_machine_sanity(feature: Feature) -> list[tuple[str, str]]` —
     returns list of `(status, message)` where status is one of
     `"OK"`, `"WARN"`, `"FAIL"`.

5. **New `cmd_review(args)`**: loads feature, generates packet, prints.

6. **New `cmd_review_record(args)`**: validates status, enforces
   non-empty notes, writes to features.json (using atomic save).

7. **Update `cmd_verify`**: add gate check BEFORE running verification
   commands. If `args.skip_review` is set (a non-empty string), skip
   the gate and print warning to stderr. Otherwise require
   `review_status == "approved"` and contract SHA match.

8. **Update `cmd_complete`**: add matching gate check and `--skip-review`
   argparse option.

9. **Update `cmd_reset`**: clear all four new fields.

10. **Update main() argparse**:
    - Add `review` subparser (takes `feature_id`)
    - Add `review-record` subparser (takes `feature_id`,
      `--status` required with choices, `--notes` required)
    - Add `--skip-review REASON` to `verify` and `complete`

### Changes to `calcifer-sdk/harness/features.json`

Backfill the four new fields on all 24 existing features (all default
`""`). Only harness-contract-review itself needs its review_status set —
that's a bootstrapping exception documented in the contract below.

### Bootstrapping exception (self-reference)

This feature is the FIRST thing to go through the new mechanism, but
the mechanism doesn't exist yet. Options:

1. **Self-approve**: implement the feature, then manually set
   `review_status = "approved"` for `harness-contract-review` in
   features.json (one-time plan edit), then verify/complete as normal.
2. **Grandfather clause**: add a carve-out — if the feature id is
   `harness-contract-review` and it's the FIRST review-gated feature,
   skip the review gate once.
3. **Retroactive review**: implement it, then run the new
   `harness.py review harness-contract-review` on itself, record verdict
   manually, then verify.

Pick option 3 — it's the most rigorous and dogfood the new mechanism on
its own contract. Implementation order:

1. Write implementation (this session)
2. Run tests
3. Run `harness.py review harness-contract-review`
4. Review the packet (self-review or spawn subagent)
5. Run `harness.py review-record harness-contract-review --status approved --notes "..."`
6. Now verify + complete will work

### Calibration philosophy (per article)

The reviewer will start lenient and miss things. That's expected.
Each time a bad contract slips through review but is caught later
(in implementation or post-hoc review), we update the reviewer
checklist in `cmd_review` with the new failure mode. Over time the
static checklist converges toward "here are all the failure modes
we've seen". This is explicit in the article: "it took several
rounds of this development loop before the evaluator was grading
in a way that I found reasonable."

## Acceptance Criteria

- [ ] `harness.py review <id>` subcommand exists and prints a packet
- [ ] `harness.py review-record <id> --status <s> --notes TEXT` exists
- [ ] `review-record` rejects unknown status values (argparse choices)
- [ ] `review-record` rejects empty notes for non-approved status
- [ ] `review-record` rejects `approved` if contract file has `TODO:`
- [ ] `Feature` dataclass has `review_status`, `review_notes`, `reviewed_at`, `reviewed_contract_sha` fields (all str, default "")
- [ ] `cmd_verify` refuses to run when `review_status != "approved"`
- [ ] `cmd_verify` refuses when `reviewed_contract_sha != current contract sha`
- [ ] `--skip-review REASON` flag on verify and complete accepts non-empty reason, warns to stderr
- [ ] `cmd_reset` clears all four review fields
- [ ] Review packet includes machine sanity section (at least: contract exists, sections non-empty, verification commands validate, no TODO markers)
- [ ] Review packet includes the 10-point reviewer checklist verbatim
- [ ] New test `test_review_record_approves_and_gates` — records approval, verify now allowed
- [ ] New test `test_review_record_rejects_without_notes` — non-approved status with empty notes is rejected
- [ ] New test `test_review_record_detects_contract_edit` — approved, then edit contract, verify refuses with SHA mismatch error
- [ ] New test `test_verify_refuses_without_approved_review` — clean feature, no review, verify fails with clear message
- [ ] New test `test_skip_review_with_reason` — --skip-review "reason" bypasses the gate
- [ ] New test `test_reset_clears_review_fields`
- [ ] All 434 existing mock tests still pass (no regressions in Calcifer core)
- [ ] progress.md entry documents the calibration philosophy quote from the article

## Verification Commands

```
.venv/bin/python -c "import subprocess; r = subprocess.run(['.venv/bin/python', 'harness/harness.py', 'review', '--help'], capture_output=True, text=True); assert r.returncode == 0 and 'review' in r.stdout, r.stdout + r.stderr"
.venv/bin/python -c "import subprocess; r = subprocess.run(['.venv/bin/python', 'harness/harness.py', 'review-record', '--help'], capture_output=True, text=True); assert r.returncode == 0 and '--status' in r.stdout, r.stdout + r.stderr"
.venv/bin/python -c "import sys; sys.path.insert(0, 'harness'); import harness as h; assert 'review_status' in {f.name for f in __import__('dataclasses').fields(h.Feature)}, 'Feature missing review_status field'"
.venv/bin/python -m pytest tests/ -q -k 'review_record_approves_and_gates or review_record_rejects_without_notes or review_record_detects_contract_edit or verify_refuses_without_approved_review or skip_review_with_reason or reset_clears_review_fields'
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

Must match `features.json` verification exactly.

## Rollback Plan

If the gate turns out too painful (e.g., slows iteration unacceptably),
the lowest-effort rollback is to **keep the subcommands but make the
gate advisory**: verify prints a warning when review_status is not
approved but proceeds. This preserves the review machinery while
removing friction.

If the mechanism fundamentally doesn't work (e.g., the packet format
is useless, or recording verdicts has a bug that corrupts
features.json), `git revert` the implementation commit and keep the
contract as record of what was tried.

If reviewer calibration proves impossibly hard (every review returns
`approved` regardless), the escape hatch is to update the packet's
checklist with concrete counter-examples and iterate. The feature is
not rolled back for calibration issues — only for structural bugs.
