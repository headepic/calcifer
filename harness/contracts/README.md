# Feature Contract: <feature-id>

> 合约模板。复制本文件并填写每一节。合约是 feature 的"真实来源"——
> 它定义了"做什么"和"怎样算完成"。不要在没有合约的情况下开始实现。

## Motivation

为什么做这个 feature？一句话说明问题和影响。

## Claude Code Reference

对标的 Claude Code 源码位置。包括：

- 文件路径（绝对或相对 `/Users/jowang/Documents/github/claude-code-source/`）
- 关键函数/类名和行号
- 核心机制的简述（不是照搬代码）

例：
```
src/services/mcp/client.ts:375-421
- checkAndRefreshOAuthTokenIfNeeded(): OAuth token refresh on 401
- handleOAuth401Error(): force refresh + retry if token changed
```

## Scope

### 要做

- 具体的事情 1
- 具体的事情 2

### 不做（non-goals）

明确写出不做的事情，防止 scope creep：

- 不做的事情 1（原因）
- 不做的事情 2（原因）

## Design

实现思路。不需要最终的代码，但需要说明：

- 涉及哪些文件
- 新增/修改哪些接口
- 与现有系统怎么集成
- 主要数据流

## Acceptance Criteria

可验证的断言。每条必须是"yes/no 可判断"的。
不要写"代码质量好"或"性能高"这种无法验证的描述。

- [ ] 断言 1（例：`MCPClient.__init__` 接受 `on_auth_error` 回调参数）
- [ ] 断言 2（例：401 响应触发 callback，返回新的 headers）
- [ ] 断言 3（例：新测试 `tests/test_mcp_auth.py` 覆盖 callback 路径）
- [ ] 断言 4（例：现有 429 mock tests 全部仍然通过）

## Verification Commands

`harness.py verify` 会运行的命令。每条必须以 exit 0 结束才算通过。

```
.venv/bin/python -m pytest tests/test_mcp.py -q
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

这些命令也要写入 `features.json` 的 `verification` 字段。

## Rollback Plan

如果实现中发现合约错了、scope 爆炸、或方案走不通，如何回退：

- 回到本 feature 开始前的 commit（`git reset --hard <sha>`）
- 在 `progress.md` 记录为什么放弃
- 把 feature 状态改回 `pending`，补充 `blocked` 描述
