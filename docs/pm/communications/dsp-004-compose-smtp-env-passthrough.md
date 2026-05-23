---
pm_id: dsp-004
pm_type: dispatch
target: ops
action: fix
feature: ft-001
priority: P1
status: completed
created_at: 2026-05-23
deadline: 2026-05-24
completed_at: 2026-05-23
---

# 任务: 修复 docker-compose 未透传 `ISBE_SMTP_*` 到 radar-worker

## 背景

[dsp-003](dsp-003-server-smtp-live-test.md) 实测时发现:用户编辑 server `.env` 加了
7 项 `ISBE_SMTP_*`,跑 `docker compose ... up -d radar-worker` 后输出
`✔ Container isbe-radar-worker Running`（**不是 Recreated**），容器内
`env | grep ISBE_SMTP` 仍为空。

根因：`docker-compose.yml` 的 `radar-worker.environment:` 块**没有列出**
`ISBE_SMTP_*`。compose 的根目录 `.env` 只做 YAML 中 `${VAR}` 替换，**不会**
自动注入容器；必须在 `environment:` 显式列出 `ISBE_SMTP_X: ${ISBE_SMTP_X:-}`
才会进容器。

dsp-002 加了 `.env.example` 的文档但漏了 compose 透传，本 dispatch 补这个洞。

## 要求

仅改一文件：`docker-compose.yml` 的 `radar-worker` 服务 `environment:` 块。

在已有的 `GITHUB_TOKEN: ${GITHUB_TOKEN:-}` / `ISBE_UID: ${ISBE_UID:-me}` 旁追加：

```yaml
      ISBE_SMTP_HOST: ${ISBE_SMTP_HOST:-}
      ISBE_SMTP_PORT: ${ISBE_SMTP_PORT:-587}
      ISBE_SMTP_FROM: ${ISBE_SMTP_FROM:-}
      ISBE_SMTP_TO: ${ISBE_SMTP_TO:-}
      ISBE_SMTP_USER: ${ISBE_SMTP_USER:-}
      ISBE_SMTP_PASS: ${ISBE_SMTP_PASS:-}
      ISBE_SMTP_ALLOW_PLAINTEXT: ${ISBE_SMTP_ALLOW_PLAINTEXT:-}
```

默认值刻意都用空（除 `PORT` 用 587），契合 `notify.is_configured()` 在缺失
时 no-op 的契约。

## 不做

- 不动 `.env.example`（dsp-002 已写）
- 不动 `notify` 代码或测试
- 不改 `docker-compose.server.yml` 或 `docker-compose.override.yml`
- 不 push、不 merge

## 验收标准

- `docker-compose.yml` 的 `radar-worker.environment:` 含 7 项 `ISBE_SMTP_*`
- `python -c "import yaml; yaml.safe_load(open('docker-compose.yml'))"` 不报错
  （YAML 语法合法）
- 分支 `fix/compose-smtp-env` 上 1 个 conventional commit

## 参考

- 关联特性：[ft-001](../features/ft-001-email-digest-delivery.md)
- 前序：[dsp-002](dsp-002-email-digest-mvp.md) + [dsp-003](dsp-003-server-smtp-live-test.md)
- compose 行为参考：https://docs.docker.com/compose/environment-variables/envvars-precedence/

## 结果（2026-05-23）✅ completed

- 分支 `fix/compose-smtp-env`，commit `82f481d`，已 merge 入 main（merge commit `26d47a6`）
- `docker-compose.yml` +7 行，缩进对齐，YAML parse OK
- 验收（agent 回报）：
  - [x] `radar-worker.environment:` 含 7 项 `ISBE_SMTP_*`
  - [x] `python -c "import yaml; yaml.safe_load(...)"` 不报错
  - [x] 分支只有 1 个 conventional commit
- 报告：[rpt-004](rpt-004-compose-fix-report.md)
