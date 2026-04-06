# Calcifer Progress Log

Append-only log of what each session accomplished. Never edit old entries.
One entry per session. Newest at the top.

---

## 2026-04-06 — when-to-use-skill-field implemented (first real run of new review gate)

Canary feature to exercise the new plan-phase review gate end-to-end. Every stage of the workflow succeeded on first try.

Implementation:
- SkillDefinition dataclass gained when_to_use: str = '' field (loader.py:49-51)
- load_skill_file parses both 'when-to-use' and 'when_to_use' frontmatter keys; added both to known_keys so they are consumed and not dumped into metadata (loader.py:82, 89)
- apply_token_budget appends '(use when: <when_to_use>)' to the description string when set; char budget includes when_to_use length (loader.py:212-216)
- No changes to the (name, desc) tuple return shape — backward compatible

Tests (7 new in tests/test_skill.py):
- test_skill_when_to_use_parsing_kebab_case: 'when-to-use' frontmatter key
- test_skill_when_to_use_parsing_snake_case: 'when_to_use' key
- test_skill_when_to_use_absent: default empty string
- test_skill_when_to_use_not_in_metadata: known_keys correctly excludes it from metadata dump
- test_skill_budget_includes_when_to_use: '(use when: ...)' appears in budget output
- test_skill_budget_without_when_to_use: skills without the field don't get the annotation
- test_skill_budget_counts_when_to_use_chars: budget accounting includes when_to_use length (enforces rule 8 footgun closure — a lazy impl that added the field but not the budget char count would fail this test)

Workflow dogfood (first real use of the new review gate):

