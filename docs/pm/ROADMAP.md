---
pm_id: roadmap-main
pm_type: roadmap
project: ISBE
status: wip
index_file: docs/pm/index.json
managed_by: skills/project-manager/scripts/pm_index.py
source_of_truth: markdown
updated_at: 2026-05-20
---

# ISBE — Roadmap

> ISBE = Information System with Backbone of Evolution。私人研究助理：抓数据 → LLM
> 写周报 → 把"你信什么"沉淀成 memory → 下期基于 memory 做对照分析。
>
> 状态说明: 💡 Idea | 📋 Planned | 🚧 WIP | ✅ Completed | 🚀 Shipped
>
> 机读索引: `docs/pm/index.json` ｜ 查询脚本: `skills/project-manager/scripts/pm_index.py`
>
> 注: 本 `docs/pm/` 于 2026-05-20 建立。更早的阶段记录见 `docs/superpowers/`
> （P0/P1/P2 archive + specs）；本路线图只承接 2026-05 之后的规划。

---

## 已交付 — 核心管线（P0–P2，🚀 Shipped）

> 🎯 目标: 多领域周期性 digest 管线自用跑通
> 📅 时间: 2026-05-06 → 2026-05-13
> 状态: 🚀 Shipped — 详见 `docs/superpowers/archive/`

- 三层数据模型（facts / memory / artifacts）+ topic 同构接口（collector → digester）
- 6 个活跃 topic：nowcasting / video-generation / image-restoration / nvda / motorcycle / china-tech
- 信源套餐：arxiv / GitHub / RSS / RSSHub / morerssplz / Crawl4AI / 股价·SEC
- 5 段周报模板 + memory 蒸馏 `.pending` + `radar review` 流
- self-hosted 一栈（postgres / minio / prefect / phoenix / rsshub / morerssplz / qdrant / uptime-kuma）
- LLM provider 可切换（deepseek / anthropic）；73 单测全绿

---

## v1.1 — 投递与运维（🚀 Shipped）

> 🎯 目标: digest 不止落盘 —— 主动推送到我的邮箱；服务器部署流程标准化
> 📅 计划时间: 2026-05
> 状态: 🚀 Shipped (2026-05-23)

### Epic: 主动投递（Delivery）

| # | Feature | 优先级 | 状态 | Feature Doc | 备注 |
|---|---------|--------|------|-------------|------|
| 1 | 邮件 digest 投递（订阅邮件） | P1 | ✅ Completed (2026-05-23) | [features/ft-001-email-digest-delivery.md](features/ft-001-email-digest-delivery.md) | id: ft-001｜MVP 闭环（plaintext markdown）；HTML 渲染拆出独立 ft-002 |

### Epic: 运维（Ops）

| 任务 | 类型 | 状态 | Dispatch | 备注 |
|------|------|------|----------|------|
| 服务器更新至最新代码并重启 | deploy | ✅ Completed (2026-05-22) | [communications/dsp-001-deploy-latest-code.md](communications/dsp-001-deploy-latest-code.md) | dsp-001｜见 [rpt-001](communications/rpt-001-deploy-report.md) |

---

## v1.2 — 邮件订阅体验升级（🚀 Shipped）

> 🎯 目标: 邮件不止"能读"，而是"读着舒服" —— 引入 ISBE 第一版视觉品牌
> 📅 计划时间: 2026-05
> 状态: 🚀 Shipped (2026-05-24)

### Epic: 主动投递（Delivery）

| # | Feature | 优先级 | 状态 | Feature Doc | 备注 |
|---|---------|--------|------|-------------|------|
| 1 | 邮件 HTML 渲染（学术墨色 brand）| P1 | ✅ Completed (2026-05-24) | [features/ft-002-email-html-rendering.md](features/ft-002-email-html-rendering.md) | id: ft-002｜用户实测"还行";学术墨色 brand 规范沉淀 |

---

## Backlog（待规划）

> 尚未分配里程碑的想法、技术债。来源：README「已知短板」+ 跨项目讨论。

| # | 类型 | 描述 | 优先级 | 来源日期 | 备注 |
|---|------|------|--------|----------|------|
| bl-001 | feature | 记忆联邦：与 explore-os 共享论点库（Tier 2 信念层）| P2 | 2026-05-20 | 跨项目；契约待写入 `docs/superpowers/specs/` |
| bl-002 | feature | Qdrant 语义检索接入（替代纯时间窗筛 facts）| P3 | 2026-05-20 | README 已知短板 |
| bl-003 | tech-debt | `radar review accept` 补 frontmatter lint | P3 | 2026-05-20 | README 已知短板 |
| bl-004 | feature | 一键部署 image | P3 | 2026-05-20 | README 已知短板 |
| bl-005 | feature | 邮件投递多收件人（`ISBE_SMTP_TO` comma-list）| P3 | 2026-05-20 | 当前单收件人 |
| bl-006 | ops | Prefect server 升级对齐 client（3.6.29 → 3.7.0+）| P2 | 2026-05-22 | dsp-001 部署后发现版本错配告警 |

---

## 版本概览

| 版本 | 状态 | 计划特性数 | 已完成 | 进度 |
|------|------|-----------|--------|------|
| P0–P2 | 🚀 Shipped | — | — | 100% |
| v1.1 | 🚀 Shipped | 1 | 1 | 100% |
| v1.2 | 🚀 Shipped | 1 | 1 | 100% |
| Backlog | — | 6 | — | — |
