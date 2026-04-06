# Calcifer Harness 工作流

长程 agent 开发的工作流框架。所有 Calcifer 新功能的开发都走此流程。

## 为什么需要 harness

长程 agent 开发有两个核心失败模式（来自 Anthropic 的 harness 设计文章）：

1. **过度野心** — 一次 session 试图做太多，context 耗尽，留下半成品
2. **假完成** — 后续 session 看到 commit 历史就草率宣告完成

Harness 通过**结构化的会话交接**解决这两个问题：

- `init.sh` 让每个 session 从干净状态启动
- `progress.md` 记录每次 session 做了什么、为什么
- `features.json` 是唯一的"真实来源"，用 `passes` 字段硬性标记是否完成
- `contracts/` 每个 feature 的验收合约（独立文件，不随 features.json 变动）
- Git 历史是安全网，可随时回退

## 三个阶段

Harness 工作流把每个 feature 的开发拆成三个阶段：

### 1. Plan（规划）
产出 feature 合约，定义"做什么"和"怎样算完成"。不写代码。

产物：`harness/contracts/<feature-id>.md`

合约内容：
- 动机：为什么做这个 feature
- 对标：Claude Code 源码中的对应实现（文件路径 + 行号）
- 验收标准：一组可验证的断言（不是"代码质量好"这种主观描述）
- 验证命令：`pytest ...` 等可机器执行的命令
- 非目标：明确不做什么，防止 scope creep

### 2. Generate（实现）
只实现一个 feature，不顺带修改其他东西。每次小步提交。

规则：
- 一次 session 只动一个 feature
- 不要删除或修改测试（除非合约明确说要重构该测试）
- 不要顺带"清理"不相关的代码
- Commit message 清楚说明改了什么

### 3. Verify（验证）
运行合约中的验证命令，必须全部通过。否则 feature 状态保持 `passes: false`。

不要因为"大部分通过了"就标记完成。验证是硬性 gate。

## 会话工作流

每次 session 严格按以下顺序：

```bash
# 1. 启动检查
./harness/init.sh
# 验证：venv、依赖、pytest 全过（带 300s 超时）、git 干净（含 untracked）

# 2. 看状态
python harness/harness.py status

# 3. 看有无半成品（in_progress 的 feature 优先完成）
python harness/harness.py resume
# 如果有 in_progress feature，先完成它们再开新的

# 4. 选下一个 feature
python harness/harness.py pick
# 输出优先级最高的 pending feature ID + 合约路径

# 5. 读合约
cat harness/contracts/<feature-id>.md
# 如果不存在 → python harness/harness.py add <id> 生成 stub，然后填写

# 6. 实现（只动相关代码，小步 commit）

# 7. 验证
python harness/harness.py verify <feature-id>
# 运行合约中的验证命令（allow-listed，带 600s 超时）
# 成功后 verified_sha 写入 features.json 作为缓存

# 8. 追加进度日志
python harness/harness.py log "<feature-id>: 做了什么" --body "细节..."
# 或手动在 progress.md 顶部追加一段

# 9. 标记完成
python harness/harness.py complete <feature-id>
# 如果 HEAD 未变 → 跳过重复 verify；否则重跑
# 必须 progress.md 有未提交的改动（防止忘记记录）
# 把 passes=true 原子写入 features.json

# 10. 提交
git add -A
git commit -m "feat(<area>): implement <feature-id>"
```

### 异常情况

```bash
# Feature 跑到一半发现 scope 错了或 block 了
python harness/harness.py block <feature-id> --reason "为什么卡住"
# 然后在 progress.md 记录，commit

# 反复 verify 失败，想回到 pending 重新考虑
python harness/harness.py reset <feature-id>
# 清掉 verified_sha 和 blocked_reason，status 回 pending
```

## 规则（强约束）

1. **一次一个 feature**。永远不要一次 session 做多个 feature。
2. **先写合约，再写代码**。没有合约的 feature 不允许开始实现。
3. **验证是硬性 gate**。verify 命令不过就不能 complete。
4. **不要修改 features.json 的其他字段**。只允许 `complete` 命令修改 `passes` 字段。
5. **不要删除或修改测试**。除非合约明确说要重构该测试。
6. **每次 session 结束留下 clean state**。所有改动已 commit，tests 全过。
7. **progress.md 是 append-only**。不要编辑旧条目。
8. **一个 session 如果做不完**，在 progress.md 记录进度和阻塞点，下次接着做。

## 文件结构

```
harness/
├── README.md           # 本文件
├── init.sh             # 环境启动检查（带 pipefail + 300s 超时 + 含 untracked 的 dirty 检测）
├── harness.py          # 工作流 CLI（subcommands 见下）
├── features.json       # feature 列表 + 状态（真实来源，atomic write）
├── progress.md         # session 日志（append-only）
└── contracts/          # 每个 feature 的验收合约
    ├── README.md       # 合约模板
    └── <id>.md         # 具体合约
```

### harness.py subcommands

| 命令 | 作用 |
|------|------|
| `status` | 显示 backlog 总览 |
| `pick` | 选优先级最高的 pending feature（in_progress 优先警告） |
| `resume` | 列出所有 in_progress feature |
| `add <id>` | 添加新 feature（生成 stub 合约） |
| `verify <id>` | 运行合约验证命令（allow-listed + timeout） |
| `complete <id>` | 标记 feature 为 done（需 verified_sha 匹配 HEAD 或重跑） |
| `block <id> --reason "..."` | 标记为 blocked |
| `reset <id>` | 重置为 pending（清 verified_sha 和 blocked_reason） |
| `log "title"` | 追加 progress.md 条目 |

### Verification 命令的安全约束

- 每条命令必须匹配 allow-list 前缀：`grep`, `pytest`, `.venv/bin/python`,
  `python -c`, `python -m pytest` 等
- 禁止 shell 元字符：`;`, `&&`, `||`, `\``, `$(`, `>`, `<`
- 每条命令有 600s 超时
- 首选**导入 + 属性检查**而非 `grep`：
  ```
  # 好：真实验证 feature 已实现
  .venv/bin/python -c "from calcifer.agent import StopHookResult"
  
  # 坏：注释或字符串也能通过
  grep -q 'StopHookResult' calcifer/agent.py
  ```

## 为什么不直接照搬文章

两篇文章描述的是**应用开发**（全栈 web app、游戏）的 harness，Calcifer 是**库**，所以：

- **没有 browser 验证** — 用 `pytest` + 和 Claude Code 源码对比
- **没有 planner/generator/evaluator 三 agent 分离** — 一个 agent 按阶段走就够
- **"clean state" 定义不同** — 不是"production-ready 的 web app"，而是"所有测试过、和参考实现对齐、无回归"
- **Feature 粒度更细** — 不是"一个 sprint 的 10 个 feature"，而是"一个具体的机制"（比如 "MCP auth refresh"）

## 和 Claude Code 源码对标的工作流

Calcifer 的一个特殊工作流：**所有新机制都要和 Claude Code 源码对比**。合约中必须包含 `reference` 字段，指向 `/Users/jowang/Documents/github/claude-code-source/` 下的具体文件和行号。这是 Calcifer 的核心设计原则。
