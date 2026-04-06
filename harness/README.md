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

每次 session 严格按以下顺序。Plan / Generate / Verify 三阶段明确分开，
plan 阶段结束时必须由**独立 reviewer**（fresh-context 的 subagent）
审阅合约并记录 verdict — 这是防止"generator 自己审自己"偏见的硬性 gate。

```bash
# ── Plan 阶段 ──

# 1. 启动检查
./harness/init.sh
# 验证：venv、依赖、pytest 全过（带 300s 超时）、git 干净（含 untracked）

# 2. 看状态
python harness/harness.py status
# 显示每个 feature 的 phase：plan_stub / plan_drafting / plan_review /
#                             generating / verifying / done / blocked

# 3. 看有无半成品
python harness/harness.py resume

# 4. 选下一个 pickable feature
python harness/harness.py pick
# 自动跳过 plan_stub feature，分两列显示：BACKLOG NEEDS PLANNING + 实际 pick

# 5. 写/读合约
cat harness/contracts/<feature-id>.md
# 不存在 → python harness/harness.py add <id> 生成 stub，然后填写所有 sections

# 6. 生成 review packet 并交给独立 reviewer
python harness/harness.py review <feature-id> > /tmp/review.txt
# 把 /tmp/review.txt 交给 fresh-context Agent tool call 或人工 reviewer

# 7. reviewer 返回后记录 verdict
python harness/harness.py review-record <feature-id> \
  --reviewer subagent \
  --status approved \
  --notes "specific feedback"
# reviewer=self 在非 bootstrap feature 上会被拒绝
# 如果是 changes_requested：编辑合约，回到第 6 步再跑一次 review

# ── Generate 阶段 ──

# 8. 实现（只动相关代码，小步 commit）

# ── Verify 阶段 ──

# 9. 跑验证
python harness/harness.py verify <feature-id>
# review 未通过 → 直接拒绝（--skip-review REASON 只在 bootstrap/紧急情况用）
# 成功后 verified_sha + verified_tree 写入 features.json 作为缓存

# 10. 追加进度日志（append-only）
python harness/harness.py log "<feature-id>: 做了什么" --body "细节..."

# 11. 标记完成
python harness/harness.py complete <feature-id>
# 双 gate：review_status == approved AND verified cache 有效
# HEAD + working tree 未变 → 跳过重复 verify

# 12. 提交
git add -A
git commit -m "feat(<area>): implement <feature-id>"
```

**Reviewer 必须是独立 context**。第 6-7 步要求 review-record 的 `--reviewer`
不能是 `self`（除非 feature 在 bootstrap 白名单里）。调用方式：

- **subagent**（推荐）：用 Agent 工具启动新 subagent，把 review packet 作为 prompt 喂进去，得到 verdict 后再回主 session 跑 `review-record`
- **human**：人工读 packet，人工决定 verdict
- **external**：外部 CI / review 工具

自审 (`--reviewer self`) 仅在 `_BOOTSTRAP_SELF_REVIEW_ALLOWED` 白名单里
的 feature 允许（目前只有 `harness-contract-review` 自己，因为它是
bootstrap 这个机制的 feature，其他 feature 不允许自审）。

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
3. **合约必须经过独立 reviewer 审议**。review-record 的 `--reviewer`
   不能是 `self`（bootstrap 例外除外）。违反 = review gate 拒绝 verify。
4. **验证是硬性双 gate**。`review_status == "approved"` AND 验证命令全过，
   才能 complete。review 未通过 verify 直接拒绝。
5. **不要修改 features.json 的其他字段**。只允许
   `complete / verify / block / reset / review-record` 修改。
6. **不要删除或修改测试**。除非合约明确说要重构该测试。
7. **每次 session 结束留下 clean state**。所有改动已 commit，tests 全过。
8. **progress.md 是 append-only**。`complete` 会 `git diff` 校验，
   任何删除的行都会拒绝。
9. **一个 session 如果做不完**，在 progress.md 记录进度和阻塞点，
   下次接着做。
10. **stub feature 不允许 pick**。verification 里还有 cmd_add 占位符的
    feature，`pick` 会归到 BACKLOG NEEDS PLANNING 不作为下一个任务。

## 文件结构

