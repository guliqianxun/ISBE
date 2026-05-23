---
pm_id: dsp-002
pm_type: dispatch
target: notify
action: develop
feature: ft-001
priority: P1
status: completed
created_at: 2026-05-20
deadline: 2026-05-23
completed_at: 2026-05-23
---

# 任务: 邮件 digest 投递 MVP —— nvda 单 topic 试水

## 背景

`notify` SMTP 传输层已存在并接入 digester（见 [ft-001](../features/ft-001-email-digest-delivery.md)），
但有三处缺口使它还不是一封"可用的订阅邮件"。本任务做 MVP：先把 `nvda`（日更 topic）
的邮件投递跑通、验证真实落箱。

## 要求

1. **补全 `.env.example`** —— 新增 `ISBE_SMTP_*` 段，逐项注释：
   - `ISBE_SMTP_HOST` / `ISBE_SMTP_FROM` / `ISBE_SMTP_TO`（三项必填）
   - `ISBE_SMTP_PORT`（默认 587=STARTTLS；465=SMTP_SSL）
   - `ISBE_SMTP_USER` / `ISBE_SMTP_PASS`（可选，设了才登录）
   - `ISBE_SMTP_ALLOW_PLAINTEXT`（=1 才允许明文发送）

2. **邮件正文承载完整 digest** —— 改 `send_digest_notification()`（或其调用层）：
   正文放入当期 `latest.md` 全文，而非现在的"路径 + 一段 excerpt"。
   收件人不碰服务器即可读完整期内容。纯文本可接受；HTML 渲染留作拉伸目标。
   - 保持契约：`notify` 绝不 raise；env 缺失即 no-op。

3. **服务器配置 + 实测** —— 在服务器 `.env` 填 `ISBE_SMTP_*`（`ISBE_SMTP_TO`
   先填自己），触发一次 `nvda` digest，确认收件箱真实收到含全文的邮件。
   - 注意：本步依赖 [dsp-001](dsp-001-deploy-latest-code.md) 已完成（服务器跑新代码）。

## 验收标准

- `.env.example` 含 7 项 `ISBE_SMTP_*` 变量及注释
- 触发 `nvda` digest 后，收件箱收到一封邮件，**正文可直接读到当期 digest 全文**
- env 未配时 digest 流程仍正常完成（no-op 回归不破）
- `notify` 相关单测通过（`uv run pytest`）

## 参考

- Feature spec: [ft-001](../features/ft-001-email-digest-delivery.md)
- 现有实现: `src/isbe/notify/__init__.py`；调用点 `src/isbe/topics/_shared/digester.py:201`
- 前置依赖: [dsp-001](dsp-001-deploy-latest-code.md)（服务器部署）

## 结果（2026-05-23）✅ completed（代码部分）

- 分支：`feat/email-digest-delivery`（worktree `.claude/worktrees/agent-a17179ded6f9ae6a7/`）
- 2 个 commit：
  - `b931b2c docs(env): document ISBE_SMTP_* in .env.example`
  - `2870ef2 feat(notify): embed full digest body in email when artifact available`
- 文件改动 `git diff --stat main..HEAD`：3 文件 +113 / −9
  - `.env.example`（+16）— 七项 `ISBE_SMTP_*` 全部注释列出
  - `src/isbe/notify/__init__.py`（+38 / −9）— `artifact_path` 可读时正文嵌入全文，否则退回 excerpt；契约（不 raise / no-op）保持
  - `tests/test_notify_email.py`（+68，新文件）— 11 个单测覆盖
- 测试：notify 单测 **11/11 通过**；整套 pytest 157 通过、3 失败（`test_facts_db.py` + `test_nowcasting_facts.py` 的 2 项，均为 Postgres 连接超时 —— 本机无 PG，非本次变更引入）
- 验收（dsp-002 的代码部分）：
  - [x] `.env.example` 含 7 项 `ISBE_SMTP_*`
  - [x] 邮件正文承载 digest 全文（已被新单测断言）
  - [x] env 未配时 digest 流程仍正常完成（已有 `is_configured()` 路径 + 新增 no-op 测试）
  - [x] notify 相关单测通过
- 报告：[rpt-002](rpt-002-email-mvp-report.md)
- **遗留 step 3（服务器配置 + 实测）→ 分派为新单 [dsp-003](dsp-003-server-smtp-live-test.md)，由用户在 server 上执行。**
