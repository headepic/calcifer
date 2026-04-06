# Feature Contract: wire-hooks-into-orchestrator

## Motivation

`HookEvent.PRE_TOOL_USE` and `POST_TOOL_USE` already exist in
`calcifer/services/hooks.py:25-32`. `HookManager.run_hooks()` with tool
pattern matching, permission decision override, `updated_input` rewriting,
and `additional_context` merging are all fully implemented.

But the tool orchestrator never calls any of this:

```
$ grep -n 'hook\|run_hooks' calcifer/services/tools/orchestrator.py
(no matches)
```

This feature wires the existing HookManager into `execute_tool_call` and
`StreamingToolExecutor`. No new types, no new infrastructure — just
connecting already-built pieces.

## Claude Code Reference

- `claude-code-source/src/services/tools/toolExecution.ts:599-900` —
  `runPreToolUseHooks()` and `runPostToolUseHooks()` calling pattern
  inside `checkPermissionsAndCallTool()`
- `claude-code-source/src/services/hooks.ts` — the `runHooks` implementation
  with `should_continue`, `updated_input`, `permission_decision` semantics

Calcifer's `HookResult` and `HookConfig` are already modeled on this.
The reference is for the **wiring location**, not the hook API shape.

## Scope

### 要做

- Add `hook_manager: HookManager | None = None` field to `ToolContext`
  (in `calcifer/types/tools.py`)
- Agent wires its `self._hook_manager` into every `ToolContext` it creates
  (in `calcifer/agent.py::_run_loop_inner`)
- `execute_tool_call` calls `context.hook_manager.run_hooks(PRE_TOOL_USE, ...)`
  after `check_input` succeeds, before `tool.call(...)`
