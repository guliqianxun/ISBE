---
pm_id: ft-002
pm_type: feature
title: 邮件 HTML 渲染（学术墨色 brand）
status: planned
priority: P1
milestone: v1.2
parent: ep-delivery
domain: [notify]
depends_on: [ft-001]
blocks: []
index_file: ../index.json
managed_by: skills/project-manager/scripts/pm_index.py
source_of_truth: markdown
tags: [feature, delivery, email, html, branding]
created_at: 2026-05-23
---

# 邮件 HTML 渲染（学术墨色 brand）

> **所属**: [v1.2 邮件订阅体验升级](../ROADMAP.md) > Epic: 主动投递
> **状态**: 📋 Planned
> **优先级**: P1
> **涉及工作流**: notify
> **创建日期**: 2026-05-23
> **索引 ID**: `ft-002`

## 概述

[ft-001](ft-001-email-digest-delivery.md) MVP 落地后实测：纯 markdown 正文在大多
数邮件客户端里以 raw 文本显示（`#` / `*` / `|` 都没渲染），可读性差。本特性把
邮件正文升级为 **HTML（multipart/alternative，HTML + plaintext 双轨）**，并借这次
为 ISBE 第一次定义视觉品牌：**学术墨色**。

## 品牌规范（学术墨色 / Academic Ink）

> ISBE 首版邮件视觉系统。设计灵感 Edward Tufte / Stripe Press / Brilliant.org。

### 调色板

| 角色 | Token | Hex | 备注 |
|------|-------|-----|------|
| 背景 | `--isbe-bg` | `#fdfcf8` | 象牙白，比纯白柔和，护眼 |
| 正文 | `--isbe-ink` | `#1a1a1a` | 近黑非黑，降低对比烫眼感 |
| 次要文字 | `--isbe-ink-2` | `#666666` | 元数据、脚注 |
| 强调 / 链接 | `--isbe-accent` | `#0d6e6e` | 深青，沉静学术感 |
| 分隔线 | `--isbe-rule` | `#e0ddd5` | 暖灰，与象牙白同色系 |
| 代码块底 | `--isbe-code-bg` | `#f4f1ea` | 比 bg 略深的米色 |

不引夜间模式（v1.x 不做）。

### 字体栈

```css
font-family: "Source Serif 4", "Source Han Serif SC", Charter, Lora,
             Georgia, "Songti SC", STSong, serif;
font-family-mono: "JetBrains Mono", "SF Mono", Consolas, Menlo, monospace;
```

不引外链字体（Google Fonts 等邮件客户端常 strip）。完全靠系统字体 fallback；
中英文都走衬线，统一气质。

### 排版

- 容器最大宽度 **640px**，居中
- 行高 1.65（中文友好）
- 段落上下 margin 1em
- H1 1.5em / H2 1.25em / H3 1.1em，全部 serif、同色（不用 accent 染色）
- 引用块：左侧 3px accent 实线 + 左 padding + 略灰文字
- 表格：上下 1px ink 线、行间 0.5px rule 线，无竖线
- 链接：accent 色 + dotted underline；hover 没有（邮件无 hover）
- 代码块：code-bg 底 + 4px 圆角 + 12px padding，mono 字体
- 行内 code：code-bg 底 + 2px 横向 padding

### 信封（envelope）

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ISBE · NOWCASTING WEEKLY
2026-W21 · 5月18–23日
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
（顶 banner：上下双 ink 实线，中间 topic_label + period_label 两行 serif）

{{ digest 全文渲染 }}

· · ·
ISBE · self-hosted research radar
（底 footer：三个居中圆点 + 一行 ink-2 灰字）
```

## 范围

### MVP

1. **Markdown → HTML**：`markdown` 库（标准 Python），启用 `tables` / `fenced_code`
   / `footnotes` 扩展。
2. **品牌化模板**：jinja2 模板（已是 dep）实现「学术墨色」envelope；
   `premailer` 库把 `<style>` 转 inline，确保邮件客户端 strip `<style>` 后仍生效。
3. **multipart/alternative**：`EmailMessage.add_alternative(html, subtype="html")`，
   text part 用现有 plaintext（保 fallback）。
4. **降级策略**：HTML 渲染抛异常 → 退回纯 plaintext + WARN（契约：`notify` 不
   raise，digest 不 fail）。

### 拉伸目标（不在本期）

- 周报 5 段（TL;DR / 逐条点评 / 跨条对照 / 分析 / 蒸馏）的语义化模板
  —— 让不同段落有更精致的视觉层级（需 digest 输出结构化数据，跨域改动）
- dark mode（v2.x）
- 头部 LOGO（先用纯字体 wordmark）

### 不做

- 不复用 explore-os `delivery/email_renderer` 代码（2026-05-20 锁定：只做记忆联邦，不抽共享库）
- 不引外部字体或图片资源
- 不改 digester 输出的 markdown 内容（只改"发送层"渲染）
- 不动 `is_configured()` / no-op 契约
- 不做多收件人（→ bl-005）

## 需求

### 功能需求
- digest 邮件在主流客户端（Gmail Web / 网易/QQ Web / iOS Mail / Outlook 2019+）显示带样式 HTML，正文清晰可读
- 不支持 HTML 的客户端自动 fallback 到 plaintext（multipart/alternative 标准行为）
- 链接保留可点击，accent 色明显
- 表格、代码块、引用块都正确渲染

### 非功能需求
- HTML 渲染 + premailer transform 整体在 1 秒内（不阻塞 digest 流程）
- 邮件总大小 < 200KB（含 plaintext + HTML 两份）
- HTML 文档自洽，零外部资源请求

## 验收标准

- 收件箱实测：3 主流客户端（你常用的）看上去都是带样式的 HTML，不是 raw markdown
- 容器宽度 640px，象牙白底，深青 accent 出现在链接与引用边线
- 引用块、表格、代码块、行内 code 都正确渲染
- 关 HTML 客户端（如纯文本邮件客户端 / mutt 默认 view）退回 plaintext
- 单测：HTML 包含预期 inline style；multipart 两部分；渲染异常的 fallback 路径
- `uv run pytest` 全绿（notify 测试数从 11 增到 ≥ 18）

## 任务分工

| 工作流 | 职责概述 | Assignment |
|-------|---------|------------|
| notify | markdown→HTML 渲染层 + 学术墨色模板 + multipart + 测试 | [dsp-005](../communications/dsp-005-email-html-mvp.md) |
| user/ops | 触发 nowcasting / nvda digest 实测 + 跨客户端肉眼验收 | 整合在 dsp-005 验收里 |

## 依赖关系

- depends_on: [ft-001](ft-001-email-digest-delivery.md)（plaintext 投递已闭环）
- 新增依赖：`markdown`、`premailer`（pyproject + uv.lock）

## 变更记录

| 日期 | 变更内容 |
|------|---------|
| 2026-05-23 | 初始创建。ft-001 实测后用户反馈 raw markdown 可读性差；定品牌方向「学术墨色」；范围锁 MVP（markdown lib + premailer + jinja2 + multipart） |

## 备注

- 这是 ISBE 第一次定义视觉品牌。本文件的「品牌规范」段落事实上充当 brand guidelines，
  后续如有第二个需要品牌化的产物（如 web dashboard），应回引本规范，保持一致。
- explore-os 已有成熟 HTML 渲染（`delivery/email_renderer`），按 2026-05-20 决策不复用，
  仅样式参照。
