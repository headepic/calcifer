# Calcifer Harness — 设计与现状

> 用于 Calcifer 功能开发的结构化工作流。强制执行 plan 阶段的合约审议、
> verify 阶段的双重 gate、以及 append-only 的会话历史。
> 灵感来自 Anthropic 的两篇 harness 设计文章，针对**库**（而非应用）做了适配。

**最后更新**: 2026-04-06 · **Commit**: `b5fb270` on `main`

---

## 1. 当前状态

| 指标 | 值 |
|---|---|
| Features 总数 | 6 |
| 已完成 (done) | 3 |
| 草拟中 (plan_drafting) | 3 |
| 占位符 (plan_stub) | 0 |
| Mock 测试 | 458 通过 |
| Bug 审阅轮数 | 4 (50 个发现，全部解决) |
| 文章对齐审阅轮数 | 2 (12 个发现，10 个应用，2 个延后) |
| 经过 review gate 真实交付的 feature | 1 (`when-to-use-skill-field`) |

**已完成**:
- `harness-contract-review` — 建立 review gate 本身的 meta-feature（bootstrap 自审）
- `mcp-auth-refresh` — 在 review gate 存在之前交付（既往不咎）
- `when-to-use-skill-field` — 第一个端到端走完整 review 工作流的 feature

**草拟中**（合约已写，等待审议）:
- `stop-hook-inject-continue`
- `wire-hooks-into-orchestrator`
- `abort-reason-tracking`

---

## 2. 起源与适配

灵感来自两篇 Anthropic 文章：

1. **"Effective harnesses for long-running agents"** — `init.sh` + `progress.txt` + `feature_list.json` 三件套，以及 initializer 与 coding agent 的分离。解决**过度野心**与**虚假完成**两大失败模式。

2. **"Harness design for long-running apps"** — planner/generator/evaluator 三 agent 架构、sprint contract 协商、基于文件的 artifact 交接、通过 prompt 迭代调校 evaluator。

**Calcifer 是库不是应用**，所以做了几处适配：

- **没有浏览器验证**（没有 UI）。改用 `pytest` + import/attribute 检查 + 与 `claude-code-source/` 交叉引用。
- **没有独立的 Planner agent**。Plan → Review → Generate 是一个 caller 顺序走过各阶段，**evaluator 保持独立 fresh-context subagent**。
- **`harness.py` 零 LLM 依赖**。Review packet 由 `harness.py` 生成，由 caller 自己的工具（Agent subagent 或人类阅读者）消费。没有 API key，没有 HTTP，没有凭据风险。

---

## 3. 架构: Plan → Generate → Verify

```
                          Plan 阶段
            ─────────────────────────────────
            │                                │
            │  harness.py add <id>           │
            │  (写入 PLACEHOLDER_VERIFY 哨兵)  │
            │         │                      │
            │         ▼                      │
            │  写合约                        │
            │  替换 placeholder 验证命令      │
            │         │                      │
            │         ▼                      │
            │  harness.py review <id>        │
            │  → 渲染 packet（元数据、       │
            │    合约 sha、机器自检、        │
            │    完整合约、checklist、       │
            │    历史记录）                   │
            │         │                      │
            │         ▼                      │
            │  ┌───────────────────────┐     │
            │  │ Fresh-context         │     │
            │  │ reviewer              │     │
            │  │ (subagent / 人类)     │     │
            │  └─────────┬─────────────┘     │
            │            │                   │
            │            ▼                   │
            │  harness.py review-record      │
            │  --reviewer X --status Y       │
            │            │                   │
            │   approved │ changes_requested │
            │      │        │               │
            │      │        └── 编辑 → 循环 │
            │      ▼                        │
            └──────┼─────────────────────────┘
                   │
                   ▼
                          Generate 阶段
            ─────────────────────────────────
            │  写代码。小步提交。           │
            │  一次只做一个 feature。       │
            └───────┬────────────────────────┘
                    ▼
                          Verify 阶段
            ─────────────────────────────────
            │  harness.py verify <id>       │
            │  ├─ review gate               │
            │  ├─ 跑命令（白名单、         │
            │  │  argv 模式、600s 超时）    │
            │  └─ 缓存 HEAD + tree sha     │
            │            │                  │
            │  harness.py log → progress.md │
            │            │                  │
            │  harness.py complete <id>     │
            │  ├─ 重新检查 review gate      │
            │  ├─ 缓存命中检查              │
            │  ├─ progress 仅追加 diff      │
            │  └─ passes=true (atomic)      │
            │            │                  │
            │  git commit + push            │
            └───────────────────────────────┘
```

