# Feature Contract: sdk-testing-module

## Motivation

Right now every test that exercises `Agent` has to mock the LLM
provider by hand:

```python
with patch.object(agent._provider, "chat_completion", new_callable=AsyncMock) as mock_chat:
    mock_chat.return_value = (Message(role="assistant", content="..."), Usage(...))
    result = await agent.run("hi")
```

This shows up 5+ times in `tests/test_p0.py` alone, ~10 lines of
boilerplate each time. Anybody using calcifer as an SDK downstream
hits the same problem and has to invent the same workaround.

The fix is a first-class `calcifer/testing/` subpackage that
provides a drop-in `MockProvider` matching `LLMProvider`'s public
interface plus a couple of assertion helpers. A test goes from 10
lines of mock setup to:

```python
from calcifer.testing import MockProvider, assert_tool_called

agent = Agent(config=CalciferConfig(api_key="test"), provider=MockProvider([
    {"tool_calls": [{"name": "add", "arguments": {"a": 1, "b": 2}}]},
    "The answer is 3.",
]))
result = await agent.run("what is 1+2?")
assert result.final_text == "The answer is 3."
assert_tool_called(result, "add")
```

Note: this feature depends on `Agent.__init__` accepting a
`provider=` keyword so a mock can be injected. Current `Agent`
constructs its own `LLMProvider` internally — the contract has to
add that injection seam.

## Claude Code Reference

No direct analog — Claude Code is a TypeScript app, not a Python
SDK. This is standard Python SDK practice: `pytest` fixtures,
`fastapi.testclient`, Anthropic's `Anthropic(test_mode=True)`,
etc. all follow the same pattern of exposing a dedicated testing
surface.

## Scope

### 要做

- **Add `provider=` injection seam to `Agent.__init__`**. Currently
  `Agent.__init__` builds an `LLMProvider` from the resolved
  config; add an optional `provider: LLMProvider | None = None`
  parameter that, when provided, bypasses the internal
  construction entirely. Must not affect any existing call site
  (all existing tests still pass without changes).
- **Create `calcifer/testing/__init__.py`** exporting:
  - `MockProvider` — an `LLMProvider`-compatible class
  - `assert_tool_called` — assertion helper
  - `assert_message_count` — assertion helper
  - The module's own `__all__` listing these three names
- **Create `calcifer/testing/mock_provider.py`** with `MockProvider`:
  - Constructor: `MockProvider(responses: list[str | dict | Message], *, exhausted: Literal["raise", "repeat"] = "raise")`
  - Each response in the list can be:
    - `str` — becomes a `Message(role="assistant", content=<str>)`
    - `dict` with optional `text`, `tool_calls` keys — becomes a
      `Message(role="assistant", content=text, tool_calls=[...])`.
      `tool_calls` is a list of `{"name": str, "arguments":
      dict|str, "id": str|None}` entries (arguments dict is
      json-encoded into `ToolCall.arguments` for compatibility
      with the real ToolCall shape)
    - `Message` — passed through verbatim
  - `async def chat_completion(self, messages, tools=None,
    model_override=None, max_tokens_override=None) -> tuple[Message, Usage]`
    — pops the next queued response, returns it with a synthetic
    `Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2)`.
  - `async def chat_completion_stream(self, messages, tools=None, ...)
    -> AsyncIterator[StreamEvent]` — yields a minimal event
    sequence (`text_delta` for each character OR one `text_delta`
    with the full text, plus a `finish` event with
    `finish_reason="stop"`, plus a `usage` event). For tool calls:
    one `tool_call_delta` per tool call followed by
    `finish(finish_reason="tool_calls")`. Keep the stream
    implementation minimal — just enough for `run_stream()` to
    terminate cleanly.
  - When the response list is exhausted: if
    `exhausted="raise"`, raise `RuntimeError("MockProvider
    exhausted after N responses")`; if `"repeat"`, re-return the
    last response indefinitely.
  - Record every call in `self.calls: list[dict]` so tests can
    inspect what the agent asked for (used by future fixtures).
  - Does **not** subclass `LLMProvider` — duck-typed is fine and
    avoids pulling in the real provider's `httpx` client machinery.
    The only contract is "has `chat_completion` and
    `chat_completion_stream` methods with matching signatures."
