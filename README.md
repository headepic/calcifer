# Calcifer

Provider-agnostic Python agent runner SDK，复刻 Claude Code Agent Runner 的核心机制，
对接任意 OpenAI 兼容 `/v1/chat/completions` endpoint（OpenAI、Ollama、vLLM、DeepSeek、…）。

> **自用 SDK** — 不发布到 PyPI，通过 `pip install -e` 或 git URL 直接装进你的项目。

## 安装

```bash
# 本地 editable 安装（最常用）
pip install -e /Users/jowang/Documents/github/calcifer

# 或者从 git 装并 pin 到 tag
pip install "git+file:///Users/jowang/Documents/github/calcifer@v0.3.0"

# 可选 extras
pip install -e "/Users/jowang/Documents/github/calcifer[mcp]"        # MCP client
pip install -e "/Users/jowang/Documents/github/calcifer[telemetry]"  # OpenTelemetry
pip install -e "/Users/jowang/Documents/github/calcifer[dev]"        # pytest + pytest-asyncio
```

要求 Python ≥ 3.11。

## 30 秒上手

```python
import asyncio
from calcifer import Agent

async def main():
    agent = Agent(
        api_key="sk-...",
        base_url="https://api.openai.com/v1",  # 或 http://localhost:11434/v1 等
        model="gpt-4o-mini",
    )
    result = await agent.run("用一句话解释 Python GIL")
    print(result.final_text)

asyncio.run(main())
```

同步版本：

```python
from calcifer import Agent
result = Agent(api_key="...", model="...").run_sync("hi")
```

环境变量自动解析（如果 `base_url=None`）：`OPENAI_BASE_URL` → 回退到 `https://api.openai.com/v1`。

## 自定义 Tool

```python
from calcifer import Agent, tool

@tool(name="add", description="Add two integers")
def add(a: int, b: int) -> str:
    return str(a + b)

agent = Agent(api_key="...", model="...", tools=[add])
result = await agent.run("7 加 8 等于几？")
```

Tool 会被 agent loop 自动调度：LLM 决定何时调用，Calcifer 执行工具、把结果注入对话、继续循环直到模型给出最终回复。

## 流式输出

```python
async for event in agent.run_stream("写首四行诗"):
    if event.type == "text_delta":
        print(event.text, end="", flush=True)
```

事件类型：`text_delta` / `tool_call_delta` / `tool_result` / `turn_start` / `turn_end` / `usage` / `finish`。

## 离线测试（无需真实 LLM）

```python
from calcifer import Agent
from calcifer.testing import MockProvider, assert_tool_called

provider = MockProvider(responses=["Hello!"])
agent = Agent(provider=provider, api_key="x", base_url="x", model="mock")

result = await agent.run("hi")
assert result.final_text == "Hello!"
```

`MockProvider` 是 duck-typed 的假 `LLMProvider`，通过 `Agent(provider=...)` 注入。
支持 canned text 响应、canned tool call 响应、多轮队列、耗尽策略（`raise` / `repeat`）。
配套 `assert_tool_called(result, name, args_contains=...)` 和 `assert_message_count(result, count=..., role=...)`。

完整用法见 [`docs/testing.md`](docs/testing.md)。