---

## 4. Feature 状态机

`Feature.phase` 是**派生属性**，不存储。从 `(passes, status, review_status, verified_sha, verified_tree, verification 内容)` 计算得出。永远不会漂移。

```
                   cmd_add
                      │
                      ▼
             ┌────────────────┐
             │   plan_stub    │ ← verification 中含 PLACEHOLDER_VERIFY
             └───────┬────────┘
                     │ 填写合约 + 替换 placeholder
                     ▼
             ┌─────────────────┐
             │  plan_drafting  │ ← 还没有 review_status
             └───────┬─────────┘
                     │ review-record --status approved
                     ▼
             ┌───────────────┐
             │  generating   │ ← review approved，无 verify 缓存
             └───────┬───────┘
                     │ verify 成功，缓存写入
                     ▼
             ┌───────────────┐
             │   verifying   │ ← verified_sha + verified_tree 已设置
             └───────┬───────┘
                     │ complete
                     ▼
             ┌────────────┐
             │    done    │ ← passes=True
             └────────────┘

      review-record --status changes_requested
                  ┃
                  ▼
           ┌─────────────────┐
           │   plan_review   │ ← 作者编辑合约（sha 改变）
           └─────────────────┘    → review 失效

      cmd_block --reason "..."
                  ┃
                  ▼
           ┌───────────┐
           │  blocked  │
           └───────────┘
```

`cmd_reset` 清空缓存和 review 字段，但**不**触碰 `harness/reviews/*.jsonl` 中的 append-only 历史。

---

## 5. 数据模型

### `Feature` dataclass

```python
@dataclass
class Feature:
    # 身份
    id: str
    title: str
    category: str
    priority: str                # critical | high | medium | low

    # 合约内容
    description: str
    motivation: str
    acceptance_criteria: list[str]
    verification: list[str]      # 白名单命令（argv tokens）
    reference: str               # Claude Code 源码交叉引用 或 "no analog"

    # 运行时状态
    status: str                  # pending | in_progress | blocked | done
    passes: bool                 # 仅由 cmd_complete 设置

    # Verify 缓存（cmd_verify 写入，cmd_complete 消费）
    verified_sha: str            # verify 时的 HEAD sha
    verified_tree: str           # sha256(git diff HEAD + 未追踪文件内容)
    blocked_reason: str          # 由 cmd_block 设置

    # 合约审议缓存（cmd_review_record 写入，cmd_verify gate 校验）
    review_status: str           # "" | approved | changes_requested | blocking
    review_notes: str            # reviewer 反馈
    reviewed_at: str             # ISO 8601 UTC
    reviewed_contract_sha: str   # review 时合约文件的 sha256[:16]
    reviewer: str                # self | subagent | human | external

    @property
    def phase(self) -> str:      # 派生；见上方状态机
        ...
```

### 磁盘文件

```
harness/
├── README.md               工作流指南（人类可读的步骤）
├── DESIGN.md               本文件（设计 + 状态快照）
├── init.sh                 每次会话启动检查：venv、依赖、测试、干净 tree
├── harness.py              CLI — 所有子命令在一个 ~1600 行文件里
├── reviewer-checklist.md   12+ 条规则，runtime 加载到 review packet
├── features.json           backlog + 状态（atomic 写入，仅 harness.py 修改）
├── progress.md             append-only 会话日志（git diff 强制校验）
├── reviews/                每个 feature 的 append-only review 历史
│   └── <id>.jsonl          每次 review-record / review-miss 一行
└── contracts/              每个 feature 的验收合约
    ├── README.md           带必填章节的模板
    └── <id>.md             具体填写的合约
```

---

## 6. CLI 参考

