# Calcifer

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Provider-agnostic Python Agent Runner SDK. Mirrors the core mechanisms of
Claude Code's agent runner, targeting any OpenAI-compatible
`/v1/chat/completions` endpoint (OpenAI, Ollama, vLLM, DeepSeek, …).

> Calcifer is not published to PyPI — install directly from this repo.

## Highlights

- **Unified agent loop** with `run()` / `run_sync()` / `run_stream()`
- **Error recovery cascade** — `prompt_too_long` → compact; `max_output_tokens` → token expand / resume; `429 / 529` → exponential backoff + model fallback
- **8 built-in tools** — Bash, FileRead/Write/Edit, Glob, Grep, SkillTool, ToolSearchTool
- **6-layer context compaction pipeline** — tool-budget → trim → micro-compact → autocompact → fold → emergency
- **MCP client** — stdio / SSE / HTTP / WebSocket transports, OAuth refresh, session rebuild
- **Skill system** — Markdown + YAML frontmatter, conditional activation, inline / fork execution
- **Multi-agent coordination** via `Coordinator`
- **Lifecycle hooks** — PreToolUse / PostToolUse / Stop / …
- **Telemetry** — `CostTracker` + OpenTelemetry spans & metrics (opt-in)
- **First-class testing** — `calcifer.testing.MockProvider` + assertion helpers, `Agent(provider=...)` injection seam
- **Type-checked** — ships `py.typed` (PEP 561), downstream `mypy` / `pyright` see real types

## Install

Requires Python ≥ 3.11.

```bash
# Install directly from GitHub (recommended: pin to a tag)
pip install "git+https://github.com/headepic/calcifer.git@v0.3.0"

# Or follow main
pip install "git+https://github.com/headepic/calcifer.git@main"

# Optional extras
pip install "calcifer[mcp] @ git+https://github.com/headepic/calcifer.git@v0.3.0"        # MCP client
pip install "calcifer[telemetry] @ git+https://github.com/headepic/calcifer.git@v0.3.0"  # OpenTelemetry
pip install "calcifer[dev] @ git+https://github.com/headepic/calcifer.git@v0.3.0"        # pytest
```

For local development, clone and install editable:

```bash
git clone https://github.com/headepic/calcifer.git
cd calcifer
pip install -e ".[dev]"
```

## 30-second hello world

```python
import asyncio
from calcifer import Agent

async def main():
    agent = Agent(
        api_key="sk-...",
        base_url="https://api.openai.com/v1",   # or http://localhost:11434/v1, etc.
        model="gpt-4o-mini",
    )
    result = await agent.run("Explain the Python GIL in one sentence.")
    print(result.final_text)

asyncio.run(main())
```

Or the synchronous variant:

```python
from calcifer import Agent
result = Agent(api_key="...", model="...").run_sync("hi")
print(result.final_text)
```

If `base_url` is omitted, Calcifer reads `OPENAI_BASE_URL` from the
environment and falls back to `https://api.openai.com/v1`.

## Custom tools

```python
from calcifer import Agent, tool

@tool(name="add", description="Add two integers")
def add(a: int, b: int) -> str:
    return str(a + b)

agent = Agent(api_key="...", model="...", tools=[add])
result = await agent.run("What is 7 plus 8?")
```

The agent loop automatically decides when to call the tool, executes it,
feeds the result back into the conversation, and continues until the model
produces a final answer.

## Streaming

```python
async for event in agent.run_stream("Write a four-line poem."):
    if event.type == "text_delta":
        print(event.text, end="", flush=True)
```

Event types: `text_delta` / `tool_call_delta` / `tool_result` / `turn_start` /
`turn_end` / `usage` / `finish`.

## Testing agents offline

```python
from calcifer import Agent
from calcifer.testing import MockProvider, assert_tool_called

provider = MockProvider(responses=["Hello!"])
agent = Agent(provider=provider, api_key="x", base_url="x", model="mock")

result = await agent.run("hi")
assert result.final_text == "Hello!"
```

`MockProvider` is a duck-typed fake `LLMProvider` injected via `Agent(provider=...)`.
It supports canned text responses, canned tool-call responses, multi-turn
queues, and exhaustion policies (`raise` / `repeat`). It ships with
`assert_tool_called(result, name, args_contains=...)` and
`assert_message_count(result, count=..., role=...)`.

See [`docs/testing.md`](docs/testing.md) for the full guide.

## Connecting an MCP server

