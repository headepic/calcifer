# Feature Contract: sdk-agent-run-sync

## Motivation

`Agent.run()` is async-only. SDK users who want to call the agent from
a sync script, REPL, or simple CLI tool currently must write
`asyncio.run(agent.run(prompt))` themselves. Standard SDK courtesy is
to provide a thin synchronous wrapper.

The wrapper is ~5 lines of code but materially improves the "first 60
seconds" experience: a user can `pip install calcifer` and run a
synchronous hello-world without learning about asyncio.

## Claude Code Reference

No direct analog. Claude Code is TypeScript with a different async
model. The sync-wrapper convention comes from the Python SDK ecosystem
(Anthropic SDK, OpenAI SDK both expose sync + async forms of the same
methods).

## Scope

### 要做

- Add `Agent.run_sync(prompt, *, messages=None) -> AgentResult` method
- Implementation: detect "called from inside a running event loop" and
  raise a clear error; otherwise `asyncio.run(self.run(...))`
- 2 new tests in `tests/test_p0.py`:
  - `test_agent_run_sync_basic` — sync call returns AgentResult
  - `test_agent_run_sync_inside_loop_raises` — calling from inside an
    asyncio loop raises a clear error

### 不做 (non-goals)

- Not adding `run_stream_sync`. Streaming-from-sync is a different
  beast and YAGNI for now.
- Not changing the existing `run()` signature.
- Not adding `__call__` shortcut. Explicit `run` / `run_sync` is clearer.

## Design

### Changes to `calcifer/agent.py`

Add a `run_sync` method right after `run`:

```python
def run_sync(
    self,
    prompt: str,
    *,
    messages: list[Message] | None = None,
) -> AgentResult:
    """Synchronous wrapper around run()."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # No running loop — safe to use asyncio.run
    else:
        raise RuntimeError(
            "Agent.run_sync() cannot be called from inside a running "
            "asyncio event loop. Use `await agent.run(...)` instead."
        )
    return asyncio.run(self.run(prompt, messages=messages))
```

`asyncio` is already imported at the top of agent.py, so no new imports.

### Tests

Add to `tests/test_p0.py` using the existing MockProvider patching pattern.

## Acceptance Criteria

- [ ] `Agent.run_sync` method exists and is sync (not a coroutine)
- [ ] `inspect.iscoroutinefunction(Agent.run_sync) is False`
- [ ] When called outside a loop, returns an `AgentResult` from a mocked agent
- [ ] When called from inside a running loop, raises `RuntimeError` with "cannot be called from inside" in the message
- [ ] New test `test_agent_run_sync_basic` passes
- [ ] New test `test_agent_run_sync_inside_loop_raises` passes
- [ ] All 468 existing mock tests still pass
- [ ] No changes to existing `Agent.run` signature

## Verification Commands

```
.venv/bin/python -c "from calcifer import Agent; import inspect; assert hasattr(Agent, 'run_sync'), 'run_sync missing'; assert not inspect.iscoroutinefunction(Agent.run_sync), 'run_sync should be sync'"
.venv/bin/python -m pytest tests/ -q -k 'agent_run_sync_basic or agent_run_sync_inside_loop_raises'
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

Must match `features.json` verification array verbatim.

## Rollback Plan

`git revert` is trivial — purely additive method, new tests are independent.
