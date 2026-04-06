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
# 验证：venv 存在、依赖安装、tests 全过、git 干净

# 2. 看状态
python harness/harness.py status
# 显示：总数、已完成、进行中、下一个优先级

# 3. 选下一个 feature
python harness/harness.py pick
# 输出优先级最高的 pending feature ID

# 4. 读合约
cat harness/contracts/<feature-id>.md
# 如果不存在 → 先进 Plan 阶段，写合约，commit，再开始实现

# 5. 实现
# （只动相关代码，小步 commit）

# 6. 验证
python harness/harness.py verify <feature-id>
# 运行合约中定义的所有验证命令

# 7. 标记完成
python harness/harness.py complete <feature-id>
# 只修改 features.json 的 passes 字段

# 8. 记录
# 编辑 harness/progress.md，追加一段描述本 session 做了什么

# 9. 提交
git add -A
git commit -m "feat(<area>): implement <feature-id>"
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
├── init.sh             # 环境启动检查
├── harness.py          # 工作流 CLI (status/pick/verify/complete)
├── features.json       # feature 列表 + 状态（真实来源）
├── progress.md         # session 日志（append-only）
└── contracts/          # 每个 feature 的验收合约
    ├── README.md       # 合约模板
    └── <id>.md         # 具体合约
```

## 为什么不直接照搬文章

两篇文章描述的是**应用开发**（全栈 web app、游戏）的 harness，Calcifer 是**库**，所以：

- **没有 browser 验证** — 用 `pytest` + 和 Claude Code 源码对比
- **没有 planner/generator/evaluator 三 agent 分离** — 一个 agent 按阶段走就够
- **"clean state" 定义不同** — 不是"production-ready 的 web app"，而是"所有测试过、和参考实现对齐、无回归"
- **Feature 粒度更细** — 不是"一个 sprint 的 10 个 feature"，而是"一个具体的机制"（比如 "MCP auth refresh"）

## 和 Claude Code 源码对标的工作流

Calcifer 的一个特殊工作流：**所有新机制都要和 Claude Code 源码对比**。合约中必须包含 `reference` 字段，指向 `/Users/jowang/Documents/github/claude-code-source/` 下的具体文件和行号。这是 Calcifer 的核心设计原则。
