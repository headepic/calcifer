# Calcifer Progress Log

Append-only log of what each session accomplished. Never edit old entries.
One entry per session. Newest at the top.

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