## 连接 MCP server

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
result = await agent.run("列出 /tmp 下的 .txt 文件")
```

支持 4 种 transport（stdio / SSE / HTTP / WebSocket），OAuth token refresh，session 过期重建。
安装时加 `[mcp]` extra。

## 架构

```
Agent(run / run_sync / run_stream)
  │
  ├── LLMProvider          OpenAI 兼容 /v1/chat/completions，指数退避，retry-after，
  │                        429/529 降级，非流式空响应 → 流式 fallback（sticky flag）
  │
  ├── ContextManager       6 层压缩 pipeline：tool budget → 裁剪 → 微压缩 → autocompact
  │                        → context fold → 应急压缩；压缩后自动恢复文件/Skill/MCP tools
  │
  ├── StreamingToolExecutor 流式 tool 参数一到齐就启动执行；concurrency_safe 工具并行，
  │                        写型工具串行；只读可中断，写型必须跑完
  │
  ├── Built-in tools        Bash / FileRead / FileWrite / FileEdit / Glob / Grep /
  │                        SkillTool / ToolSearchTool
  │
  ├── MCP client            stdio/SSE/HTTP/WS transport，OAuth refresh，tool schema 缓存
  │
  ├── Skill system          Markdown + YAML frontmatter，条件激活（paths glob），
  │                        inline / fork 执行模式，`$ARGUMENTS`/`$1..$N` 变量替换
  │
  ├── Coordinator           多 agent 并行协调，worker 独立 context + 受限 tool 集
  │
  ├── HookManager           PreToolUse / PostToolUse / Stop / … lifecycle hooks
  │
  ├── SessionStorage        会话 transcript 存盘，支持 resume / crash recovery
  │
  └── Telemetry             CostTracker + OpenTelemetry spans + metrics（opt-in）
```

错误恢复级联（Agent loop 内部自动处理）：

1. `prompt_too_long` → reactive compact → autocompact → 重试
2. `max_output_tokens` → 第 1 阶段升 token 上限（8K → 64K）；第 2+ 阶段注入 resume 消息
3. `429 / 529` → 指数退避 + retry-after → 模型降级

## 文档

- [`docs/public-api.md`](docs/public-api.md) — 公开 API 表面和稳定性等级
- [`docs/testing.md`](docs/testing.md) — `calcifer.testing` 用法
- [`examples/`](examples/) — 5 个 cookbook 示例（`01_hello` → `05_mcp`）
- `CLAUDE.md` — 给 Claude Code 的项目约束

## Examples

| 文件 | 说明 | 需要真实 LLM |
|---|---|---|
| `examples/01_hello.py` | 最小 Agent 调用 | ✅ |
| `examples/02_tool.py` | `@tool` 装饰器 + 多步工具调用 | ✅ |
| `examples/03_stream.py` | `run_stream()` 流式输出 | ✅ |
| `examples/04_testing.py` | `MockProvider` 离线测试 | ❌ |
| `examples/05_mcp.py` | 接入 MCP filesystem server | ✅ + Node.js |

真实 LLM 示例通过环境变量配置：

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.openai.com/v1   # 可选
export OPENAI_MODEL=gpt-4o-mini                    # 可选
python examples/01_hello.py
```

## 测试

```bash
.venv/bin/python -m pytest tests/ -q \
  --ignore=tests/test_e2e_real.py \
  --ignore=tests/test_e2e_mcp_skill.py
```

426 mock 测试，覆盖 agent loop、上下文压缩、工具编排、MCP、Skill、Hook、Coordinator、side query、testing module、streaming fallback 等。E2E 测试默认排除（需要真实 LLM endpoint）。

## 版本 / pin

版本只在本地通过 git tag 管理，不发布到 PyPI。当前 baseline：**`v0.3.0`**。

在你的消费项目里 pin 到具体 commit 或 tag：

```bash
pip install "git+file:///Users/jowang/Documents/github/calcifer@v0.3.0"
```

或者直接用 editable install，跟着 `main` 滚动：

```bash
pip install -e /Users/jowang/Documents/github/calcifer
```

需要升级时：

```bash
cd /Users/jowang/Documents/github/calcifer
git pull   # 或 git checkout v0.x.y
```

## 对标 Claude Code

Calcifer 的每个核心机制都有对应的 Claude Code 源码实现，参考实现位于
`/Users/jowang/Documents/github/claude-code-source/`。新机制提交时 commit message
应指向具体源文件和行号（例：`src/services/mcp/client.ts:375-421`）。

**不实现**的 Anthropic 专属特性：`cache_control`、beta headers、prompt caching、
Anthropic SDK 依赖。

## License

MIT