- Handle pre-hook return:
  - `should_continue=False` → return an error tool result immediately
    (don't call the tool)
  - `updated_input` set → re-validate and use the new input
  - `additional_context` set → prepend to tool result content at the end
    (post-hook injects it; pre-hook stashes it in context metadata)
- `execute_tool_call` calls `context.hook_manager.run_hooks(POST_TOOL_USE, ...)`
  after `tool.call` returns, before the `context_modifier` step
- Post-hook `additional_context` is appended to the tool result content
- Hook exceptions are caught and logged (hook failures must not crash the call)
- Write tests for: veto, rewrite, additional_context injection, exception handling

### 不做 (non-goals)

- No new `on_pre_tool` / `on_post_tool` registration API — use the existing
  `register_callback(HookEvent.PRE_TOOL_USE, ...)` API
- No new `HookResult` fields — use the ones that exist
- No async generator hooks
- No hook priority / ordering — use registration order
- No pre-hook replacing the tool result (only `should_continue=False`
  short-circuits; actual result replacement is not in scope)

## Design

### Changes to `calcifer/types/tools.py`

Add one field to `ToolContext`:

```python
@dataclass
class ToolContext:
    ...
    hook_manager: Any = None  # HookManager | None, typed as Any to avoid import cycle
```

### Changes to `calcifer/agent.py`

In `_run_loop_inner` where `ToolContext(...)` is constructed, pass
`hook_manager=self._hook_manager`. Note: Calcifer's `Agent` currently does not
own a `HookManager` — add `self._hook_manager: HookManager | None = None`
in `__init__` and a `set_hook_manager()` method for user setup.

### Changes to `calcifer/services/tools/orchestrator.py::execute_tool_call`

Between step 3b (check_input) and step 4 (execute):

```python
pre_additional_context = ""
if context.hook_manager:
    from ...services.hooks import HookEvent, HookInput
    try:
        hook_input = HookInput(
            hook_event_name=HookEvent.PRE_TOOL_USE.value,
            tool_name=tool.name,
            tool_input=raw_args,
            session_id=context.chain_id or "",
            cwd=context.cwd,
        )
        pre_result = await context.hook_manager.run_hooks(HookEvent.PRE_TOOL_USE, hook_input)
        if not pre_result.should_continue:
            return Message(
                role="tool",
                content=f"Tool blocked by pre-use hook: {pre_result.stop_reason}",
                tool_call_id=tc.id,
            )
        if pre_result.updated_input is not None:
            raw_args = pre_result.updated_input
            try:
                validated = tool.validate_input(raw_args)
            except Exception as e:
                return Message(
                    role="tool",
                    content=f"Invalid input after hook rewrite: {e}",
                    tool_call_id=tc.id,
                )
        pre_additional_context = pre_result.additional_context or ""
    except Exception as e:
        logger.warning("Pre-tool hook for %s raised: %s", tool.name, e)
```

After step 4 (tool.call) returns:

```python
if context.hook_manager:
    from ...services.hooks import HookEvent, HookInput
    try:
        post_input = HookInput(
            hook_event_name=HookEvent.POST_TOOL_USE.value,
            tool_name=tool.name,
            tool_input=raw_args,
            session_id=context.chain_id or "",
            cwd=context.cwd,
        )
        post_result = await context.hook_manager.run_hooks(HookEvent.POST_TOOL_USE, post_input)
        if post_result.additional_context:
            result.content = f"{result.content}\n\n{post_result.additional_context}"
    except Exception as e:
        logger.warning("Post-tool hook for %s raised: %s", tool.name, e)
```

### Note on `HookManager.run_hooks`

`HookManager.run_hooks()` already returns a merged `HookResult`. Check the
actual signature in `hooks.py` before writing the tests — if it takes a
different argument shape than assumed above, adapt the design but not the
contract.

## Acceptance Criteria

- [ ] `ToolContext.hook_manager` field exists (Any type, default None)
- [ ] `Agent` has `self._hook_manager: HookManager | None` initialized in `__init__`
- [ ] `Agent` passes `hook_manager` into every `ToolContext` it constructs
- [ ] `execute_tool_call` imports `HookEvent` lazily and calls `run_hooks(PRE_TOOL_USE, ...)` after `check_input` and before `tool.call`
- [ ] Pre-hook `should_continue=False` returns an error tool result with the stop_reason
- [ ] Pre-hook `updated_input` replaces `raw_args` and re-runs `tool.validate_input`
- [ ] `execute_tool_call` calls `run_hooks(POST_TOOL_USE, ...)` after `tool.call`
- [ ] Post-hook `additional_context` is appended to the tool result content
- [ ] Pre-hook and post-hook exceptions are caught and logged, execution continues
- [ ] New test `test_pre_tool_hook_veto` — pre-hook returns should_continue=False → tool not called, error message produced
- [ ] New test `test_pre_tool_hook_rewrite` — pre-hook returns updated_input → tool sees new input
- [ ] New test `test_post_tool_hook_additional_context` — post-hook appends text to result
- [ ] New test `test_hook_exception_does_not_crash` — hook raises, tool still runs normally
- [ ] All 429 existing mock tests still pass

## Verification Commands

```
.venv/bin/python -c "from calcifer.types.tools import ToolContext; import dataclasses; assert 'hook_manager' in {f.name for f in dataclasses.fields(ToolContext)}"
.venv/bin/python -m pytest tests/ -q -k 'pre_tool_hook_veto or pre_tool_hook_rewrite or post_tool_hook_additional_context or tool_hook_exception_does_not_crash'
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

Note: we intentionally do NOT include an `inspect.getsource` substring check.
Round 2 review flagged it as morally equivalent to grep — a docstring
containing the required strings would pass. The behavioral tests
(`test_pre_tool_hook_veto` etc.) are the real gate: they exercise the actual
wired-up code path.

## Rollback Plan

If adding `hook_manager` to `ToolContext` causes circular import problems
with `calcifer/services/hooks.py`, use `Any` as the type annotation (not a
forward ref) and document the runtime type in a comment.

If wiring into `execute_tool_call` conflicts with the `context_modifier`
ordering, keep the post-hook BEFORE `context_modifier` (as specified) so
the modifier still sees any context the post-hook wrote.

If the test patterns don't match existing test conventions, `git reset` and
study the existing hook tests first.
