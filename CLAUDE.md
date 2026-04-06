# Calcifer — Instructions for Claude

这个项目有一个结构化的 **harness 工作流**。所有新功能的开发都走这个流程。

**在开始任何实现之前，阅读 `harness/README.md`**。它定义了会话工作流的所有规则。

## 项目定位

Calcifer 是一个 **provider-agnostic** 的 Python agent runner 库，对标
Claude Code 的 Agent Runner 核心机制，面向任意 OpenAI 兼容 API。

核心设计原则：

- **不绑定任何 LLM 提供商** — 通过 `/v1/chat/completions` 接入 OpenAI / Ollama / vLLM / DeepSeek 等
- **对标 Claude Code 源码** — 每个核心机制都有对应的 Claude Code 实现
- **Anthropic 专属特性不实现** — cache_control、beta headers、prompt caching 等

参考实现位于 `/Users/jowang/Documents/github/claude-code-source/`。

## 会话工作流（强制）

任何新功能都必须经过以下流程：

```
1. 启动检查：  ./harness/init.sh
2. 看状态：    python harness/harness.py status
3. 选 feature：python harness/harness.py pick
4. 读合约：    cat harness/contracts/<feature-id>.md
5. 实现（只改一个 feature）
6. 验证：      python harness/harness.py verify <feature-id>
7. 标记完成：  python harness/harness.py complete <feature-id>
8. 追加日志：  编辑 harness/progress.md（append-only）
9. 提交：      git commit -m 'feat(area): implement <feature-id>'
```

## 硬性规则

1. **一次 session 只做一个 feature**。不要顺带修改不相关的代码。
2. **先写合约，再写代码**。没有合约的 feature 不允许开始实现。
   - 新 feature：`python harness/harness.py add <id>` 会生成模板
   - 然后填写 `harness/contracts/<id>.md` 的所有小节
   - commit 合约后才能开始实现
3. **验证是硬性 gate**。`verify` 命令不过就不允许 `complete`。
4. **不要删除或修改已有测试**，除非合约明确说要重构该测试。
5. **不要修改 `features.json` 的字段**（`harness.py complete` 除外）。
6. **`progress.md` 是 append-only**。只在顶部追加新条目，永远不要编辑旧条目。
7. **会话结束时留下干净状态**：所有改动已 commit，tests 全过，没有 uncommitted changes。

## 不走 harness 的例外

以下情况可以跳过 harness：

- **纯文档改动**（README、docs/*）— 直接提交
- **修复 CI 或环境问题**（`.gitignore`、`pyproject.toml` 配置调整）— 直接提交
- **紧急 bugfix**（回归测试揭示的明显 bug）— 可以不走合约，但必须追加 progress.md 条目说明

所有其他改动（新特性、对齐 Claude Code、重构、改进）必须走 harness。

## 技术规范

### 测试

- 所有新代码必须有测试
- mock 测试放在 `tests/test_*.py`，用 `pytest` 运行
- E2E 测试（`test_e2e_*.py`）需要真实 LLM，默认从 `init.sh` 排除
- 运行 mock 测试：
  ```
  .venv/bin/python -m pytest tests/ -q \
    --ignore=tests/test_e2e_real.py \
    --ignore=tests/test_e2e_mcp_skill.py \
    --ignore=tests/test_tui_web.py
  ```

### 代码风格

- Python 3.11+
- 使用 `from __future__ import annotations`
- Dataclasses + Pydantic 混用（Pydantic 用于 tool schemas，dataclass 用于内部类型）
- 异步代码用 `asyncio`，不用 trio
- 不依赖 Claude Code 专属的 Anthropic SDK

### 对比 Claude Code

每次对标 Claude Code 源码时，合约必须包含 `reference` 字段，指向具体的文件
和行号。例：`src/services/mcp/client.ts:375-421`。

这是 Calcifer 的核心设计原则：任何新机制都要能说清楚"对应 Claude Code 的
哪一部分"以及"我们为什么做/不做它"。

## 常见陷阱

- **不要给 Tool 加 permission 系统** — 已在之前的 session 中移除，设计上不做
- **不要实现 Anthropic 专属特性** — cache_control、beta headers、prompt caching 等
- **不要修改已有的 429 mock 测试** — 除非合约说要重构
- **不要在 `harness/` 之外写新的 CLI/脚本** — 所有工作流入口在 `harness.py`

## 参考资料

- `harness/README.md` — 完整工作流文档
- `README.md` — 项目功能和使用
- `/Users/jowang/Documents/github/claude-code-source/` — 参考实现源码
