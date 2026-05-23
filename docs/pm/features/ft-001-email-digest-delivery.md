---
pm_id: ft-001
pm_type: feature
title: 邮件 digest 投递（订阅邮件）
status: wip
priority: P1
milestone: v1.1
parent: ep-delivery
domain: [notify, digester]
depends_on: []
blocks: []
index_file: ../index.json
managed_by: skills/project-manager/scripts/pm_index.py
source_of_truth: markdown
tags: [feature, delivery, email]
created_at: 2026-05-20
---

# 邮件 digest 投递（订阅邮件）

> **所属**: [v1.1 投递与运维](../ROADMAP.md) > Epic: 主动投递
> **状态**: 🚧 WIP
> **优先级**: P1
> **涉及工作流**: notify, digester
> **创建日期**: 2026-05-20
> **索引 ID**: `ft-001`

## 概述

ISBE 现在把周报落到 `artifacts/<topic>/<period>/latest.md`（+ MinIO + Postgres），
但**不会主动送到我面前** —— 还得自己去翻文件。本特性让 digest 生成后**自动发邮件**，
真正实现"信息流向我，而不是我去追"。

**关键现状：传输层已存在，本特性是产品化而非从零造。**

`src/isbe/notify/__init__.py` 已实现完整 SMTP 发送（`send_digest_notification()`），
且已接入全部 3 个 digester（`nvda` / `_shared/articles_digester` / `_shared/digester`）。
env 变量缺失即 no-op，digest 流程绝不因推送失败而 fail。

但它离"可用的订阅邮件"还差三处缺口：

1. **配置不可发现** —— `.env.example` 没有任何 `ISBE_SMTP_*` 条目，运维者无从得知要配什么。
2. **邮件内容是残的** —— 正文只有 `topic / period / artifact 路径 / 一段 excerpt`。
   收件人在手机上拿到一个**服务器本地文件路径**，根本读不到 digest 本身。
3. **未验证** —— 线上从未配过 SMTP env，`notify` 路径从未真正投递过一封信。

## 范围

### MVP（本特性当前目标）—— nvda 单 topic 试水

按 PM 决策（2026-05-20）：先只接一个 topic 投递，跑通了再推广。
选 `nvda` —— 它是唯一 `cadence: daily_after_close` 的日更 topic，天然满足"每天发一封"。

1. **`.env.example` 补全 `ISBE_SMTP_*` 段**，含注释说明 587/465 行为与 `ALLOW_PLAINTEXT`。
2. **邮件正文承载完整 digest** —— 把 `latest.md` 全文放进正文（纯文本即可；HTML 渲染为拉伸目标），
   而非只放路径 + excerpt。收件人无需碰服务器即可读完当期 digest。
3. **服务器配置 SMTP env 并实测** —— 配 `nvda` 收件后，确认真实邮件落到收件箱。

### 拉伸目标（非 MVP，验证通过后再排）

- markdown → HTML 渲染（参照 explore-os `delivery/email_renderer`，**不复用代码**，仅参照）
- 多收件人（`ISBE_SMTP_TO` comma-list，见 bl-005）
- 每日聚合：weekly topic 同日多份 digest 合并成一封（当前 MVP 单 topic 无此需求）

### 不做

- 不改 `notify` 的"绝不 raise / env 缺失即 no-op"契约
- 不接非邮件渠道（IM / webhook）

## 需求

### 功能需求
- `.env.example` 列出全部 `ISBE_SMTP_*` 变量及默认值/语义
- digest 邮件正文包含当期 digest 全文（不再是路径 + excerpt）
- `nvda` digest 生成后自动投递成功

### 非功能需求
- `notify` 失败绝不使 digest 流程 fail（维持现有契约）
- 不在明文连接上发送凭据/内容，除非显式 `ISBE_SMTP_ALLOW_PLAINTEXT=1`（现有行为，保持）

## 验收标准

- `.env.example` 含 `ISBE_SMTP_HOST/PORT/FROM/TO/USER/PASS/ALLOW_PLAINTEXT` 七项及注释
- 触发一次 `nvda` digest 后，配置的收件箱收到一封邮件，**正文可直接读到当期 digest 全文**
- env 变量未配时，digest 流程仍正常完成（no-op，回归不破）
- `notify` 相关单测通过

## 任务分工

| 工作流 | 职责概述 | Assignment | 状态 |
|-------|---------|------------|------|
| notify | `.env.example` 补全 + 邮件正文改为全文投递 | [dsp-002](../communications/dsp-002-email-digest-mvp.md) | ✅ 2026-05-23（见 [rpt-002](../communications/rpt-002-email-mvp-report.md)） |
| user/ops | server `.env` 填 SMTP 凭据 + 触发 nvda digest + 验收落箱 | [dsp-003](../communications/dsp-003-server-smtp-live-test.md) | 📋 Pending |

> 具体任务拆分与进度由执行方维护。PM 通过 `pm_index.py aggregate` 读取全局状态。

## 依赖关系

- depends_on: 无（`notify` 模块已存在）
- 关联: [dsp-001](../communications/dsp-001-deploy-latest-code.md) 把含 `notify` 的最新代码部署到服务器；
  服务器跑新代码是本特性线上验证的前提

## 变更记录

| 日期 | 变更内容 |
|------|---------|
| 2026-05-20 | 初始创建。确认 `notify` SMTP 传输层已存在并接入 digester；范围定为产品化 3 缺口；MVP 锁 nvda 单 topic |
| 2026-05-23 | dsp-002 代码部分 ✅（分支 `feat/email-digest-delivery`，2 commit，11/11 notify 测试通过）；剩 dsp-003 用户在 server 实测落箱 |

## 备注

- 现有 `notify` 实现：`src/isbe/notify/__init__.py`；调用点见 `topics/_shared/digester.py:201`
- explore-os 已有成熟 HTML 邮件渲染（`delivery/email_renderer` + `email_sender`）。
  按 2026-05-20 决策「只做记忆联邦、不抽共享库」，此处 ISBE 独立实现，仅作样式参照。