```python
from calcifer import Agent, CalciferConfig, MCPServerConfig

config = CalciferConfig(
    api_key="...",
    model="...",
    mcp_servers=[
        MCPServerConfig(
            name="fs",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        ),
    ],
)
agent = Agent(config=config)
await agent.connect_mcp_servers()
result = await agent.run("List the .txt files under /tmp.")
```

Four transports supported (stdio / SSE / HTTP / WebSocket), with OAuth token
refresh and session rebuild on expiry. Install with the `[mcp]` extra.

## Architecture

```
Agent(run / run_sync / run_stream)
  │
  ├── LLMProvider           OpenAI-compatible /v1/chat/completions; exponential
  │                         backoff; retry-after; 429/529 fallback; transparent
  │                         streaming fallback when an endpoint returns
  │                         empty content in non-streaming mode
  │
  ├── ContextManager        6-layer pipeline: tool-budget → trim → micro-compact
  │                         → autocompact → context-fold → emergency. Recovers
  │                         recently-read files / active Skills / MCP tools
  │                         after compaction
  │
  ├── StreamingToolExecutor Launches tools as soon as their arguments are fully
  │                         streamed; concurrency-safe tools run in parallel,
  │                         write tools run serially; read-only tools are
  │                         interruptible, writes must finish
  │
  ├── Built-in tools        Bash, FileRead, FileWrite, FileEdit, Glob, Grep,
  │                         SkillTool, ToolSearchTool
  │
  ├── MCP client            stdio/SSE/HTTP/WS transport, OAuth refresh, tool
  │                         schema caching, 200 K content cap
  │
  ├── Skill system          Markdown + YAML frontmatter, conditional activation
  │                         by path globs, inline / fork execution, variable
  │                         substitution ($ARGUMENTS, $1..$N)
  │
  ├── Coordinator           Multi-agent orchestration with isolated contexts,
  │                         restricted tool sets, shared scratchpad
  │
  ├── HookManager           PreToolUse / PostToolUse / Stop / Notify lifecycle
  │                         hooks
  │
  ├── SessionStorage        Disk-backed transcripts with resume / crash recovery
  │
  └── Telemetry             CostTracker, OpenTelemetry spans & metrics (opt-in)
```

## Examples

| File | Description | Requires live LLM |
|---|---|---|
| [`examples/01_hello.py`](examples/01_hello.py) | Minimal agent call | ✅ |
| [`examples/02_tool.py`](examples/02_tool.py) | `@tool` decorator + multi-step tool use | ✅ |
| [`examples/03_stream.py`](examples/03_stream.py) | `run_stream()` streaming output | ✅ |
| [`examples/04_testing.py`](examples/04_testing.py) | `MockProvider` offline testing | ❌ |
| [`examples/05_mcp.py`](examples/05_mcp.py) | Connecting to an MCP filesystem server | ✅ + Node.js |

Examples that hit a real LLM read configuration from environment variables:

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.openai.com/v1   # optional
export OPENAI_MODEL=gpt-4o-mini                    # optional
python examples/01_hello.py
```

Point `OPENAI_BASE_URL` at Ollama, vLLM, or any other OpenAI-compatible
server to use it instead.

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -q \
  --ignore=tests/test_e2e_real.py \
  --ignore=tests/test_e2e_mcp_skill.py
```

426 mock tests cover the agent loop, context pipeline, tool orchestration,
MCP client, Skill system, hooks, Coordinator, side query, testing module,
and streaming fallback. E2E tests hit real LLM endpoints and are excluded
by default.

## Documentation

- [`docs/public-api.md`](docs/public-api.md) — public API surface and stability tiers
- [`docs/testing.md`](docs/testing.md) — `calcifer.testing` module guide
- [`examples/`](examples/) — runnable cookbook examples

## Versioning

Calcifer is not published to PyPI. Versioning is managed through git tags
in this repository. Current baseline: **`v0.3.0`**.

Pin to a tag in your consumer project:

```bash
pip install "git+https://github.com/headepic/calcifer.git@v0.3.0"
```

## Status

Single-maintainer project, developed in the open for self-use. Issues and
pull requests are welcome, but please understand that review bandwidth is
limited and the scope is intentionally narrow: stay close to Claude Code's
core mechanisms, target OpenAI-compatible APIs only.

**Non-goals** (intentional omissions):

- Anthropic-proprietary features (`cache_control`, beta headers, prompt caching)
- Anthropic SDK as a dependency
- Multi-provider abstraction (use `pi-ai` or `litellm` if you need 20+ providers)
- Built-in TUI / web UI / CLI (this is a library, not an application)
- Tool permission / sandboxing system

## License

MIT — see [LICENSE](LICENSE).
