# calcifer-ask

One-shot codebase Q&A CLI. 问一个关于当前仓库的问题，拿到一个带文件引用的 Markdown 答案。

位于 `apps/ask/`，是 Calcifer SDK 的第二个消费者（第一个是 `apps/tui/`）。
和 SDK 物理隔离：独立 `pyproject.toml`，独立 package（`ask`），单向依赖 calcifer。

## 和 TUI 的区别

这两个 app 故意覆盖 SDK 的不同 surface，目的是 dogfood 整个 API 表面：

| 维度 | `apps/tui/` | `apps/ask/` |
|---|---|---|
| 交互形态 | 长会话，多轮聊天 | 一次性问答 |
| Agent API | `run_stream()` 流式 | `run_sync()` 一次性 |
| 自定义 tool | ❌ 只用内置 | ✅ `git_log` 通过 `@tool` 装饰器 |
| Stop hook | ❌ | ✅ `register_stop_hook` 限 8 轮 |
| 测试 | ❌ | ✅ `MockProvider` 单元测试 |
| 工具集 | 全部 8 个内置 | 过滤成只读（glob/grep/file_read）+ 自定义 |

## 特性

- **Read-only**：只加载 Glob、Grep、FileRead 这三个只读内置工具，加一个自定义的 `git_log`
- **有上限**：`register_stop_hook` 强制最多 8 个 turn，防止跑飞
- **Markdown 渲染**：Rich 把答案渲染成漂亮的终端 Markdown
- **`--raw`**：可以直接打印纯文本，方便管道 / 脚本
- **可测试**：配套 `tests/test_ask.py` 用 `MockProvider` 覆盖核心行为，`pytest` 一键跑

## 安装

从 calcifer 仓库根目录：

```bash
pip install -e .             # 装 SDK 本体
pip install -e apps/ask      # 装 ask
# 跑单元测试还需要 dev extras：
pip install -e "apps/ask[dev]"
```

## 使用

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.openai.com/v1   # 可选
export OPENAI_MODEL=gpt-4o-mini                    # 可选

ask "Agent loop 如何处理 429 错误？"
ask "哪里定义了 6 层 context 压缩管线？"
ask "最近三次 commit 改了什么"
ask --raw "列出所有 @tool 装饰的函数" | tee tools.txt
```

示例输出：

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ question ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Agent loop 如何处理 429 错误？                                            ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
╭─ answer ────────────────────────────────────────────────────────────────╮
│ Agent loop 在 calcifer/services/api/provider.py:165-192 检测到 429 响应 │
│ 时，走三级恢复：                                                         │
│                                                                         │
│  1. 指数退避重试（BASE_DELAY_S × 2^attempt）                            │
│  2. 如果响应里带 retry-after header，优先用这个值                       │
│  3. 连续 3 次 529 后如果有 fallback_model，切到降级模型                 │
╰─────────────────────────────────────────────────────────────────────────╯
turns=3 tool_calls=2 tokens=1420 cost=$0.000180
```

## 测试

```bash
pytest apps/ask/tests
```

5 个测试，完全不需要真实 LLM，用 `calcifer.testing.MockProvider` 模拟响应：

- `test_ask_returns_canned_text_when_model_answers_directly`
- `test_ask_runs_tool_then_final_answer`
- `test_ask_tool_call_is_observable_via_assert_tool_called`
- `test_ask_message_count_helper`
- `test_git_log_tool_returns_string_for_self_repo`

## 文件

```
apps/ask/
├── README.md
├── pyproject.toml
├── ask/
│   ├── __init__.py
│   ├── __main__.py
│   └── app.py          # CLI + build_agent + @tool git_log + stop hook
└── tests/
    └── test_ask.py     # MockProvider-based unit tests
```

## License

MIT（继承自 calcifer 仓库）。
