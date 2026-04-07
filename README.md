# Calcifer

Provider-agnostic Python agent runner SDK. 复刻了 Claude Code Agent Runner 的核心机制，
面向任意 OpenAI 兼容 API（OpenAI、Ollama、vLLM、DeepSeek、…）。

> **自用 SDK** — 不发布到 PyPI。通过 `pip install -e` 接入你的项目。

## 安装

```bash
pip install -e /path/to/calcifer-sdk

# 可选 extras
pip install -e "/path/to/calcifer-sdk[mcp]"        # MCP client
pip install -e "/path/to/calcifer-sdk[telemetry]"  # OpenTelemetry
pip install -e "/path/to/calcifer-sdk[tui,web]"    # 旧的 TUI/Web 前端（非 SDK 必须）
pip install -e "/path/to/calcifer-sdk[dev]"        # pytest
```

需要 Python ≥3.11。

## Hello World

```python
import asyncio
from calcifer import Agent

async def main():
    agent = Agent(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="llama3",
    )
    result = await agent.run("用一句话解释 Python 的 GIL")
    print(result.final_text)

asyncio.run(main())
```

或同步版本：

```python
from calcifer import Agent

agent = Agent(base_url="...", api_key="...", model="...")
result = agent.run_sync("hi")
print(result.final_text)
```

## 自定义 Tool

```python
from calcifer import Agent, tool

@tool(name="add", description="两数相加")
def add(a: int, b: int) -> str:
    return str(a + b)

agent = Agent(base_url="...", api_key="...", model="...", tools=[add])
result = await agent.run("7 加 8 等于几？")
```

## 流式输出

```python
async for event in agent.run_stream("写首诗"):
    if event.type == "text_delta":
        print(event.text, end="", flush=True)
```

## 测试 (无需真实 LLM)

```python
from calcifer import Agent
from calcifer.testing import MockProvider, assert_tool_called

provider = MockProvider(responses=["hello!"])
agent = Agent(provider=provider, model="mock", api_key="x", base_url="x")
result = await agent.run("hi")
assert result.final_text == "hello!"
```

完整用法见 [`docs/testing.md`](docs/testing.md)。

## 文档

- [`docs/public-api.md`](docs/public-api.md) — 公开 API 表面、稳定性等级
- [`docs/testing.md`](docs/testing.md) — `calcifer.testing` 模块用法
- [`examples/`](examples/) — 端到端 cookbook 示例

## 包含的能力

- **Agent runner**：`run()` / `run_sync()` / `run_stream()`，错误恢复级联（prompt_too_long、max_output_tokens、429/529、模型降级）
- **8 个内置 Tool**：Bash、FileRead、FileWrite、FileEdit、Glob、Grep、Skill、ToolSearch
- **6 层上下文压缩**：tool budget → 裁剪 → 微压缩 → autocompact → fold → 应急
- **MCP client**：4 种 transport (stdio/SSE/HTTP/WebSocket)，OAuth refresh，session 重建
- **Skill 系统**：Markdown + YAML frontmatter，条件激活，inline/fork 模式
- **多 Agent 协调**：`Coordinator`，并行 worker，scratchpad 共享
- **Hooks**：lifecycle hooks (PreToolUse / PostToolUse / Stop / …)
- **Telemetry**：CostTracker、OpenTelemetry spans + metrics
- **Testing utilities**：`MockProvider` + assertion helpers

## 测试

```bash
pip install -e ".[dev]"
.venv/bin/python -m pytest tests/ -q \
  --ignore=tests/test_e2e_real.py \
  --ignore=tests/test_e2e_mcp_skill.py \
  --ignore=tests/test_tui_web.py
```

458 mock 测试。E2E 和 TUI 测试默认排除。

## 许可证

MIT
