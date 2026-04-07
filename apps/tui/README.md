# calcifer-tui

一个极简的终端聊天 UI，基于 Calcifer SDK。

位于 `apps/tui/`，和 `calcifer/` 包**物理隔离**：

- 独立的 `pyproject.toml`
- 独立的 package 名（`calcifer_tui`，不在 `calcifer.__all__` 里）
- 单向依赖：TUI `from calcifer import ...`，SDK 完全不知道 TUI 存在
- 装 SDK 不会拉这个 TUI

## 特性

- 流式输出（边生成边显示）
- 工具调用可视化（`→ bash {...}` / `← result`）
- 8 个内置工具（Bash、FileRead、FileWrite、FileEdit、Glob、Grep、SkillTool、ToolSearchTool）
- 对话历史跨回合保留
- Ctrl+C 中断当前回合（对话不丢）
- Ctrl+D 退出
- Slash 命令：`/help` / `/clear` / `/tools` / `/model` / `/cost` / `/exit`
- Prompt 历史（prompt_toolkit）
- Token 和 cost 统计显示

## 安装

需要 Python ≥ 3.11。从 calcifer 仓库根目录：

```bash
# 1. 装 SDK 本体（editable，一次性）
pip install -e .

# 2. 装 TUI
pip install -e apps/tui
```

两步完成后，同一个 venv 里 `calcifer` 和 `calcifer-tui` 都可 import。

> **为什么两步**：Python 没有原生 monorepo workspace，但 `pip install -e`
> 看到 venv 里已经有 `calcifer`，就不会再从 PyPI / git 拉，直接满足依赖。

## 运行

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.openai.com/v1   # 可选
export OPENAI_MODEL=gpt-4o-mini                    # 可选

calcifer-tui
# 或
python -m calcifer_tui
```

接 Ollama / vLLM / 本地 endpoint：

```bash
export OPENAI_API_KEY=ollama
export OPENAI_BASE_URL=http://localhost:11434/v1
export OPENAI_MODEL=llama3
calcifer-tui
```

## 使用示例

```
Calcifer TUI — minimal terminal agent chat
/help 查看命令 · Ctrl+D 退出 · Ctrl+C 中断当前回合

you › 列出当前目录下的 .py 文件
  → glob {"pattern": "*.py"}
  ← ["apps/tui/calcifer_tui/app.py", ...]
calcifer › 当前目录下有以下 Python 文件：

- apps/tui/calcifer_tui/app.py
- apps/tui/calcifer_tui/__main__.py
- ...
turns=2 tokens=387 cost=$0.000087

you › /exit
bye.
```

## 架构

单文件 TUI，~230 行。核心循环：

```
main loop:
  user_input = prompt_toolkit.prompt_async(...)
  if slash_command: handle_and_continue
  async for event in agent.run_stream(user_input, messages=conversation):
      render(event)      # 根据 event.type 分发
  conversation = result.messages   # 保留到下一轮
```

渲染策略：

- `text_delta` → 直接 `sys.stdout.write`（实时 flush）
- `tool_call_start` / `tool_call_result` → `rich.console` 彩色标签
- `run_complete` → 底部状态栏（turns / tokens / cost）
- `error` → 红色错误提示

Ctrl+C 处理用 `asyncio.add_signal_handler` 在每个 turn 开始时临时挂 hook，
把 SIGINT 转成 `agent._abort_event.set()`，turn 结束时解绑。

## 文件

```
apps/tui/
├── README.md
├── pyproject.toml
└── calcifer_tui/
    ├── __init__.py
    ├── __main__.py
    └── app.py          # 全部 TUI 逻辑在这里
```

## 许可证

MIT（继承自 calcifer 仓库）。
