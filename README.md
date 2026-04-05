# Calcifer

Provider-agnostic Python agent runner that replicates the core mechanisms of Claude Code's Agent Runner for any OpenAI-compatible API.

## What is this

Calcifer is a **library** for building LLM-powered agents that can use tools, manage long conversations, and recover from errors — without being locked to any specific LLM provider.

Point it at OpenAI, a local Ollama instance, vLLM, DeepSeek, or any `/v1/chat/completions` endpoint:

```python
from calcifer import Agent, tool

@tool(name="add", description="Add two numbers")
def add(a: int, b: int) -> str:
    return str(a + b)

agent = Agent(
    base_url="http://localhost:11434/v1",
    api_key="ollama",
    model="llama3",
    tools=[add],
)
result = await agent.run("What is 7 + 8?")
print(result.final_text)
```

## Core Architecture

```
Agent Loop (unified run/run_stream)
  |
  +-- LLM Provider (httpx, retry w/ backoff, retry-after, model fallback)
  +-- Context Manager (6-layer compaction pipeline)
  +-- Tool Orchestrator (streaming execution, concurrency control)
  +-- Session Persistence (save/resume/repair)
  +-- Telemetry (OpenTelemetry spans + metrics)
```

### Agent Loop

Single unified loop powers both `run()` and `run_stream()`. Error recovery cascade:

1. **prompt_too_long** -> reactive compact -> autocompact -> retry
2. **max_output_tokens** -> phase 1: escalate cap (8K->64K); phase 2+: inject resume message
3. **429/529 overload** -> exponential backoff w/ retry-after header -> fallback model

Also: diminishing output detection, stop hooks, abort control, query guard, chain tracking.

### Context Management

Six-layer compaction pipeline keeps conversations within the context window:

| Layer | Mechanism | Trigger |
|-------|-----------|---------|
| 1 | Tool result budget (500K chars cap) | Every turn |
| 2 | Snip (trim oldest messages) | Every turn |
| 3 | Microcompact (clear old tool results) | Every turn |
| 4 | Autocompact (LLM summarization) | 90% threshold |
| 5 | Context collapse (fold tool call regions) | Reactive only |
| 6 | Reactive compact (all layers, aggressive) | API 413 error |

Post-compact restoration re-injects recently read files, invoked skills, and MCP tool lists.

### Tools

8 built-in tools:

| Tool | Capabilities |
|------|-------------|
| **Bash** | Shell execution, timeout, background tasks, read-only detection, security classification |
| **FileRead** | Numbered lines, offset/limit, binary detection |
| **FileWrite** | Create vs update detection, auto read-state tracking |
| **FileEdit** | Fuzzy matching (5 normalization strategies), read-before-edit, staleness check |
| **Glob** | Recursive pattern matching |
| **Grep** | ripgrep integration, -B/-A/-C context lines, output modes, VCS directory exclusion |
| **SkillTool** | Load and execute skills with variable substitution |
| **ToolSearchTool** | Deferred tool discovery with keyword scoring |

Create custom tools with the `@tool` decorator or subclass `Tool` for full control.

### Streaming Tool Execution

Tools start executing as soon as their arguments finish streaming — no waiting for the full LLM response. Concurrency-safe tools run in parallel; mutating tools run serially. Interrupt behavior: read-only tools cancel on abort, mutating tools finish first.

### MCP Integration

Connect to any [Model Context Protocol](https://modelcontextprotocol.io/) server:

```python
from calcifer import CalciferConfig, MCPServerConfig

config = CalciferConfig(
    mcp_servers=[
        MCPServerConfig(name="github", transport="stdio", command="mcp-server-github"),
    ]
)
agent = Agent(config=config)
await agent.connect_mcp_servers()
```

4 transports (stdio, SSE, HTTP, WebSocket), session expiry detection + rebuild, tool schema caching, annotations mapping, 200K content size limiting.

### Skills

Markdown files with YAML frontmatter that extend agent capabilities:

```markdown
---
name: review
description: Review code changes
allowed-tools: [bash, file_read, grep]
user-invocable: true
paths: ["*.py", "*.ts"]
---

Review the code changes in the current branch...
```

Features: conditional activation (path-based), inline/fork execution, variable substitution (`$ARGUMENTS`, `$1..$N`), dynamic discovery, post-compact restoration.

### Multi-Agent Coordination

```python
from calcifer import Coordinator, CoordinatorConfig

coord = Coordinator(config, tools, CoordinatorConfig(max_workers=3))
results = await coord.run_workers([
    ("research", "Find all API endpoints"),
    ("implement", "Add the new endpoint"),
], parallel=True)
```

Workers get isolated contexts, restricted tool sets, and a shared scratchpad directory. Abort propagates from coordinator to all workers.

### Frontends

- **TUI**: Rich-based terminal UI with markdown rendering, tool call display, spinner
- **Web GUI**: FastAPI + SSE chat interface
- **Print mode**: text / json / stream-json output for scripting

## Install

```bash
pip install -e ".[all]"    # everything
pip install -e ".[tui]"    # TUI only
pip install -e ".[web]"    # Web GUI only
pip install -e ".[mcp]"    # MCP support
```

Requires Python >= 3.11.

## Usage

### As a library

```python
from calcifer import Agent

async with Agent(api_key="...", model="gpt-4o") as agent:
    # Non-streaming
    result = await agent.run("Write a hello world program")
    print(result.final_text)

    # Streaming
    async for event in agent.run_stream("Explain this code"):
        if event.type == "text_delta":
            print(event.text, end="")
```

### CLI

```bash
calcifer                         # Interactive TUI
calcifer -p "What is 2+2?"      # Print mode
calcifer --web                   # Web GUI on localhost:8000
calcifer --model gpt-4o-mini     # Model override
```

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

429 mock tests covering agent loop, context management, tool execution, MCP, skills, session recovery, and more.

## License

MIT