| 子命令 | 用途 |
|---|---|
| `status` | 按 phase 分组显示 backlog 概览 |
| `pick` | 优先级最高的可拾取 feature；stub 归到 "BACKLOG NEEDS PLANNING" |
| `resume` | 列出 in_progress 的 feature |
| `add <id>` | 新 feature：stub 合约 + placeholder 验证命令 |
| `review <id>` | 把 review packet 渲染到 stdout |
| `review-record <id> --reviewer X --status Y --notes T` | 记录 verdict；非 bootstrap feature 的 `reviewer=self` 会被拒绝 |
| `review-miss <id> --what T` | 向 review 历史追加一条"reviewer 漏掉了"的校准记录 |
| `verify <id>` | 跑验证命令；gate 校验 review 已批准 + 合约 sha 匹配 |
| `complete <id>` | 标记 done；gate 校验 verify 缓存 + progress append-only |
| `block <id> --reason T` | 标记为 blocked 并记录原因 |
| `reset <id>` | blocked/in_progress → pending；清空 verify+review 缓存但**不**清 reviews 历史 |
| `log T --body B` | 向 progress.md 顶部插入一条带日期的条目（UTF-8，拒绝多行标题） |

逃生口（都需要非空 audit reason 写到 stderr）：
- `verify --skip-review REASON` / `complete --skip-review REASON` — 绕过 review gate
- `complete --skip-progress-check REASON` — 绕过 append-only progress 检查

---

## 7. 十个关键设计决策（含理由）

### D1 · `harness.py` 零 LLM 依赖

Review **由 caller 驱动**（Claude 通过 Agent 工具，或人类）。`harness.py` 做三件事：生成 packet、记录 verdict、gate 下一步。没有 API key，没有 httpx 调 OpenAI/Anthropic，在任何环境都能工作。代价：caller 必须记得调 subagent 而不是自审。**强制手段**是 `--reviewer` choice gate（见 D2）。

### D2 · Reviewer 身份必填且类型受限

`review-record --reviewer` 必须是 `{self, subagent, human, external}` 之一。`self` 在 bootstrap allowlist `_BOOTSTRAP_SELF_REVIEW_ALLOWED = {"harness-contract-review"}` 之外**会被拒绝**。这强制了文章 2 的 evaluator/generator 分离，不需要独立的 agent 进程 —— fresh-context 要求达成了相同的"无法对自己的工作保持怀疑"属性。

### D3 · 合约 SHA 锁定

Review 时记录 `sha256(contract_file_bytes)[:16]`。如果合约在批准后被编辑，`cmd_verify` 会拒绝并提示"合约自 review 以来已被编辑 — 必须重新 review"。防止"批准空 stub，然后填进垃圾"的绕过。

### D4 · 工作树指纹

`verified_tree = sha256(git diff HEAD + 未追踪文件内容)`，排除 `harness/features.json` 和 `harness/progress.md`（这些在工作流中本来就会变）。`cmd_complete` 比较当前指纹与缓存的指纹，不匹配就强制 verify 重跑。防止"在脏 tree 上 verify，然后 revert 实现，然后 complete"的绕过。

### D5 · argv 模式 verify（永远不用 `shell=True`）

`subprocess.run(argv_list, ...)`，永远不走 shell。这一点就消除了所有 shell 注入类（换行、重定向、进程替换、命令替换、glob）。白名单 + redirect token 拒绝是 defense-in-depth —— 注入面本来就不存在，这些只是给作者的 fail-fast 信号"你写了一个 shell-ish 命令"。

### D6 · 占位符哨兵作为一等状态

`PLACEHOLDER_VERIFY` 是模块级常量，被以下使用：
- `cmd_add` — 写入它作为默认验证命令
- `validate_and_parse_verify_command` — 原样拒绝它
- `_is_stub_feature` / `Feature.phase` — 检测它来标记 feature 为 `plan_stub`
- `cmd_pick` — 把 stub 显示在 "BACKLOG NEEDS PLANNING"（不交给 coder）

单一真相源。通过 `add` 创建新 feature 进入一个明显坏的状态，系统其他部分会识别并拒绝推进。

### D7 · 通过 diff 解析强制 append-only

`cmd_complete` 跑 `git diff HEAD -- harness/progress.md` 并遍历 unified diff。一旦进入 hunk（由 `@@` 标志），任何以 `-` 开头的行都被视为真正的删除。content 中孤立的 `---` markdown 水平线（在 hunk 内部）会被正确分类为删除，而不是 diff 元数据（pre-hunk）。这能 catch "reviewer 删了一条 HR" —— round 3 review 实际演示过的 case。

### D8 · 机器自检作为**上下文**，不是**gate**

