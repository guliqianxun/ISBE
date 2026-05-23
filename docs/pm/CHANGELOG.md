---
pm_id: changelog-main
pm_type: changelog
project: ISBE
source_of_truth: markdown
updated_at: 2026-05-20
---

# ISBE — Changelog

> 项目级聚合历史。逐次迭代/特性的状态变更汇总于此。
> 更早的工程进度见 `docs/superpowers/PROGRESS.md` 与 `docs/superpowers/archive/`。

## 2026-05-23 (晚)

- **Milestone**: **v1.1「投递与运维」🚀 Shipped** —— 唯一计划特性 ft-001 闭环。
- **Feature**: [ft-001 邮件 digest 投递](features/ft-001-email-digest-delivery.md) → **✅ Completed**。
  用户在 server 上确认收到 `nowcasting` digest 邮件（flow `military-panda`），MVP（plaintext markdown）闭环。
- **Dispatch**: [dsp-003](communications/dsp-003-server-smtp-live-test.md) → ✅；[dsp-004](communications/dsp-004-compose-smtp-env-passthrough.md) → ✅。
- **后续**: 用户反馈 raw markdown 在邮件客户端可读性差（预期之中，dsp-002 已标 stretch goal）→ 待开 ft-002 做 HTML 渲染。

## 2026-05-23

- **Feature**: [ft-001 邮件 digest 投递](features/ft-001-email-digest-delivery.md) 代码部分完成
  ([dsp-002](communications/dsp-002-email-digest-mvp.md) → ✅；见 [rpt-002](communications/rpt-002-email-mvp-report.md))。
  分支 `feat/email-digest-delivery`，2 commit，11/11 notify 测试通过。
- **Notify**: `.env.example` 新增 7 项 `ISBE_SMTP_*`；邮件正文从「路径+excerpt」升级为承载 digest 全文。
- **Dispatch**: 新建 [dsp-003](communications/dsp-003-server-smtp-live-test.md) —— 用户在 server 上配 SMTP `.env` + 触发 nvda digest + 验收落箱。
- **Hotfix**: [dsp-004](communications/dsp-004-compose-smtp-env-passthrough.md) → ✅ ——
  server 实测发现 `.env` 改完 `up -d` 容器内仍无 `ISBE_SMTP_*`，根因是 `docker-compose.yml`
  的 `radar-worker.environment:` 块漏了透传。补 7 行后修复（[rpt-004](communications/rpt-004-compose-fix-report.md)）。
- **PM 内部经验**: 派遣 executor 时应显式要求在结束前打印分支 + 全部 commit SHA + diff + 验证命令输出，避免中间步骤掉链。该约束本次（dsp-004）有效，agent 一次性收尾。

## 2026-05-22

- **Ops**: [dsp-001](communications/dsp-001-deploy-latest-code.md) 服务器部署完成
  → ✅。`isbe-radar-worker` 用最新代码重建重启，16 个 deployment 正常 served，
  无 ImportError。验收见 [rpt-001](communications/rpt-001-deploy-report.md)。
- **Build**: 修复 `uv sync` 构建慢 —— 走阿里云 PyPI 镜像源（`--build-arg PIP_INDEX_URL`）。
- **Backlog**: 新增 `bl-006` —— Prefect server 版本错配（3.6.29 < client 3.7.0），待升级对齐。

## 2026-05-20

- **PM**: 建立 `docs/pm/` 项目管理台账（ROADMAP / features / communications）。
- **Roadmap**: 新增 v1.1「投递与运维」里程碑。
- **Feature**: 新增 [ft-001 邮件 digest 投递](features/ft-001-email-digest-delivery.md)（🚧 WIP）。
  确认 `notify` SMTP 传输层已存在并接入全部 digester；本特性范围为产品化 3 缺口。
- **Dispatch**: 派发 [dsp-001 服务器部署](communications/dsp-001-deploy-latest-code.md)
  与 [dsp-002 邮件投递 MVP](communications/dsp-002-email-digest-mvp.md)。
- **Backlog**: 记录记忆联邦（与 explore-os）、Qdrant 接入等 5 项待规划事项。
