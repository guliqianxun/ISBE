---
status: Accepted
date: 2026-05-12
author: liuzhiheng (with Claude review)
supersedes: 2026-05-06-self-growing-info-system-design.md 的 §0 + §1.1 + §2 表后半 + §3 + §4.4-4.7 + §6.5 / §6 C3
---

# ISBE v1 Scope Correction

## Decision

**ISBE v1 交付一个多域每日情报日报系统。**

其他能力（chat agent / L3 自扩展 / 看板 / 语义检索 / agent-written memory / weekly_compact / weekly_insight）一律推到 v2，**v1 跑稳 14 天后**再评估。

## Why（这次纠偏的原因）

原 spec §0 一句话承诺四件事：日报 + 对话 + L3 自扩展 + 可手编辑长期记忆。

实际 P0' → P2 走过来的代码（`src/isbe/{facts,topics,scheduler,llm,observability,workflows}`）只解决了第 1 件。Task 15-19（hermes 集成）一直挂着，"路径 B'" 这个事实路径没有 spec。继续按四件事 roadmap 走的代价：

1. **Scope diffusion** —— L3a / 看板 / chat 各 1-2 周工程量，分摊到 6-8 周窗口，每件都做不透
2. **Spec ≠ 代码** —— 越往后越难复读，新会话 onboard 成本上升
3. **未验证依赖** —— hermes 评估 (Task 15) 未做，但若干章节预设它存在
4. **Review 心智负担** —— 用户为每个未启用的子系统（`.pending` / 冲突解决 / 防递归）维护决策

剥光后用户真实想要的就一件事：**"每天 7:00 收到值得读的日报。"** 其他都是周边。

## What changes（具体改什么）

### v1 范围（保留 + 继续做）

| 模块 | 现状 |
|---|---|
| 多域 daily digest（nowcasting / video-gen / image-restoration / nvda 已上线） | ✅ 已跑通 |
| `topics.yaml` 用户编辑（新加 topic 不写 Python） | ✅ |
| `feedback/*.md` **用户手写** 注入 workflow | ✅ |
| Prefect 调度 + worker | ✅ |
| Postgres 去重持久化（C1） | ✅ |
| MinIO blob + artifacts | ✅ |
| Phoenix LLM trace | ✅ |
| C2 极简（`documents.read` flag） | ✅ |

**v1 继续做的事 = 加域**。每加一个用户真想读的域 = 一次产品胜利。不再做横向抽象（除非加第 5/6 个域时确实有成本）。

### v2 候选（**冻结实现，保留设计**）

| 原 spec 章节 | v2 状态 |
|---|---|
| §3 L3 自扩展（含 §3.1-3.7） | 设计保留，v1 不实现 |
| §4.4 `.pending` 审核流 | v1 期 memory **只有用户在写**；agent 写回的审核流入 v2 |
| §4.5 weekly_compact flow | v2 |
| §4.6 用户 vs agent 冲突解决 | v2（v1 不存在 agent 写） |
| §4.7 Qdrant 语义检索升级阶梯 | v2 |
| §6.5 chat-triggered insight | v2 |
| §6 C3 提炼洞察整层 | v2 |
| chat agent / hermes runtime | v2 |
| Next.js 看板（原 P5） | v2 |
| 多用户实际隔离 | v2 |
| Task 15-19（hermes 评估 + 集成） | **v2 入口前评审** |

### 路径命名统一

原 spec 出现过 "路径 A / 路径 B / 路径 B'" 三种命名 → 统一为：

**v1 自建（lite）**：自建少量 Python + Prefect + Postgres + MinIO + 文件式 memory，**不依赖 hermes**。AGENTS.md / spec §1 / PROGRESS 三处同步该命名。

## Acceptance test for v1（替代所有 phase 验收）

> **新朋友拿到 docker-compose + topics.yaml，30 分钟内，明早 7:00 收到包含其新增 topic 的日报。**

详见 `tests/acceptance/test_v1_smoke.md`。

## v1 → v2 evaluation gate

v1 ramp 跑稳后，**用户连续读 14 天日报、积累真实反馈**，才进入 v2 评审。评审清单：

1. 14 天里日报有几天用户没打开？打开了的日报有几条进了用户脑中（能复述）？
2. `feedback/*.md` 用户改了多少次？哪些改动在下一封日报里实际生效了？
3. 当前最想要的下一个能力是 chat / L3a / 看板 / 多用户 / 其他 中的哪一个？为什么？
4. 是否有用户提出*未列出*的能力？

**未回答 1-4 之前不进 v2 任何工程**。

## What stays the same

- AGENTS.md 红线 1-7 不动（仅在 #5 加一句"v1 Agent Loop 未实现"）
- 模块边界铁律（spec §1.3）不动
- 草稿审核流的*未来设计*保留（spec §4.4）
- 双执行范式分离原则不动
- §5 错误/可观测/测试 整章不动（是 v1 跑稳的基础）
- §6.1-6.4 C1/C2 基础部分不动

## Out of scope（本次 ADR 不做）

- 重写 spec §3-§6 正文（仅加 status banner）
- 删除任何已有代码
- 改 P1 / P1.5 / P1.6 / P1.7 / P2 已完成的工作

仅作 **spec 与 PROGRESS 的诚实化**。

---

*这次纠偏依据 2026-05-12 与 Claude 的 design review 会话。Review 原文不入仓库；要点是：spec 承诺 4 件事，代码做 1 件事，应让 spec 跟上代码而不是相反。*