Review packet 包含一个 `[MACHINE SANITY]` section，显示 OK/WARN/FAIL：合约文件存在、必填章节都在、没有 TODO 占位符、验证命令通过校验、引用文件存在、合约 ↔ features.json 验证数组同步、占位符哨兵不在场。这些是 **reviewer 看到的输入** —— **不是**独立 gate，**不会**绕过 reviewer 的判断阻断流程。这符合文章 2 的精神：机器检查**补充**人/agent 判断，不**替代**它。（最初设计有独立的 `lint` 子命令，重读文章后被丢弃。）

### D9 · Reviewer checklist 作为运行时文件

`harness/reviewer-checklist.md` 在 packet 渲染时读取。编辑就是普通的 git commit。新的失败模式追加新规则。Git 历史就是 provenance。实现了文章 2 的校准循环：*"读 evaluator 的日志，找出它的判断与我分歧的例子，更新 QA 的 prompt"* —— 但 prompt 是 markdown 文件，不是埋在源码里的 Python 字符串字面量。

### D10 · Append-only review 历史（reset 不会清）

每次 `review-record` 和 `review-miss` 都向 `harness/reviews/<id>.jsonl` 追加一行 JSON。`cmd_reset` **不**触碰这些文件。久而久之它们就成为校准 corpus：哪个 reviewer 在什么时候、在什么合约 sha 下漏掉了什么。未来工作：把反复出现的 miss 模式浮现出来，指导 reviewer-checklist 演化。

---

## 8. 硬性规则与强制点

| # | 规则 | 强制手段 |
|---|---|---|
| 1 | 一次会话一个 feature | 社会规范 + `pick`/`resume` 在 in_progress 时警告 |
| 2 | 先有合约再写代码 | `cmd_review` 拒绝缺失合约；`cmd_review_record` 拒绝含未引用 `TODO:` 的合约 |
| 3 | 必须有独立 reviewer | `cmd_review_record --reviewer` 拒绝 `self`（bootstrap allowlist 除外） |
| 4 | `complete` 双 gate | `cmd_verify` 检查 review_status + `cmd_complete` 检查 verify 缓存 + 合约 sha |
| 5 | 不要删/改测试 | 社会规范 |
| 6 | 只有工具能写 `features.json` | 所有 writer 都用 `save_features()`（atomic tempfile + os.replace） |
| 7 | `progress.md` append-only | `cmd_complete` 跑 `git diff HEAD --` 并遍历 hunk 找删除 |
| 8 | `reviews/*.jsonl` reset 不会清 | `cmd_reset` 只清 feature 字段，不动文件 |
| 9 | Stub feature 不可 pick | `cmd_pick` 按 `_is_stub_feature` 分区，归到 BACKLOG NEEDS PLANNING |
| 10 | 会话结束时干净状态 | `init.sh` 在 `git status --porcelain` 非空时 exit 2 |

---

## 9. 与 Anthropic 文章的对齐

### 忠实实现的

| 文章原则 | Calcifer 对应 |
|---|---|
| `init.sh` + `progress` + `features.json` 三件套 | `harness/init.sh` + `harness/progress.md` + `harness/features.json` |
| 一次会话一个 feature | 社会规则 + pick/resume 警告 + phase 状态机 |
| 干净状态规则 | `init.sh` 通过 `git status --porcelain` 拒绝脏 tree |
| Sprint contract 协商 | `cmd_review` + `cmd_review_record` + 合约 SHA 锁定支持迭代 |
| Evaluator/generator 分离 | Reviewer 身份 gate（必须 fresh-context subagent，`self` 被拒） |
| 基于文件的 artifact 交接 | `contracts/*.md` + `features.json` + `reviews/*.jsonl` + `progress.md` |
| 可度量的验收标准 | 合约模板禁止"代码质量好"这类标准；`_machine_sanity` 检查 |
| 通过 prompt 迭代校准 | `reviewer-checklist.md` + `cmd_review_miss` corpus |
| "Claude 开箱不是好的 QA agent" | Checklist 自带 3 条历史事件规则作为 provenance |
| 过度野心 / 虚假完成防护 | 双 gate + append-only progress + phase 状态 + 一次一个 feature 规则 |

### 有意的偏差