- **Create `calcifer/testing/assertions.py`** with:
  - `def assert_tool_called(result: AgentResult, tool_name: str, *, args_contains: dict | None = None) -> None`
    — walks `result.messages` for assistant tool calls matching
    `tool_name`. If `args_contains` is provided, each key/value
    must appear in the parsed JSON arguments. Raises
    `AssertionError` with a helpful diff on failure (lists what
    tool calls WERE made so the user can fix their test fast).
  - `def assert_message_count(result: AgentResult, *, role: str | None = None, count: int) -> None`
    — counts messages in `result.messages` optionally filtered by
    role, raises `AssertionError` on mismatch with a readable
    summary.
- **Add `tests/test_testing_module.py`** with these tests:
  1. `test_mock_provider_basic_text_response` — queue one string,
     agent returns it
  2. `test_mock_provider_multi_turn_tool_call` — queue a tool-call
     dict followed by a text response, agent runs the tool, loops
     back, returns the final text
  3. `test_mock_provider_exhausted_raises` — default behavior
  4. `test_mock_provider_exhausted_repeats` — `exhausted="repeat"`
     keeps returning the last response
  5. `test_assert_tool_called_passes` — happy path
  6. `test_assert_tool_called_fails_with_useful_message` — asserts
     the error message lists the tool calls that WERE made
  7. `test_assert_tool_called_args_contains` — subset match
  8. `test_assert_message_count_happy` + `_fail` — both paths
  9. `test_agent_accepts_provider_injection` — verifies the new
     `provider=` parameter on `Agent.__init__` actually routes
     through the injected provider (not the default one)
- **Add a "Testing utilities" subsection to `docs/public-api.md`**
  — one short paragraph noting `calcifer.testing` exists as a
  public submodule with `MockProvider`, `assert_tool_called`,
  `assert_message_count`, and is intentionally NOT in
  `calcifer.__all__`. Keeps the canonical surface doc truthful.
- **Add `docs/testing.md`** — minimal (~40 lines): "here's how to
  use `calcifer.testing` in your own tests", one code example
  for basic text, one for tool calls, one for assertion helpers.
  Not a Sphinx doc, just a Markdown cookbook page.

### 不做 (non-goals)

- **Not adding `calcifer.testing` to the top-level
  `calcifer.__all__`**. Testing utilities live under
  `calcifer.testing.*` namespace, imported as
  `from calcifer.testing import MockProvider`. Adding to
  top-level `__all__` would pollute the main import surface and
  require coordinating edits in `docs/public-api.md` +
  `tests/test_packaging.py::_EXPECTED_PUBLIC_API`. The submodule
  has its own `__all__`.
- **Not implementing a full stream mock with realistic chunking**.
  The stream implementation is the minimal event sequence that
  makes `run_stream` terminate without errors. Users who want to
  test partial-delta handling can still `patch` directly.
- **Not implementing fixture wiring for pytest**
  (`@pytest.fixture`-decorated helpers). `MockProvider` is
  constructor-based and can be used with or without pytest; a
  fixtures layer is additive and can come in a follow-up.
- **Not modifying `LLMProvider` itself**. `MockProvider` is duck-
  typed; `LLMProvider` stays unchanged.
- **Not mocking `Coordinator` or MCP servers**. Those are their
  own worlds; this feature is scoped to the single-agent
  `LLMProvider` seam only.
- **Not adding a `calcifer.testing` classifier / keyword to
  `pyproject.toml`**. The submodule ships with the main package.

## Design

### File layout

```
calcifer/testing/
  __init__.py          # re-exports + __all__
  mock_provider.py     # MockProvider class
  assertions.py        # assert_tool_called, assert_message_count

tests/test_testing_module.py    # 9 tests
docs/testing.md                 # usage doc
```

### `Agent.__init__` change (minimal)

Current signature (roughly):
```python
def __init__(self, config=None, *, api_key=None, base_url=None,
             model=None, tools=None, ...):
    ...
    self._provider = LLMProvider(api_key=..., base_url=...)
```

Change: add `provider=None` kwarg. If provided, use it directly;
otherwise construct `LLMProvider` as today. The resolved
`self._config.base_url = _resolve_base_url(...)` logic still runs
either way (for consistency). One new line plus one branch.

Existing call sites all pass `config=...` or `api_key=...` — none
pass `provider=...`, so this is purely additive.

### `MockProvider.chat_completion` sketch

