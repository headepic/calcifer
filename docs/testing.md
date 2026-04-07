# Testing with calcifer

Calcifer ships a small testing submodule (`calcifer.testing`) so
downstream users can test their agents without hitting a real LLM.

## What's in the box

```python
from calcifer.testing import MockProvider, assert_tool_called, assert_message_count
```

- **`MockProvider(responses, *, exhausted="raise"|"repeat")`** —
  a drop-in replacement for `LLMProvider` that pops responses off a
  queue in order. Inject it via the `Agent(provider=...)` kwarg.
- **`assert_tool_called(result, name, *, args_contains=None)`** —
  asserts the agent called a specific tool, optionally with a
  subset-matching check on the arguments.
- **`assert_message_count(result, *, count, role=None)`** —
  asserts the message count, optionally filtered by role.

`calcifer.testing` is public but deliberately NOT exported from
`calcifer.__all__`. Import it by its full path.

## Basic text response

```python
import pytest
from calcifer import Agent, CalciferConfig
from calcifer.testing import MockProvider


@pytest.mark.asyncio
async def test_my_agent_says_hi():
    provider = MockProvider(["Hello from the mock!"])
    agent = Agent(config=CalciferConfig(api_key="test"), provider=provider)

    result = await agent.run("hi")

    assert result.final_text == "Hello from the mock!"
```

## Multi-turn with a tool call

Queue a tool-call dict followed by the final text response. The
agent runs the real tool, loops back, and finalizes:

```python
from calcifer import tool


@tool(name="add", description="Add two numbers")
def add(a: int, b: int) -> str:
    return str(a + b)


@pytest.mark.asyncio
async def test_my_agent_adds():
    provider = MockProvider([
        {"tool_calls": [{"name": "add", "arguments": {"a": 1, "b": 2}}]},
        "The answer is 3.",
    ])
    agent = Agent(
        config=CalciferConfig(api_key="test"),
        tools=[add],
        provider=provider,
    )

    result = await agent.run("what is 1 + 2?")

    assert result.final_text == "The answer is 3."
    assert_tool_called(result, "add", args_contains={"a": 1, "b": 2})
```

## Exhaustion policy

By default `MockProvider` raises `RuntimeError` when the queue is
empty — this catches "I forgot to queue the next response" bugs
loudly. If you're testing a scenario where the agent should keep
looping on the same response, use `exhausted="repeat"`:

```python
provider = MockProvider(["stuck"], exhausted="repeat")
```

## Inspecting what the agent asked for

`MockProvider` records every call:

```python
provider = MockProvider(["ok"])
agent = Agent(config=CalciferConfig(api_key="test"), provider=provider)
await agent.run("hi")

assert len(provider.calls) == 1
assert provider.calls[0]["method"] == "chat_completion"
# provider.calls[0]["messages"] is the message list that was sent
```
