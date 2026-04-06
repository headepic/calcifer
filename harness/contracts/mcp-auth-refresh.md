# Feature Contract: mcp-auth-refresh

## Motivation

Long-running sessions (>30 min) that connect to OAuth-protected MCP servers
currently break when the auth token expires. The client raises on 401 and the
entire session dies. Claude Code handles this with a refresh-and-retry pattern
that Calcifer lacks.

## Claude Code Reference

- `src/services/mcp/client.ts:363-421` — OAuth token handling
  - `checkAndRefreshOAuthTokenIfNeeded()` at line 375 — proactive refresh
  - `handleOAuth401Error()` at line 402 — reactive refresh on 401, retry if token changed
- `src/services/mcp/client.ts:152-159` — `McpAuthError` class
- `src/services/mcp/client.ts:257-316` — file-backed auth cache (15-min TTL)

The cache layer is out of scope (see non-goals). We only replicate the
refresh-and-retry mechanism.

## Scope

### 要做

- Add `on_auth_error` optional callback parameter to `MCPClient.__init__`
- When `_send_request` receives HTTP 401 or 403 (or JSON-RPC error code
  matching auth failure), invoke the callback
- Callback signature: `async def on_auth_error(server_name: str) -> dict[str, str] | None`
- If callback returns new headers, update transport and retry the request once
- If callback returns None or is not set, raise as today
- Add tests covering:
  - Callback not set → current behavior (raise)
  - Callback returns new headers → retry succeeds
  - Callback returns None → raise
  - Callback raises → wrap and raise original auth error

### 不做 (non-goals)

- No persistent auth cache (claude-code has one, we don't need it for a library)
- No OAuth flow itself — the callback is the user's responsibility
- No proactive refresh (only reactive on 401) — proactive requires cache
- No Anthropic-specific needs-auth file at `~/.config/claude/...`

## Design

Changes to `calcifer/services/mcp/client.py`:

1. Add `on_auth_error` parameter to `MCPClient` dataclass (callable or None)
2. Extend `_send_request` to catch auth errors (currently only catches -32001):
   - Detect by HTTP status (if available from transport) or JSON-RPC error code
   - Before raising, check if callback is set AND we haven't retried yet
   - If yes: call callback, update `self.transport` headers if possible, retry
3. Only one retry per request (similar to existing `_retry_count` pattern)

Transport layer concern: the HTTP/SSE transports need a way to update headers
after connect. Check current `transport.py` — if `headers` is only set in
`__init__`, we need to add a `update_headers(h: dict)` method to the abstract
`MCPTransport` class.

## Acceptance Criteria

- [ ] `MCPClient.__init__` accepts `on_auth_error: Callable[[str], Awaitable[dict | None]] | None = None`
- [ ] `MCPTransport` abstract class has `update_headers(headers: dict) -> None` method (stdio no-op, sse/http updates)
- [ ] `_send_request` detects 401/403 (HTTP) and auth-related JSON-RPC errors
- [ ] On auth error with callback set and no prior retry: invoke callback
- [ ] If callback returns dict: update headers, retry request once
- [ ] If callback returns None: raise original auth error
- [ ] Callback exceptions are caught and logged, original auth error raised
- [ ] New test `test_mcp_auth_refresh_callback_success` — simulates 401 → callback returns headers → retry succeeds
- [ ] New test `test_mcp_auth_refresh_callback_none` — simulates 401 → callback returns None → raises
- [ ] New test `test_mcp_auth_refresh_no_callback` — simulates 401 → no callback → raises (current behavior)
- [ ] All existing mock tests still pass (429 baseline)

## Verification Commands

```
.venv/bin/python -m pytest tests/test_mcp.py -q
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

## Rollback Plan

If the transport layer changes prove too invasive (e.g., stdio doesn't cleanly
support header updates), scope down:

- Fallback scope: on auth error, just call the callback as a notification
  (no retry) and raise. User can reconnect externally.
- Record the scope reduction in `progress.md` and update `features.json`
  description.

If the whole approach fails, `git reset --hard HEAD~N` and mark the feature
`blocked` with a note explaining why.
