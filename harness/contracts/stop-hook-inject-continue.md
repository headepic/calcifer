# Feature Contract: stop-hook-inject-continue

## Motivation

Current stop hooks can only return `bool` — stop the loop or keep going.
Claude Code's stop hooks can also inject messages (blocking errors, budget
warnings, correction prompts) and optionally continue. This enables:

- Budget enforcement that asks the model to wrap up (instead of hard-killing)
- Content filter corrections ("your output violated X, please rewrite")
- External state updates injected mid-loop without tool calls

Without this, any hook that wants to influence the model has to either stop
the loop entirely or add a synthetic tool.

## Claude Code Reference

- `src/query/stopHooks.ts:64-250` — `handleStopHooks()` return shape
  - Returns `{ blockingErrors: Message[], preventContinuation: boolean }`
  - `blockingErrors` are injected into the conversation as meta messages
  - If `preventContinuation` is true, loop breaks after injection
- `src/query.ts:1267-1306` — caller integration:
  - If blockingErrors non-empty: reassign state.messages with them appended, `continue`
  - If preventContinuation: `return { reason: 'stop_hook_prevented' }`

## Scope

### 要做

- New `StopHookResult` dataclass in `calcifer/agent.py`:
  - `stop: bool` (default False)
  - `inject_messages: list[Message]` (default empty)
- Update `StopHookFn` type to allow returning `bool | StopHookResult`
- In the agent loop, after running each hook:
  - Normalize the result: bool → StopHookResult(stop=bool, inject_messages=[])
  - Append any inject_messages to conversation (they will be sent next turn)
  - If any hook returned stop=True, break after all hooks run
- Inject messages must be marked `is_meta=True` for recovery tracking
- Backward compat: hooks returning bool still work

### 不做 (non-goals)

- No "blocking error" vs "info message" distinction (Claude Code does, we keep it simple)
- No hook ordering changes
- No async generator hook results
- No retrying the turn after injection (messages just get sent next turn)

## Design

Changes to `calcifer/agent.py`:

1. Add `StopHookResult` dataclass near the top (after `AgentResult`)
2. Update `StopHookFn` type — the return can be any of: bool, StopHookResult,
   or an awaitable of either:
   ```python
   StopHookReturn = bool | StopHookResult
   StopHookFn = Callable[
       [list[Message], "ToolContext"],
       StopHookReturn | Awaitable[StopHookReturn],
   ]
   ```
3. In the `_run_loop_inner` stop hook section (around line 740):
   - After resolving the hook result (await if coroutine), normalize to StopHookResult
   - Collect all inject_messages from all hooks
   - Extend conversation with them
   - If any `stop=True`, set `should_stop = True`
4. No changes needed to the session save logic — the injected messages are
   just regular messages from the loop's POV

Tests: add test cases with:
- Hook returns False (no-op, existing behavior)
- Hook returns True (stop, existing behavior)
- Hook returns StopHookResult(stop=False, inject_messages=[msg]) — next turn sees msg
- Hook returns StopHookResult(stop=True, inject_messages=[msg]) — msg injected, then stop

## Acceptance Criteria

- [ ] `StopHookResult` dataclass defined in calcifer/agent.py with fields stop and inject_messages
- [ ] `StopHookResult` exported from `calcifer/__init__.py`
- [ ] Stop hooks can return `bool` OR `StopHookResult` (union in type hint)
- [ ] Bool return values preserved (True = stop, False = continue)
- [ ] StopHookResult with inject_messages non-empty appends them to conversation
- [ ] StopHookResult with stop=True stops the loop after appending
- [ ] Injected messages are marked `is_meta=True`
- [ ] Injected meta messages do not break `calcifer.utils.recovery.detect_interruption` — `recovery.py:36` treats `is_meta=True` user messages as non-terminal, so our injection must not be misread as mid-prompt interruption
- [ ] Hook exceptions are caught and logged, not propagated (existing behavior preserved)
- [ ] New test `test_stop_hook_inject_and_continue` verifies injection without stopping
- [ ] New test `test_stop_hook_inject_and_stop` verifies inject + stop
- [ ] Existing bool-based stop hook tests still pass
- [ ] All 429 mock tests still pass

## Verification Commands

```
.venv/bin/python -c "from calcifer.agent import StopHookResult; r = StopHookResult(stop=False, inject_messages=[]); assert hasattr(r, 'stop') and hasattr(r, 'inject_messages')"
.venv/bin/python -c "from calcifer import StopHookResult"
.venv/bin/python -m pytest tests/ -q -k 'stop_hook_inject'
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

Must match `features.json` verification exactly.

## Rollback Plan

If the type union causes mypy or type-checker issues, fall back to a single
type: require all hooks to return `StopHookResult`, with a deprecation shim
that wraps bool returns in a warning. Update existing tests to use the new type.

If the injection mechanism conflicts with the existing `inject_messages` logic
elsewhere (unlikely but possible), `git reset` and re-scope.