Step 1 (init.sh): clean tree, 451 tests pass
Step 2 (pick): wanted canary, explicitly picked when-to-use-skill-field instead of the medium-priority top
Step 3 (review packet): machine sanity reported all sections present, no TODO, 4 verification commands validate, contract↔features.json in sync, 5 referenced files found (1 claude-code-source intentionally skipped as warn)
Step 4 (subagent review): fresh-context general-purpose agent worked the 12-rule checklist. Verified rule 3 (symbol doesn't already exist) by reading loader.py:28 and grepping. Verified rule 5 (design matches reality) by reading the exact lines the contract proposed to modify. Verified rule 6 (verification commands will fail before impl and pass after) by mentally simulating. Flagged two non-blocking nits: (a) contract says 429 baseline tests but actual is 451; (b) contract says upstream Claude Code accepts camelCase + snake_case but loadSkillsDir.ts:252 only reads snake_case. Neither affects the implementation. Verdict: approved, high confidence.
Step 5 (review-record): recorded with reviewer=subagent, notes captured for the history JSONL
Step 6 (implement): 3 file edits (loader.py dataclass, known_keys + parsing, apply_token_budget), 7 new tests
Step 7 (verify): 4/4 gate commands passed, cache written
Step 8 (complete): next

Tests: 451 -> 458 (+7). No regressions.

The review gate was not 'just' a paperwork check — the subagent reviewer's rule-by-rule walkthrough produced the concrete generator note 'ensure the new tests assert on actual parsed values and budget output substring, not just field existence' which I then enforced in test_skill_budget_counts_when_to_use_chars. That test would have caught a lazy impl that added the field but forgot to account for its chars in apply_token_budget — exactly the kind of footgun rule 8 is designed to close.

---

## 2026-04-06 — Harness contract review mechanism + article alignment

Ported from sdk-refactor branch (commits 908cd4e, 24f19e4, b70c3b6).

Major harness additions:
- harness.py review + review-record + review-miss subcommands
- Feature.reviewer field; reviewer='self' rejected for non-bootstrap features
- Review gate on verify/complete (review_status == approved AND contract_sha match)
- --skip-review REASON escape hatch
- Feature.phase derived property (plan_stub → plan_drafting → plan_review → generating → verifying → done)
- cmd_pick skips stub features (cmd_add placeholder verify) and surfaces them as BACKLOG NEEDS PLANNING
- validate_and_parse_verify_command rejects the PLACEHOLDER_VERIFY sentinel
- _machine_sanity checks contract ↔ features.json verification drift
- harness/reviewer-checklist.md loaded at runtime (was a Python string literal)
- harness/reviews/<id>.jsonl append-only review history (cmd_reset does not clear)
- cmd_review_miss records calibration events

Doc updates:
- README.md rewritten with Plan → Generate → Verify three-phase workflow
- CLAUDE.md workflow steps now show review gate (was 10 steps, now 13)
- Hard rules now include: reviewer=self rejection, double gate, reviews append-only, stub unpickable
- 'For why we don't copy the articles verbatim' section rewritten: the evaluator/generator split IS now enforced, just via the --reviewer gate rather than separate agent processes

Bootstrap:
- harness-contract-review was self-reviewed (reviewer=self, in _BOOTSTRAP_SELF_REVIEW_ALLOWED)
- Then reviewed round-by-round by subagents (4 bug rounds + 2 article-alignment rounds)
- Round 2 article-aligned review: PASS_WITH_MINOR_FIXES verdict, all minor items applied

Tests: test_harness_review.py with 17 tests (all passing in the sdk-refactor worktree). Mock test suite unaffected.

SDK refactor work (17 features) stays on sdk-refactor branch and will be worked on separately from main.

---

## 2026-04-06 — mcp-auth-refresh implemented

First feature shipped through the harness workflow.

Implementation:
- MCPTransport base class gained default update_headers() (no-op with debug log)
- SSETransport.update_headers: merges into self._headers AND httpx client.headers (takes effect on next POST)
- HTTPTransport.update_headers: same pattern
- WebSocketTransport.update_headers: stages headers for next reconnect (logs note)
- StdioTransport: inherits the no-op default (stdio has no HTTP headers)
- MCPClient: added on_auth_error: OnAuthErrorFn | None = None constructor arg
- New OnAuthErrorFn type alias: Callable[[str], Awaitable[dict[str, str] | None]]
- New MCPClient._transport_send method wraps self.transport.send() in try/except httpx.HTTPStatusError
- On 401/403 with callback set and _auth_retry_count == 0: invoke callback
- Callback returns dict: call update_headers, retry once with _auth_retry_count=1
- Callback returns None: re-raise original HTTPStatusError
- Callback raises: log warning, re-raise ORIGINAL auth error (not the callback's exception)
- Retry guard prevents loops (only one refresh per request)
- _send_request now calls _transport_send instead of transport.send directly
- _rebuild_session and connect's notifications/initialized pings also go through _transport_send

Tests (5 new, all passing):
- test_mcp_auth_refresh_callback_success: 401 then success with new headers
- test_mcp_auth_refresh_callback_none: 401 + None return → raises
- test_mcp_auth_refresh_no_callback: baseline, no callback → raises
- test_mcp_auth_refresh_callback_exception: callback raises → original error re-raised
- test_mcp_auth_refresh_only_retries_once: verifies no loop even if retry also fails

AuthRefreshTransport helper mocks httpx.HTTPStatusError by actually constructing httpx.Request/Response — no MagicMock tricks that could diverge from real httpx behavior.

Mock test total: 429 → 434 (+5). No regressions.

Harness workflow: the contract's verification gates (import check for on_auth_error parameter + hasattr check for update_headers + -k auth_refresh pytest filter) all correctly FAILED before implementation and PASS after. This validates the harness gating works end to end.

---

## 2026-04-06 — Harness round 3 review fixes

Round 3 verdict: PASS WITH MINOR FIXES. All 11 round-2 claims verified fixed. Addressed the 1 medium + 3 low + 1 info items the reviewer identified.

- [MEDIUM] _progress_edits_status diff parser: previously skipped any line starting with '---' as metadata, which wrongly caught bare '---' markdown HR lines in progress.md content. Reviewer demonstrated: deleting an HR line was not detected as a non-append edit. Fix: track in_hunk state (set by '@@' line) and only treat lines as removals inside hunks. Pre-hunk lines are diff metadata. Verified with a real git-diff reproduction.

- [LOW] working_tree_fingerprint now hashes untracked file CONTENTS (sha256), not just paths. Previous version would accept a same-named untracked file with different content as a cache hit. Bounded at 10MB/file; symlinks/fifos get a NONFILE marker. Verified by content-swap test.

- [LOW] Backfilled verified_tree field on all 5 existing features.json entries. Updated top-level description to accurately say 'harness.py verify writes verified_sha + verified_tree on success; harness.py complete sets passes=true' (previously conflated verify and complete).

- [LOW] --skip-progress-check now takes a non-empty audit REASON string (was action='store_true'). The reason is printed to stderr for logging. Bypassing without a reason fails loudly. Rejects whitespace-only reasons.

- [INFO] Added 'Safety model' section to CLAUDE.md explaining that the verify allow-list gates command SHAPE (prefixes), not payload. python -c '...' is allowed; the actual Python code must be reviewed in the plan commit. Harness is a collaboration quality gate, not a sandbox against hostile authors.

All 429 mock tests still pass. Reviewer's bypass tests (HR deletion, content swap) all correctly rejected now.

---

## 2026-04-06 — Harness round 2 review fixes

Applied fixes from round 2 review (15 findings). Critical fixes:

- harness.py: removed shell=True entirely. cmd_verify now uses shlex.split as argv list. This eliminates all shell-injection classes (newlines, redirects, process substitution, globs, command substitution). Reviewer's bypass attempts (pytest\nrm -rf ..., pytest >/tmp/pwn, pytest <(...)) all verified neutralized. Added defense-in-depth rejection of redirect-like tokens for fail-fast errors.
- init.sh: detects timeout / gtimeout / neither (fixes macOS where GNU coreutils is not default). Added trap cleanup for temp pytest log.
- features.json abort_reason filter fixed: reviewer correctly noted that '-k abort_reason' matches zero tests because none of the required test names contain that substring. Changed to explicit disjunction of the 3 full test names. Contract aligned.
- Working tree fingerprint cache (addresses N3): verify now records both HEAD SHA AND a sha256 of git diff HEAD + untracked files (excluding harness/features.json and harness/progress.md). complete invalidates cache if either changed. Closes the dirty-tree verify -> revert -> complete bypass.
- cmd_complete rejects blocked features (must reset first).
- cmd_complete now a single load; removed triple-read confusion.
- _progress_edits_status replaces _progress_has_pending_edits: checks both non-empty diff AND append-only (no removed lines in git diff HEAD). Fails closed on subprocess errors.
- cmd_log: explicit encoding='utf-8', rejects multi-line titles and markdown-metachar starts.
- Removed inspect.getsource grep-like check from wire-hooks verification (reviewer correctly flagged as same false-positive class as grep). Behavioral pytest tests are the real gate.
- CLAUDE.md: updated workflow to include resume/block/reset/log subcommands, note cache tree-hash behavior and append-only diff check.
- Feature dataclass: verified_tree field added alongside verified_sha.

All 429 mock tests still pass. Reviewer bypass attempts verified blocked (pytest >/tmp/X does not create file under argv mode).

---

## 2026-04-06 — Harness round 1 review fixes

Applied fixes from external reviewer (15 issues found). Critical fixes:

- init.sh: added set -o pipefail and switched cleanliness check to git status --porcelain (catches untracked files). Added 300s test timeout.
- harness.py: atomic save via tempfile + os.replace. Per-command 600s verify timeout. Allow-listed command prefixes. Blocked unquoted shell metachars. verified_sha cache prevents complete from re-running tests. progress.md touch gate on complete. New subcommands: resume, block, reset, log.
- features.json: replaced all grep-based gates with import/attribute checks via python -c. Tighter pytest -k filters to avoid false positives from pre-existing tests.
- Renamed pre-post-tool-hooks to wire-hooks-into-orchestrator after discovering HookEvent.PRE_TOOL_USE and HookManager already exist; the real gap is orchestrator wiring. Rewrote contract accordingly.
- Fixed mcp-auth-refresh design: HTTP auth errors raise from transport layer (raise_for_status), never reach _send_request's JSON-RPC loop. Catch must happen wrapping transport.send() call. Reference is now honestly noted as 'no direct analog in Claude Code'.
- Fixed StopHookFn type expression typo. Added is_meta + recovery.detect_interruption interaction criterion.
- Aligned all contract verification commands with features.json to single source of truth.

---

## 2026-04-06 — Harness established

Set up the harness workflow in `harness/` following the design principles from
Anthropic's harness articles, adapted for a library (not a web app).

### What was built

- `harness/README.md` — workflow documentation and rules
- `harness/init.sh` — environment startup check (venv, deps, test suite, git clean)
- `harness/harness.py` — CLI with `status`, `pick`, `verify`, `complete`, `add` subcommands
- `harness/features.json` — backlog seeded with 5 pending features
- `harness/contracts/README.md` — contract template
- `harness/contracts/<feature-id>.md` — contracts for each seed feature
- `harness/progress.md` — this file
- `CLAUDE.md` (repo root) — tells future sessions to follow the harness workflow

### Seed backlog

5 features identified from the Claude Code comparison analysis, marked as ⚠️
"valuable but not strictly necessary" during the prior module reviews:

1. `mcp-auth-refresh` (medium) — MCP auth refresh callback
2. `stop-hook-inject-continue` (medium) — stop hook can inject + continue
3. `abort-reason-tracking` (low) — abort signal with reason enum
4. `pre-post-tool-hooks` (low) — pre/post tool hook points
5. `when-to-use-skill-field` (low) — skill when_to_use frontmatter

### Retroactive summary of prior work

Before the harness was established, the following was done across many sessions
(reconstructed from git log for future reference):

- **Agent loop**: unified run/run_stream, cascade error recovery (PTL → reactive
  compact → autocompact), two-phase max_output_tokens recovery, retry-after
  header parsing, streaming finish_reason bug fix, tool interrupt behavior
- **Context management**: 6-layer compaction pipeline aligned with Claude Code
  (budget → snip → microcompact → autocompact → collapse → reactive), absolute
  buffer thresholds, compact boundary messages, post-compact file+skill+MCP
  restoration, NO_TOOLS_PREAMBLE
- **Tool system**: 28 mechanisms aligned with Claude Code, context_modifier fixed,
  GrepTool -B/-A/-C + VCS exclusion, FileEditTool fuzzy matching (5 strategies),
  FileWriteTool create vs update, permission system removed
- **Skill system**: 10 mechanisms aligned (frontmatter, conditional activation,
  inline/fork, variable substitution, post-compact restoration)
- **MCP integration**: 16 mechanisms aligned (4 transports, session rebuild,
  schema caching, tool adapter with annotations, 200K content limit)
- **Coordinator**: worker abort propagation, context isolation
- **Context engineering**: message normalization pipeline (5 passes) before API
- **Frontends**: TUI (Rich), Web GUI (FastAPI+SSE)
- **Tests**: 429 mock tests passing
- **Docs**: README in Chinese

### Why the harness was built

Prior work was ad-hoc: the user would ask "compare X with Claude Code", then
"implement the missing pieces", then repeat for the next module. This worked
because we had a running conversation with full context, but it doesn't scale:

1. Each new topic lost the analysis from the previous one
2. No single place recorded "what's the backlog"
3. Nothing enforced "verify before marking done" — we sometimes claimed
   things were aligned when they were not
4. No checkpoint between sessions — everything lived in the chat

The harness fixes all four: `features.json` is the backlog, `contracts/` lock
in acceptance criteria before implementation, `verify` is a hard gate, and
`progress.md` survives any compaction.

### Next session should

1. Run `./harness/init.sh` to verify the environment
2. Run `python harness/harness.py pick` to see the next feature
3. Read the contract for that feature
4. Implement it (one feature only)
5. `verify` → `complete` → append a new entry at the top of this file → commit
