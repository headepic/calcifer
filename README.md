# Calcifer

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Provider-agnostic 的 Python Agent Runner SDK。复刻 Claude Code Agent Runner 的核心机制，
对接任意 OpenAI 兼容 `/v1/chat/completions` endpoint（OpenAI、Ollama、vLLM、DeepSeek 等）。

> Calcifer 不发布到 PyPI，直接从本仓库安装。

## 特性一览

- **统一的 Agent 循环**：`run()` / `run_sync()` / `run_stream()` 三种入口共用同一套 loop
- **错误恢复级联**：`prompt_too_long` → 压缩；`max_output_tokens` → 扩容 / resume；`429 / 529` → 指数退避 + 模型降级
- **9 个内置工具**：Bash、FileRead、FileWrite、FileEdit、Glob、Grep、WebSearch、SkillTool、ToolSearchTool
- **6 层上下文压缩管线**：tool-budget → 裁剪 → 微压缩 → autocompact → fold → 应急
- **MCP 客户端**：支持 stdio / SSE / HTTP / WebSocket 四种 transport，OAuth token refresh，session 过期自动重建
- **Skill 系统**：Markdown + YAML frontmatter，基于 path glob 的条件激活，inline / fork 执行模式
- **多 Agent 协调**：`Coordinator` 并行 worker + 独立上下文 + 共享 scratchpad
- **生命周期 Hooks**：PreToolUse / PostToolUse / Stop / Notify 等
- **Telemetry**：`CostTracker` + OpenTelemetry spans 和 metrics（按需启用）
- **一等公民的测试支持**：`calcifer.testing.MockProvider` + 断言辅助 + `Agent(provider=...)` 注入位
- **类型检查友好**：随包提供 `py.typed`（PEP 561），下游 `mypy` / `pyright` 看到真实类型

## 安装

需要 Python ≥ 3.11。

```bash
# 直接从 GitHub 安装（推荐 pin 到 tag）
pip install "git+https://github.com/headepic/calcifer.git@v0.3.0"

# 或者跟随 main 分支滚动
pip install "git+https://github.com/headepic/calcifer.git@main"

# 可选 extras
pip install "calcifer[mcp] @ git+https://github.com/headepic/calcifer.git@v0.3.0"        # MCP 客户端
pip install "calcifer[telemetry] @ git+https://github.com/headepic/calcifer.git@v0.3.0"  # OpenTelemetry
pip install "calcifer[dev] @ git+https://github.com/headepic/calcifer.git@v0.3.0"        # pytest
```

如果要本地开发，clone 下来以 editable 方式安装：

```bash
git clone https://github.com/headepic/calcifer.git
cd calcifer
pip install -e ".[dev]"
```

## 30 秒上手

```python
import asyncio
from calcifer import Agent

async def main():
    agent = Agent(
        api_key="sk-...",
        base_url="https://api.openai.com/v1",   # 或 http://localhost:11434/v1 等
        model="gpt-4o-mini",
    )
    result = await agent.run("用一句话解释 Python 的 GIL。")
    print(result.final_text)

asyncio.run(main())
```

同步版本：

```python
from calcifer import Agent
result = Agent(api_key="...", model="...").run_sync("hi")
print(result.final_text)
```

如果不传 `base_url`，Calcifer 会读 `OPENAI_BASE_URL` 环境变量，回退到
`https://api.openai.com/v1`。

## 自定义工具

```python
from calcifer import Agent, tool

@tool(name="add", description="两个整数相加")
def add(a: int, b: int) -> str:
    return str(a + b)

agent = Agent(api_key="...", model="...", tools=[add])
result = await agent.run("7 加 8 等于几？")
```

Agent 循环会自动决定何时调用工具、执行工具、把结果注入上下文，然后继续
循环直到模型给出最终回复。

## 流式输出

```python
async for event in agent.run_stream("写一首四行的诗。"):
    if event.type == "text_delta":
        print(event.text, end="", flush=True)
```

事件类型：`text_delta` / `tool_call_delta` / `tool_result` / `turn_start` /
`turn_end` / `usage` / `finish`。

## 离线测试 Agent（无需真实 LLM）

```python
from calcifer import Agent
from calcifer.testing import MockProvider, assert_tool_called

provider = MockProvider(responses=["Hello!"])
agent = Agent(provider=provider, api_key="x", base_url="x", model="mock")

result = await agent.run("hi")
assert result.final_text == "Hello!"
```

`MockProvider` 是一个 duck-typed 的假 `LLMProvider`，通过 `Agent(provider=...)`
注入。支持预置文本响应、预置工具调用响应、多轮队列、耗尽策略（`raise` / `repeat`）。
配套 `assert_tool_called(result, name, args_contains=...)` 和
`assert_message_count(result, count=..., role=...)` 两个断言辅助。

完整用法见 [`docs/testing.md`](docs/testing.md)。