```
harness/
├── README.md               # 本文件
├── init.sh                 # 环境启动检查（pipefail + 300s 超时 + 含 untracked 的 dirty 检测）
├── harness.py              # 工作流 CLI（subcommands 见下）
├── reviewer-checklist.md   # reviewer 的 10+ 条 checklist（runtime 加载到 review packet）
├── features.json           # feature 列表 + 状态（真实来源，atomic write）
├── progress.md             # session 日志（append-only）
├── reviews/                # append-only 每个 feature 的 review 历史（reset 不清）
│   └── <id>.jsonl          # 每次 review-record / review-miss 的事件
└── contracts/              # 每个 feature 的验收合约
    ├── README.md           # 合约模板
    └── <id>.md             # 具体合约
```

### harness.py subcommands

| 命令 | 作用 |
|------|------|
| `status` | 显示 backlog 总览（按 phase 分组） |
| `pick` | 选优先级最高的 pickable feature（in_progress 警告 + stub 分离到 BACKLOG NEEDS PLANNING） |
| `resume` | 列出所有 in_progress feature |
| `add <id>` | 添加新 feature（生成 stub 合约 + placeholder verify） |
| `review <id>` | 打印 review packet 到 stdout（contract + features.json + machine sanity + reviewer checklist + 历史） |
| `review-record <id> --reviewer X --status Y --notes ...` | 记录 reviewer verdict；`self` 非 bootstrap 会被拒绝 |
| `review-miss <id> --what "..."` | 记录一次"reviewer 漏过"的事件到 reviews/<id>.jsonl 作为 calibration corpus |
| `verify <id>` | 运行合约验证命令（allow-listed + timeout + review gate） |
| `complete <id>` | 标记 feature 为 done（需 review approved + verified cache 有效） |
| `block <id> --reason "..."` | 标记为 blocked |
| `reset <id>` | 重置为 pending（清 verify/review 缓存字段，但**不**清 reviews/ 历史） |
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

## 和两篇文章的对比

两篇文章描述的是**应用开发**（全栈 web app、游戏）的 harness，Calcifer
是**库**，所以有几处有意的偏差；但核心原则都忠实复刻了。

**有意对齐的**：
- **Plan → Generate → Verify 三阶段** — Feature.phase 显式追踪
  (plan_stub → plan_drafting → plan_review → generating → verifying → done)
- **Evaluator / Generator 分离** — cmd_review_record 的 `--reviewer` gate
  强制 reviewer 不是 `self`；reviewer 必须是 fresh-context subagent / human /
  external。**这不是单 agent 走所有阶段** —— review packet 通过文件交接
  给独立 reviewer，和 Article 2 的 sprint contract negotiation 模式一致。
- **文件作为 artifact 交接** — contracts/ + features.json + reviews/*.jsonl +
  progress.md 都是 append-only 的通信媒介
- **可度量的验收标准** — 合约模板强制 "yes/no 可验证" 而非 "代码质量好"
- **Evaluator calibration loop** — reviewer-checklist.md 是 runtime 加载
  的文件，review-miss 命令记录漏过的案例，checklist 用 git 历史迭代
- **Over-ambition + false-completion 的防护** — 一次 session 一个 feature
  + 双 gate complete + 工作树 fingerprint 缓存

**有意偏差的**：
- **没有 browser 验证** — 库没有 UI。用 `pytest` + import/attribute 检查 +
  和 Claude Code 源码对比作为"无法靠表面形状伪造"的 gate
- **没有独立 Planner agent** — 用户/Claude 直接写合约后由 evaluator
  subagent 审阅，不再中间加一个 Planner 步骤（Planner → Generator 的
  交接对单库场景过重）
- **harness.py 不调 LLM** — review packet 由 harness.py 生成，但 reviewer
  是**调用方**（Claude 通过 Agent 工具 / 人）。这保持 harness 零依赖，同时
  通过 `--reviewer` gate 强制独立 context
- **"clean state" 定义不同** — 不是 production-ready web app，而是
  "所有测试过、和参考实现对齐、工作树干净"
- **Feature 粒度更细** — 一次 session 一个具体机制（比如 "MCP auth refresh"），
  不是 "一个 sprint 的 10 个 feature"

## 和 Claude Code 源码对标的工作流

Calcifer 的一个特殊工作流：**所有新机制都要和 Claude Code 源码对比**。合约中必须包含 `reference` 字段，指向 `/Users/jowang/Documents/github/claude-code-source/` 下的具体文件和行号。这是 Calcifer 的核心设计原则。
