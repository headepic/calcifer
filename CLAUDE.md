# Calcifer SDK — Instructions for Claude

## 项目定位

Calcifer 是一个 **provider-agnostic** 的 Python agent runner 库，对标
Claude Code 的 Agent Runner 核心机制，面向任意 OpenAI 兼容 API。

核心设计原则：

- **不绑定任何 LLM 提供商** — 通过 `/v1/chat/completions` 接入 OpenAI / Ollama / vLLM / DeepSeek 等
- **对标 Claude Code 源码** — 每个核心机制都有对应的 Claude Code 实现
- **Anthropic 专属特性不实现** — cache_control、beta headers、prompt caching 等
- **自用为主** — 不发布到 PyPI，无 CHANGELOG / release 流程

参考实现位于 `/Users/jowang/Documents/github/claude-code-source/`。

## 技术规范

### 测试

- 所有新代码必须有测试
- mock 测试放在 `tests/test_*.py`，用 `pytest` 运行
- E2E 测试（`test_e2e_*.py`）需要真实 LLM，默认排除
- 运行 mock 测试：
  ```
  .venv/bin/python -m pytest tests/ -q \
    --ignore=tests/test_e2e_real.py \
    --ignore=tests/test_e2e_mcp_skill.py
  ```

### 代码风格

- Python 3.11+
- 使用 `from __future__ import annotations`
- Dataclasses + Pydantic 混用（Pydantic 用于 tool schemas，dataclass 用于内部类型）
- 异步代码用 `asyncio`，不用 trio
- 不依赖 Anthropic SDK

### 对比 Claude Code

对标 Claude Code 源码时，commit message 应指向具体文件和行号，例如
`src/services/mcp/client.ts:375-421`，并说明为什么做/不做某机制。

## 常见陷阱

- **不要给 Tool 加 permission 系统** — 设计上不做
- **不要实现 Anthropic 专属特性** — cache_control、beta headers、prompt caching 等
- **不要修改已有的 429 mock 测试** — 除非明确要重构
- **会话结束时留下干净状态** — 所有改动已 commit，tests 全过

## 参考资料

- `README.md` — 项目功能和使用
- `docs/public-api.md` — 公开 API 列表
- `docs/testing.md` — `calcifer.testing` 用法
- `/Users/jowang/Documents/github/claude-code-source/` — 参考实现源码
