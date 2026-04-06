# Feature Contract: pre-post-tool-hooks

## Motivation

Calcifer's tool execution pipeline has no extension point between validation
and execution. Claude Code runs `runPreToolUseHooks` and `runPostToolUseHooks`
around every tool call. Use cases this enables:

- Audit logging (log every tool call with inputs)
- Content filtering / PII scrubbing on inputs or outputs
- Input rewriting (e.g., expand abbreviated paths)
- Policy enforcement (deny-list without touching tool code)
- Telemetry injection

Without hooks, each of these requires modifying every tool or wrapping the
entire orchestrator externally. Hooks make them one-liner registrations.

## Claude Code Reference

- `src/services/tools/toolExecution.ts:599-900` — `checkPermissionsAndCallTool`
  - `runPreToolUseHooks()` called around line 750+
  - `runPostToolUseHooks()` / `runPostToolUseFailureHooks()` called after
- `src/services/hooks.ts` — hook registration and execution

Calcifer already has `calcifer/services/hooks.py` with `HookManager` — this
feature extends it with tool-specific hook points.

## Scope

### 要做

- Add two new `HookEvent` types: `PRE_TOOL_USE`, `POST_TOOL_USE`
- Extend `HookManager` with `run_pre_tool` and `run_post_tool` methods
- Pre-tool hook signature: `async def on_pre_tool(tool_name, input_dict, context) -> dict | bool`
  - Return `False` to veto execution (tool result becomes an error message)
  - Return `dict` to rewrite the input (new input replaces original)
  - Return `True` (or None) to allow with original input
- Post-tool hook signature: `async def on_post_tool(tool_name, input_dict, result, context) -> ToolResult | None`
  - Return `ToolResult` to replace the result
  - Return `None` to keep the original
- Hook exceptions are caught, logged, and do not crash the tool call
- Wire both into `execute_tool_call` in orchestrator:
  - Pre-hook after `check_input`, before `tool.call`
  - Post-hook after `tool.call`, before `truncate`
- Pre-hook veto produces a tool result with `is_error=True` and a clear message

### 不做 (non-goals)

- No pre/post hooks for non-tool LLM calls (compact, classify, etc.)
- No hook priority / ordering (just registration order)
- No async generator hooks (just plain async functions)
- No per-tool hook registration (hooks see tool_name and filter themselves)

## Design

Changes to `calcifer/services/hooks.py`:

1. Add to `HookEvent` enum:
   ```python
   PRE_TOOL_USE = "pre_tool_use"
   POST_TOOL_USE = "post_tool_use"
   ```
2. Add methods to `HookManager`:
   ```python
   async def run_pre_tool(self, tool_name, input_dict, context) -> dict | bool:
       # Run all registered pre-tool hooks
       # Return False if any veto, else final input_dict
       ...

   async def run_post_tool(self, tool_name, input_dict, result, context) -> ToolResult:
       # Run all registered post-tool hooks
       # Return final result (may be replaced by any hook)
       ...
   ```

Changes to `calcifer/services/tools/orchestrator.py`:

1. `execute_tool_call` needs access to the hook manager. Add `hook_manager`
   to `ToolContext` (optional).
2. After step 3b (check_input), before step 4 (execute):
   ```python
   if context.hook_manager:
       pre_result = await context.hook_manager.run_pre_tool(tool.name, raw_args, context)
       if pre_result is False:
           return Message(role="tool", content="Tool blocked by pre-use hook", ...)
       if isinstance(pre_result, dict):
           raw_args = pre_result
           validated = tool.validate_input(raw_args)
   ```
3. After step 4 (tool.call), before step 5 (context_modifier):
   ```python
   if context.hook_manager:
       replaced = await context.hook_manager.run_post_tool(
           tool.name, raw_args, result, context,
       )
       if replaced is not None:
           result = replaced
   ```

Agent wires hook_manager into context:
```python
context = ToolContext(..., hook_manager=self._hook_manager)
```

(Requires adding `hook_manager` field to `ToolContext`.)

## Acceptance Criteria

- [ ] `HookEvent.PRE_TOOL_USE` and `HookEvent.POST_TOOL_USE` exist
- [ ] `HookManager.run_pre_tool(tool_name, input, context)` async method
- [ ] `HookManager.run_post_tool(tool_name, input, result, context)` async method
- [ ] `ToolContext.hook_manager` field (optional)
- [ ] `execute_tool_call` invokes pre-tool hooks after validation
- [ ] Pre-hook returning `False` produces an error tool result (no tool.call)
- [ ] Pre-hook returning `dict` rewrites the input (with re-validation)
- [ ] Pre-hook returning `None` or `True` allows with original input
- [ ] `execute_tool_call` invokes post-tool hooks after tool.call
- [ ] Post-hook can return a new ToolResult that replaces the original
- [ ] Hook exceptions are caught and logged, tool call continues
- [ ] Agent wires its HookManager into ToolContext automatically
- [ ] New test `test_pre_tool_hook_veto` — pre-hook returns False → tool not called
- [ ] New test `test_pre_tool_hook_rewrite` — pre-hook mutates input → tool sees new input
- [ ] New test `test_post_tool_hook_replace` — post-hook returns new result
- [ ] New test `test_hook_exception_does_not_crash` — hook raises, tool still runs
- [ ] All 429 mock tests still pass

## Verification Commands

```
.venv/bin/python -m pytest tests/ -q -k 'hook' --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

## Rollback Plan

If adding `hook_manager` to `ToolContext` creates too many call-site changes,
fall back to passing the HookManager through `execute_tool_call`'s kwargs
instead, and have the agent pass it explicitly in `_execute_tools`.

If the post-hook result replacement conflicts with `context_modifier` logic,
run post-hook AFTER context_modifier (last in pipeline).
