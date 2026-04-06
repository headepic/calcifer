# Feature Contract: abort-reason-tracking

## Motivation

Calcifer's abort signal is currently a bare `asyncio.Event`. When it fires,
tools see an opaque boolean `abort_signal` and all produce the same
"Tool execution was cancelled" error message. Claude Code distinguishes:

- `user_interrupt` — user pressed Ctrl+C / closed the UI
- `sibling_error` — a parallel tool in the same batch failed
- `streaming_fallback` — the LLM call had to fall back to non-streaming mid-stream
- `timeout` — a timeout hit

Knowing the reason matters because:
1. Error messages to the LLM should be accurate ("your sibling tool errored"
   vs "the user cancelled")
2. Telemetry needs the distinction
3. Some recovery logic only makes sense for specific reasons

## Claude Code Reference

- `src/services/tools/StreamingToolExecutor.ts:153-205` — synthetic error
  message generation for `sibling_error`, `user_interrupted`, `streaming_fallback`
- `src/services/tools/StreamingToolExecutor.ts:301-318` — per-tool child
  AbortController, sibling abort propagation
- `src/query.ts:1348-1351` — abort reason check at end-of-turn
- `src/utils/abortController.ts` — AbortController hierarchy with reasons

## Scope

### 要做

- Add `AbortReason` enum in `calcifer/types/tools.py`:
  - `USER_INTERRUPT = "user_interrupt"`
  - `SIBLING_ERROR = "sibling_error"`
  - `TIMEOUT = "timeout"`
  - `OTHER = "other"`
- Add `abort_reason: AbortReason | None = None` field to `ToolContext`
- `Agent.abort()` accepts optional `reason: AbortReason = USER_INTERRUPT`
- Agent stores the reason alongside the Event (simple `self._abort_reason` attribute)
- When propagating abort into ToolContext (`context.abort_signal = ...`),
  also set `context.abort_reason = self._abort_reason`
- Orchestrator: when cancelling a tool due to sibling error, set
  `context.abort_reason = SIBLING_ERROR` temporarily for that tool's result message
- Error messages use the reason: `"Cancelled: {reason.value}"`

### 不做 (non-goals)

- No per-tool child AbortController hierarchy (current single Event is enough
  for Calcifer's scale)
- No streaming_fallback reason (we don't do streaming-to-nonstreaming fallback)
- No cross-session propagation of reasons
- No reason-specific recovery logic in the main loop (that's a separate feature)

## Design

Changes to `calcifer/types/tools.py`:

```python
class AbortReason(str, Enum):
    USER_INTERRUPT = "user_interrupt"
    SIBLING_ERROR = "sibling_error"
    TIMEOUT = "timeout"
    OTHER = "other"

@dataclass
class ToolContext:
    ...
    abort_signal: bool = False
    abort_reason: AbortReason | None = None
```

Changes to `calcifer/agent.py`:

```python
def abort(self, reason: AbortReason = AbortReason.USER_INTERRUPT) -> None:
    self._abort_reason = reason
    self._abort_event.set()
```

Then anywhere the loop sets `context.abort_signal = self._abort_event.is_set()`,
also set `context.abort_reason = self._abort_reason`.

Changes to `calcifer/services/tools/orchestrator.py`:

- `run_tools` serial fallback: when the sibling has errored, set
  `context.abort_reason = AbortReason.SIBLING_ERROR` in the synthetic message
- `StreamingToolExecutor._run`: when `self._has_errored`, build the message
  with SIBLING_ERROR reason

Error message template:
```python
def _cancellation_message(reason: AbortReason | None) -> str:
    if reason == AbortReason.USER_INTERRUPT:
        return "Cancelled: user interrupted"
    if reason == AbortReason.SIBLING_ERROR:
        return "Cancelled: a parallel tool in the same batch errored"
    if reason == AbortReason.TIMEOUT:
        return "Cancelled: timeout"
    return "Cancelled"
```

## Acceptance Criteria

- [ ] `AbortReason` enum defined in `calcifer/types/tools.py` with 4 values
- [ ] Exported from `calcifer/__init__.py` and `calcifer/types/__init__.py`
- [ ] `ToolContext.abort_reason` field added (default None)
- [ ] `Agent.abort()` accepts `reason` parameter (default USER_INTERRUPT)
- [ ] `Agent._abort_reason` tracks the most recent abort reason
- [ ] `context.abort_reason` propagates from agent state to tool context
- [ ] Sibling-error cancellation uses SIBLING_ERROR reason
- [ ] Cancelled tool result messages include reason text
- [ ] New test `test_abort_user_interrupt_reason` — abort with USER_INTERRUPT, verify reason in tool result
- [ ] New test `test_abort_sibling_error_reason` — parallel tool fails, siblings get SIBLING_ERROR reason
- [ ] New test `test_abort_default_reason` — abort() without arg defaults to USER_INTERRUPT
- [ ] All 429 mock tests still pass

## Verification Commands

```
.venv/bin/python -c "from calcifer.types.tools import AbortReason; assert AbortReason.USER_INTERRUPT and AbortReason.SIBLING_ERROR"
.venv/bin/python -c "from calcifer import AbortReason"
.venv/bin/python -c "from calcifer.types.tools import ToolContext; import dataclasses; fields = {f.name for f in dataclasses.fields(ToolContext)}; assert 'abort_reason' in fields"
.venv/bin/python -c "from calcifer.agent import Agent; import inspect; sig = inspect.signature(Agent.abort); assert 'reason' in sig.parameters"
.venv/bin/python -m pytest tests/ -q -k 'abort_user_interrupt_reason or abort_sibling_error_reason or abort_default_reason'
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

Narrow keyword `abort_reason` (not `abort or interrupt`) to avoid matching
unrelated existing tests that mention interrupts. Must match `features.json`
verification exactly.

## Rollback Plan

If the ToolContext field addition breaks many downstream usages, put the
reason in `context.metadata["abort_reason"]` instead of a dedicated field.
Update the contract and the tests.

If the enum conflicts with any existing symbol, rename to `AbortSignalReason`.
