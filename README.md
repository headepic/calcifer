# Calcifer

不绑定任何 LLM 提供商的 Python Agent Runner，复刻了 Claude Code Agent Runner 的核心机制，面向任意 OpenAI 兼容 API。

## 这是什么

Calcifer 是一个用于构建 LLM 驱动的 Agent 的**库**。它能使用工具、管理长对话、自动从错误中恢复 —— 且不锁定任何特定的 LLM 提供商。

接入 OpenAI、本地 Ollama、vLLM、DeepSeek，或任何 `/v1/chat/completions` 接口：

```python
from calcifer import Agent, tool

@tool(name="add", description="两数相加")
def add(a: int, b: int) -> str:
    return str(a + b)

agent = Agent(
    base_url="http://localhost:11434/v1",
    api_key="ollama",
    model="llama3",
    tools=[add],
)
result = await agent.run("7 + 8 等于多少？")
print(result.final_text)
```

## 核心架构

```
Agent Loop (统一的 run/run_stream)
  |
  +-- LLM Provider (httpx, 指数退避重试, retry-after, 模型降级)
  +-- Context Manager (6 层上下文压缩管线)
  +-- Tool Orchestrator (流式执行, 并发控制)
  +-- Session Persistence (保存/恢复/修复)
  +-- Telemetry (OpenTelemetry spans + metrics)
```

### Agent Loop

单一统一循环同时支撑 `run()` 和 `run_stream()`。错误恢复级联：

1. **prompt_too_long** -> reactive compact -> autocompact -> 重���
2. **max_output_tokens** -> 第 1 阶段: 仅升级 token 上限 (8K->64K); 第 2 阶段+: 注入 resume 消息
3. **429/529 过载** -> 指数退避 + retry-after header 解析 -> 降级模型

还有：输出递减检测、stop hooks、中止控制、并发保护、调用链追踪。

### 上下文管理

6 层压缩管线，确保对话不超出上下文窗口：

| 层 | 机制 | 触发时机 |
|----|------|---------|
| 1 | 工具结果预算 (500K 字符上限) | 每轮 |
| 2 | 裁剪 (移除最旧消息) | 每轮 |
| 3 | 微压缩 (清理旧工具输出) | 每轮 |
| 4 | 自动压缩 (LLM 总结) | 达到 90% 阈值 |
| 5 | 上下文折叠 (折叠工具调用区域) | 仅应急 |
| 6 | 应急压缩 (所有层，激进模式) | API 413 错误 |

压缩后自动恢复：重新注入最近读取的文件内容、已调用的 Skill、MCP 工具列表。

### 工具

8 个内置工具：

| 工具 | 能力 |
|------|------|
| **Bash** | Shell 执行, 超时, 后台任务, 只读命令检测, 安全分类 |
| **FileRead** | 编号行输出, offset/limit, 二进制检测 |
| **FileWrite** | 区分新建/覆盖, 写后自动追踪读状态 |
| **FileEdit** | 模糊匹配 (5 种归一化策略), 编辑前必须先读, 文件变更检测 |
| **Glob** | 递归模式匹配 |
| **Grep** | ripgrep 集成, -B/-A/-C 上下文行, 多种输出模式, VCS 目录排除 |
| **SkillTool** | 加载并执行 Skill，支持变量替换 |
| **ToolSearchTool** | 延迟工具发现 + 关键词评分 |

通过 `@tool` 装饰器创建自定义工具，或继承 `Tool` 基类获得完整控制。

### 流式工具执行

工具参数一流式完成就立即开始执行 —— 不用等 LLM 完整响应。并发安全的工具并行执行，写入型工具串行执行。中断语义：只读工具可取消，写入型工具必须执行完。

### MCP 集成

连接任意 [Model Context Protocol](https://modelcontextprotocol.io/) 服务器：

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

4 种传输层 (stdio, SSE, HTTP, WebSocket)，会话过期检测 + 重建，工具 schema 缓存，annotations 映射，200K 内容大小限制。

### Skill 系统

通过 Markdown + YAML frontmatter 扩展 Agent 能力：

```markdown
---
name: review
description: 审查代码变更
allowed-tools: [bash, file_read, grep]
user-invocable: true
paths: ["*.py", "*.ts"]
---

审查当前分支的代码变更...
```

特性：条件激活 (基于文件路径)、inline/fork 执行模式、变量替换 (`$ARGUMENTS`, `$1..$N`)、动态发现、压缩后恢复。

### 多 Agent 协调

```python
from calcifer import Coordinator, CoordinatorConfig

coord = Coordinator(config, tools, CoordinatorConfig(max_workers=3))
results = await coord.run_workers([
    ("research", "找出所有 API 端点"),
    ("implement", "添加新的端点"),
], parallel=True)
```

Worker 拥有独立上下文、受限工具集和共享 scratchpad 目录。中止信号从 coordinator 传播到所有 worker。

### 前端

- **TUI**：基于 Rich 的终端 UI，支持 Markdown 渲染、工具调用展示、加载动画
- **Web GUI**：FastAPI + SSE 聊天界面
- **Print 模式**：text / json / stream-json 输出，用于脚本集成

## 安装

```bash
pip install -e ".[all]"    # 全部
pip install -e ".[tui]"    # 仅 TUI
pip install -e ".[web]"    # 仅 Web GUI
pip install -e ".[mcp]"    # MCP 支持
```

需要 Python >= 3.11。

## 使用

### 作为库

```python
from calcifer import Agent

async with Agent(api_key="...", model="gpt-4o") as agent:
    # 非流式
    result = await agent.run("写一个 hello world 程序")
    print(result.final_text)

    # 流式
    async for event in agent.run_stream("解释这段代码"):
        if event.type == "text_delta":
            print(event.text, end="")
```

### 命令行

```bash
calcifer                         # 交互式 TUI
calcifer -p "2+2 等于多少？"      # Print 模式
calcifer --web                   # Web GUI (localhost:8000)
calcifer --model gpt-4o-mini     # 指定模型
```

## 测试

```bash
pip install -e ".[dev]"
pytest tests/ --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

429 个 mock 测试，覆盖 agent loop、上下文管理、工具执行、MCP、Skill、会话恢复等。

## 许可证

MIT
