---
pm_id: rpt-004
pm_type: report
workstream: ops
feature: ft-001
dispatch_ref: dsp-004
status: completed
created_at: 2026-05-23
---

# 报告: docker-compose `ISBE_SMTP_*` 透传修复 — 完成

## 摘要

[dsp-004](dsp-004-compose-smtp-env-passthrough.md) 已由 executor agent 完成。
`docker-compose.yml` 的 `radar-worker.environment:` 块现在显式列出 7 项
`ISBE_SMTP_*`，server 编辑 `.env` 之后 `up -d radar-worker` 会真正 recreate
容器并把变量注入。

## 交付

- 分支：`fix/compose-smtp-env`（commit `82f481d`） → 已 merge 至 main（merge commit `26d47a6`）
- 改动：`docker-compose.yml` +7 行（`radar-worker.environment:` 块）

`diff` hunk：

```diff
@@ services:
       DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY:-}
       GITHUB_TOKEN: ${GITHUB_TOKEN:-}
       ISBE_UID: ${ISBE_UID:-me}
+      ISBE_SMTP_HOST: ${ISBE_SMTP_HOST:-}
+      ISBE_SMTP_PORT: ${ISBE_SMTP_PORT:-587}
+      ISBE_SMTP_FROM: ${ISBE_SMTP_FROM:-}
+      ISBE_SMTP_TO: ${ISBE_SMTP_TO:-}
+      ISBE_SMTP_USER: ${ISBE_SMTP_USER:-}
+      ISBE_SMTP_PASS: ${ISBE_SMTP_PASS:-}
+      ISBE_SMTP_ALLOW_PLAINTEXT: ${ISBE_SMTP_ALLOW_PLAINTEXT:-}
```

## 派遣过程小记（PM 留档）

派遣单里**显式要求 agent 在结束前打印分支 + 全部 commit SHA + diff + YAML 验证**
（吸取 rpt-002 的教训）→ agent 一次性收尾完整，无遗留未提交修改。该模板已成为
PM 派遣 executor 的标准要求。

## 根因复盘

dsp-002 在补 `.env.example` 文档时漏了 compose 透传 —— 文档约定和实际容器
环境之间的"中间一跳"被忽略。未来类似 PR（向 `.env.example` 加 env 项）应
同时检查 `docker-compose.yml` 的 `environment:` 块是否也需要补对应行。

## 用户下一步（自动续上 dsp-003）

1. 拉最新代码（server）：`git pull --ff-only origin main`
2. `up -d radar-worker` —— 这次应见 `Recreated`，不是 `Running`
3. `exec env | grep ISBE_SMTP` 应输出 7 行（前提是 `.env` 已配）
4. 触发 digest → 看邮箱