## 接入 MCP server

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
result = await agent.run("列出 /tmp 下的所有 .txt 文件。")
```

支持 4 种 transport（stdio / SSE / HTTP / WebSocket），OAuth token 自动刷新，
session 过期自动重建。需要安装 `[mcp]` extra。

## 架构

```
Agent(run / run_sync / run_stream)
  │
  ├── LLMProvider           OpenAI 兼容 /v1/chat/completions；指数退避；retry-after；
  │                         429/529 降级；非流式响应返回空 content 时自动 fallback
  │                         到流式模式（sticky flag，后续调用直接走流式）
  │
  ├── ContextManager        6 层压缩管线：tool-budget → 裁剪 → 微压缩 → autocompact
  │                         → context-fold → 应急。压缩后自动恢复最近读取的文件 /
  │                         激活的 Skill / MCP 工具列表
  │
  ├── StreamingToolExecutor 工具参数一流式完成就立即启动执行；concurrency_safe 的
  │                         工具并行，写入型工具串行；只读工具可中断，写入必须跑完
  │
  ├── 内置工具              Bash、FileRead、FileWrite、FileEdit、Glob、Grep、
  │                         WebSearch、SkillTool、ToolSearchTool
  │
  ├── MCP 客户端            stdio/SSE/HTTP/WS 四种 transport，OAuth refresh，
  │                         工具 schema 缓存，单次内容 200K 上限
  │
  ├── Skill 系统            Markdown + YAML frontmatter，基于 path glob 的条件激活，
  │                         inline / fork 两种执行模式，变量替换（$ARGUMENTS、$1..$N）
  │
  ├── Coordinator           多 agent 编排：worker 拥有独立 context、受限工具集、
  │                         共享 scratchpad 目录
  │
  ├── HookManager           PreToolUse / PostToolUse / Stop / Notify 生命周期 hook
  │
  ├── SessionStorage        对话 transcript 存盘，支持 resume 和崩溃恢复
  │
  └── Telemetry             CostTracker + OpenTelemetry spans 和 metrics（opt-in）
```

## 示例

| 文件 | 说明 | 需要真实 LLM |
|---|---|---|
| [`examples/01_hello.py`](examples/01_hello.py) | 最小的 Agent 调用 | ✅ |
| [`examples/02_tool.py`](examples/02_tool.py) | `@tool` 装饰器 + 多步工具调用 | ✅ |
| [`examples/03_stream.py`](examples/03_stream.py) | `run_stream()` 流式输出 | ✅ |
| [`examples/04_testing.py`](examples/04_testing.py) | `MockProvider` 离线测试 | ❌ |
| [`examples/05_mcp.py`](examples/05_mcp.py) | 接入 MCP filesystem server | ✅ + Node.js |

需要真实 LLM 的示例通过环境变量配置：

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.openai.com/v1   # 可选
export OPENAI_MODEL=gpt-4o-mini                    # 可选
python examples/01_hello.py
```

把 `OPENAI_BASE_URL` 指向 Ollama、vLLM 或其他 OpenAI 兼容服务即可切换后端。

## 测试

```bash
pip install -e ".[dev]"
pytest tests/ -q \
  --ignore=tests/test_e2e_real.py \
  --ignore=tests/test_e2e_mcp_skill.py
```

Mock 测试覆盖 agent 循环、上下文压缩管线、工具编排、MCP 客户端、
Skill 系统、Hook、Coordinator、side query、testing 模块、流式 fallback 等。
E2E 测试需要真实 LLM endpoint，默认排除。

## 文档

- [`docs/public-api.md`](docs/public-api.md) — 公开 API 表面和稳定性等级
- [`docs/testing.md`](docs/testing.md) — `calcifer.testing` 模块使用指南
- [`examples/`](examples/) — 可运行的 cookbook 示例

## 应用（`apps/`）

`apps/` 目录下是基于 Calcifer SDK 构建的独立应用。它们和 SDK 物理隔离：
各自有 `pyproject.toml`，各自 `pip install -e`，SDK 安装时不会拉入。

- [`apps/ask/`](apps/ask/) — 一次性代码库问答 CLI，`ask` 命令
- [`apps/chatbot/`](apps/chatbot/) — 可复用会话对象 + 交互式 chatbot，`calcifer-chatbot` 命令

装 SDK + chatbot 的完整流程：

```bash
pip install -e .             # 装 SDK 本体
pip install -e apps/chatbot  # 装 chatbot（会在 venv 里找到已装好的 calcifer）
calcifer-chatbot
```

## 版本管理

Calcifer 不发布到 PyPI，版本通过本仓库的 git tag 管理。当前 baseline：**`v0.3.0`**。

在你的消费项目里 pin 到 tag：

```bash
pip install "git+https://github.com/headepic/calcifer.git@v0.3.0"
```

## 项目定位

这是一个单人维护的项目，面向 Claude Code 核心机制的 Python 复刻，在开源模式下
自用为主。Issue 和 PR 欢迎，但请理解维护带宽有限，且项目范围是有意保持收敛的：
贴近 Claude Code 的核心机制，只对接 OpenAI 兼容 API。

**明确不做**（有意为之的 non-goals）：

- Anthropic 专属特性（`cache_control`、beta headers、prompt caching）
- 把 Anthropic SDK 作为依赖
- 多 provider 抽象层（需要 20+ provider 请用 `pi-ai` 或 `litellm`）
- 内置 Web UI / CLI（核心包是库，不捆绑应用）
- Tool permission / sandbox 系统

## License

MIT — 见 [LICENSE](LICENSE)。