| 文章原则 | Calcifer 选择 | 原因 |
|---|---|---|
| 浏览器验证（Puppeteer） | `pytest` + import/attribute 检查 + 源码交叉引用 | Calcifer 是库；没有 UI |
| 独立的 Planner agent | 作者直接写合约，然后 subagent review | Planner→Generator 交接对单库工作过度工程 |
| Spawn evaluator 进程 | 由 caller 驱动的 fresh-context subagent | 保持 `harness.py` 零依赖 |
| 三个独立的 prompt | 一个 Claude session 走 plan → generate → verify；一个 subagent 仅用于 review | 对库规模来说合适的 agent 拆分粒度 |

---

## 10. 延后项（明确可以跳过）

来自第二轮文章对齐审议：

| # | Rec | 为什么延后 |
|---|---|---|
| 4 | 按标准结构化 verdict + 行号引用 | 自由文本 `--notes` 暂时够用；如果 miss 模式集中在标准粒度上再加 |
| 6 | 用于 round-zero 脚手架的 initializer 阶段 artifact | 只有把 harness 部署到新 repo 才需要，正在进行的 Calcifer 工作不需要 |
| 9 | **Review 时的 dry-run verify** | round-2 reviewer 说是"最高 leverage 的下一步改进"。~30 行。如果在实现之前每条 verify 命令都通过，自动拒绝批准（catch "feature 已经做完了"的情况） |
| 11 | 合约模板的反模式 section | 与 reviewer-checklist.md 规则重复 |

---

## 11. 明确不在 scope 内的东西

- **Verify 命令的权限模式**。allow-list 控制命令**形状**（前缀），不控制 **payload**。`python -c "任意代码"` 是允许的。真正的 payload gate 是对 `features.json` 改动的 git review。
- **CI 集成子命令**。没有 `harness.py ci`。Harness 是工作流工具；CI 跑普通 `pytest`。
- **自动 reviewer 校准**。Checklist 通过手动 git commit 演化。没有 ML，没有自动 prompt 调优。
- **多 reviewer 共识**。单 reviewer，单 verdict。重新 review（通过让合约 SHA 失效的编辑）是迭代路径。
- **`harness.py` 中的 API key 或 HTTP**。仅 stdlib；项目已有依赖之外没有新依赖。
- **`harness.py` spawn 自己拥有的进程**（LLM 调用、CI runner、deploy）。它生成 packet 和记录 verdict。其他都是 caller 的工作。

---

## 12. 审议历史一览

```
Round 1 (bugs)       15 个发现  → 12 个修复    commit f0604f1
Round 2 (bugs)       15 个发现  → 12 个修复    commit ab032de
Round 3 (bugs)        5 个发现  →  5 个修复    commit e29296e   PASS_WITH_MINOR_FIXES
Round 4 (bugs)        0 个发现  →  —          —               PASS — ship it

Round 1 (articles)   12 个发现  →  7 个应用    commit 24f19e4
Round 2 (articles)    3 个发现  →  3 个应用    commit b70c3b6   PASS_WITH_MINOR_FIXES
(Rounds 3-5 未使用)
```

合计：6 轮 50 个发现，所有 critical/high/medium 都已解决，低优先级延后项有跟踪。

---

## 13. 未来"准备好交付"的标准

一个 feature **准备好通过 harness 交付**，当且仅当以下全部为真：

1. ✅ 合约文件存在于 `harness/contracts/<id>.md`，每个必填章节都已填写
2. ✅ 合约 reference 引用了真实的 claude-code-source 文件 + 行号范围，或者诚实地说"no direct analog"
3. ✅ 合约中没有未引用的 `TODO:` 占位符
4. ✅ `features.json` 验证命令与合约的 `## Verification Commands` 块逐字一致
5. ✅ 验证使用 import/attribute 检查（`.venv/bin/python -c "from X import Y"`），而不是 grep 源码
6. ✅ 验证命令在实现前 FAIL，实现后 PASS
7. ✅ Fresh-context reviewer（subagent / human / external —— 永远不是 `self`）已记录 `review_status: approved`
8. ✅ Review 时的合约 sha 仍与当前文件匹配（没有偷偷编辑）
9. ✅ 实现通过 verify
10. ✅ `progress.md` 有本次会话的新 append 条目
11. ✅ Complete 时工作树除了白名单 harness 文件之外是干净的
12. ✅ Commit 信息说明改了什么以及为什么

如果任何一项失败，harness 会拒绝转换并告诉你哪一步错了。