```python
async def chat_completion(self, messages, tools=None, model_override=None, max_tokens_override=None):
    self.calls.append({"method": "chat_completion", "messages": list(messages), "tools": tools})
    msg = self._next_response()
    return msg, Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2)
```

`_next_response()` handles the `responses` cursor and the
`exhausted` policy. Normalization (str/dict/Message → Message) is
done lazily on each call, not eagerly in `__init__`.

### `assert_tool_called` error message shape

On failure:
```
AssertionError: expected tool call 'bash' not found in AgentResult.
Tool calls observed:
  1. add(a=1, b=2)
  2. read_file(path='/tmp/x')
```

For `args_contains`:
```
AssertionError: tool 'add' was called but no call had args containing {'a': 99}.
Observed call args:
  1. {'a': 1, 'b': 2}
  2. {'a': 3, 'b': 4}
```

Helpful errors > terse ones. This is a testing tool; its failure
messages are its UX.

### `tests/test_testing_module.py` imports

```python
from calcifer import Agent, CalciferConfig
from calcifer.testing import MockProvider, assert_tool_called, assert_message_count
```

The `from calcifer.testing import ...` import is the thing being
smoke-tested by the file's mere existence.

## Acceptance Criteria

- [ ] `calcifer/testing/__init__.py` exists and exports
      `MockProvider`, `assert_tool_called`, `assert_message_count`
      (all three importable via `from calcifer.testing import ...`)
- [ ] `calcifer/testing/__init__.py` has an `__all__` listing
      exactly those three names
- [ ] `Agent.__init__` accepts a `provider=` keyword argument
      (type `LLMProvider | None`, default `None`)
- [ ] `MockProvider` has `chat_completion` and
      `chat_completion_stream` methods matching `LLMProvider`'s
      signature (duck-typed, not subclass)
- [ ] `MockProvider` records every call in `self.calls`
- [ ] `MockProvider` with `exhausted="raise"` (default) raises
      `RuntimeError` when the response queue is empty
- [ ] `MockProvider` with `exhausted="repeat"` re-returns the last
      response after exhaustion
- [ ] `assert_tool_called` raises `AssertionError` with a message
      that lists the tool calls that WERE observed when the
      expected call is missing
- [ ] `assert_tool_called` supports `args_contains=` subset match
- [ ] `assert_message_count` raises `AssertionError` on mismatch
      and supports optional `role=` filter
- [ ] All 9 new tests in `tests/test_testing_module.py` pass
- [ ] `docs/testing.md` exists and shows at least one example
      using `MockProvider` and one using `assert_tool_called`
- [ ] `calcifer.__all__` is **unchanged** (testing module does NOT
      get added — still 31 names)
- [ ] All existing mock tests still pass (currently 482, expect
      482 + 9 = 491 after this feature)
- [ ] `docs/public-api.md` gains a one-line mention of
      `calcifer.testing` under a new "Testing utilities" subsection,
      clarifying it's a public submodule (not in `__all__` by
      design) so readers scanning the canonical surface doc learn
      the namespace exists

## Verification Commands

```
.venv/bin/python -c "from calcifer.testing import MockProvider, assert_tool_called, assert_message_count; from calcifer.testing import __all__; assert set(__all__) == {'MockProvider','assert_tool_called','assert_message_count'}, f'testing __all__ mismatch: {__all__}'"
.venv/bin/python -c "import inspect; from calcifer import Agent; sig = inspect.signature(Agent.__init__); assert 'provider' in sig.parameters, f'Agent.__init__ missing provider kwarg: {list(sig.parameters.keys())}'"
.venv/bin/python -c "import calcifer; assert len(calcifer.__all__) == 31, f'calcifer.__all__ drifted: {len(calcifer.__all__)} names'"
.venv/bin/python -c "from pathlib import Path; p=Path('docs/testing.md'); assert p.exists(), 'docs/testing.md missing'; t=p.read_text(); assert 'MockProvider' in t and 'assert_tool_called' in t, 'docs/testing.md missing core examples'"
.venv/bin/python -m pytest tests/test_testing_module.py -q
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

Must match `features.json` verification array verbatim.

## Rollback Plan

`git revert` removes the new `calcifer/testing/` subpackage, the
new test file, and `docs/testing.md`. The `Agent.__init__`
`provider=` kwarg is purely additive — removing it affects no
existing call site (verified because all existing tests pass
without the kwarg). No other runtime code changes.
