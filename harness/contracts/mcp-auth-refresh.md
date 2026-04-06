# Feature Contract: mcp-auth-refresh

## Motivation

Long-running sessions (>30 min) that connect to OAuth-protected MCP servers
currently break when the auth token expires. The client raises on 401 and the
entire session dies. Claude Code handles this with a refresh-and-retry pattern
that Calcifer lacks.

## Claude Code Reference

**Important: no direct analog exists in Claude Code.**

`claude-code-source/src/services/mcp/client.ts:363-421` is
`createClaudeAiProxyFetch()`, which is specific to claude.ai's OAuth proxy:
- Uses Anthropic's keychain-backed `getClaudeAIOAuthTokens()` singleton
- Calls `checkAndRefreshOAuthTokenIfNeeded()` — a global refresh cycle,
  not a pluggable callback
- Uses lockfile-based cache invalidation

That pattern is tightly coupled to Anthropic's OAuth machinery. Calcifer is
provider-agnostic, so we invent a **generic callback** inspired by the
reactive-refresh idea but decoupled from any specific OAuth flow.

The cache layer, proactive refresh, and needs-auth file cache are all
explicitly out of scope (see non-goals).

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

### Critical: auth errors happen in the transport layer, not in `_send_request`

`calcifer/services/mcp/transport.py` lines 227 and 270 call
`resp.raise_for_status()` right after POSTing. A 401 from the server becomes
`httpx.HTTPStatusError` raised **from inside `transport.send()`** — it never
reaches `_send_request()`'s JSON-RPC response loop. So catching it inside the
response loop (as an earlier draft of this contract proposed) is wrong.

The catch must happen where `self.transport.send(request)` is called.

### Changes to `calcifer/services/mcp/transport.py`

Add `update_headers` to the `MCPTransport` abstract base class:

```python
class MCPTransport(ABC):
    @abstractmethod
    async def update_headers(self, headers: dict[str, str]) -> None:
        """Update auth / request headers for subsequent send() calls."""
        ...
```

Implementations:
- `StdioTransport.update_headers` — no-op (stdio has no HTTP headers, log a debug line)
- `SSETransport.update_headers` — merge into `self._headers` (or whatever the
  current field is called); next SSE reconnect picks them up
- `HTTPTransport.update_headers` — merge into the dict passed to `httpx.post(..., headers=...)`
- `WebSocketTransport.update_headers` — best-effort; log + update for reconnect

### Changes to `calcifer/services/mcp/client.py`

1. Add constructor parameter:
   ```python
   @dataclass
   class MCPClient:
       name: str
       transport: MCPTransport
       on_auth_error: Callable[[str], Awaitable[dict[str, str] | None]] | None = None
       ...
   ```

2. Wrap `await self.transport.send(request)` in `_send_request`:
   ```python
   async def _send_request(self, method, params=None, _retry_count=0):
       request = {...}
       try:
           await self.transport.send(request)
       except httpx.HTTPStatusError as e:
           status = e.response.status_code
           if status in (401, 403) and self.on_auth_error and _retry_count == 0:
               logger.info("MCP %s: auth error %d, calling refresh callback", self.name, status)
               try:
                   new_headers = await self.on_auth_error(self.name)
               except Exception as cb_exc:
                   logger.warning("MCP %s: on_auth_error callback raised: %s", self.name, cb_exc)
                   raise e from cb_exc
               if new_headers:
                   await self.transport.update_headers(new_headers)
                   return await self._send_request(method, params, _retry_count=1)
           raise
       # ... existing response loop unchanged
   ```

### Stdio behavior

Stdio transport is documented as a no-op for auth refresh. If an auth
callback is set and a stdio transport is used, we log a warning once on
first use. Stdio MCP servers authenticate via environment variables at
subprocess launch, not via headers; refresh requires relaunching the
process (explicitly out of scope).

## Acceptance Criteria

- [ ] `MCPTransport` abstract class has `update_headers(headers: dict) -> None` method
- [ ] `StdioTransport.update_headers` is a no-op + debug log
- [ ] `SSETransport.update_headers` updates internal headers
- [ ] `HTTPTransport.update_headers` updates internal headers
- [ ] `MCPClient.__init__` accepts `on_auth_error` parameter
- [ ] `_send_request` wraps `self.transport.send(request)` in try/except `httpx.HTTPStatusError`
- [ ] 401/403 with callback set and `_retry_count == 0` invokes the callback
- [ ] Callback returning dict triggers `update_headers` + single retry
- [ ] Callback returning None raises the original `HTTPStatusError`
- [ ] Callback raising an exception logs and re-raises the original `HTTPStatusError`
- [ ] No retry after first retry (`_retry_count` guard prevents loops)
- [ ] New test `test_mcp_auth_refresh_callback_success` — simulates 401 → callback returns headers → retry succeeds
- [ ] New test `test_mcp_auth_refresh_callback_none` — simulates 401 → callback returns None → raises
- [ ] New test `test_mcp_auth_refresh_no_callback` — simulates 401 → no callback → raises (baseline)
- [ ] New test `test_mcp_auth_refresh_callback_exception` — callback raises → original 401 raised
- [ ] All existing mock tests still pass (429 baseline)

## Verification Commands

```
.venv/bin/python -c "from calcifer.services.mcp.client import MCPClient; import inspect; sig = inspect.signature(MCPClient); assert 'on_auth_error' in sig.parameters, 'on_auth_error missing from MCPClient'"
.venv/bin/python -c "from calcifer.services.mcp.transport import MCPTransport; assert hasattr(MCPTransport, 'update_headers'), 'update_headers missing from MCPTransport'"
.venv/bin/python -m pytest tests/test_mcp.py -q -k 'auth_refresh'
.venv/bin/python -m pytest tests/test_mcp.py -q
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

Must match the `verification` array in `harness/features.json` exactly —
keep the two in sync when editing.

## Rollback Plan

If the transport layer changes prove too invasive (e.g., stdio doesn't cleanly
support header updates), scope down:

- Fallback scope: on auth error, just call the callback as a notification
  (no retry) and raise. User can reconnect externally.
- Record the scope reduction in `progress.md` and update `features.json`
  description.

If the whole approach fails, `git reset --hard HEAD~N` and mark the feature
`blocked` with a note explaining why.
