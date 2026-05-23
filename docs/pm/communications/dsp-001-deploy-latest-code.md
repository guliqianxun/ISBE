---
pm_id: dsp-001
pm_type: dispatch
target: ops
action: deploy
feature: "-"
priority: P1
status: completed
created_at: 2026-05-20
deadline: 2026-05-21
completed_at: 2026-05-22
---

# 任务: 服务器更新至最新代码并重启 radar-worker

## 背景

服务器（LAN `192.168.0.156`，repo 在 `/mnt/nas_iscsi/isbe/`）上的 `isbe-radar-worker`
已运行 9 天，跑的是 9 天前构建的 `isbe/radar:latest` 镜像。其后主分支累积了多个提交
（最新 `d2b3bee`），需要更新部署。

**关键：本次必须重建镜像，不能只重启。**

- `radar-worker` 把 `./src` bind-mount 进容器，Python 源码改动重启即生效。
- 但 `tenacity>=9.0` 依赖在 **2026-05-18**（commit `a3769a9`）才加入 `pyproject.toml`，
  晚于当前镜像的构建时间。依赖装在镜像内的 `/app/.venv`（**未** bind-mount）。
- 若只重启，bind-mount 的新代码会 `import tenacity` 失败 → worker 崩溃。
- → 必须 `--build` 重建镜像，让新 venv 含 tenacity。

执行方式：由项目所有者在服务器上 SSH 手动执行（PM 决策 2026-05-20）。

## 要求

在服务器 `/mnt/nas_iscsi/isbe/` 下依次执行：

```bash
cd /mnt/nas_iscsi/isbe

# 1. 预览并拉取最新代码
git fetch origin
git log --oneline HEAD..origin/main          # 应看到截至 d2b3bee 的提交
git pull --ff-only origin main

# 2. 重建 radar 镜像（含 tenacity 等新依赖）
docker compose -f docker-compose.yml -f docker-compose.server.yml \
  --profile worker build radar-worker

# 3. 应用 DB 迁移（幂等；近 10 个提交未新增 alembic 迁移，预期 no-op，可安全执行）
docker compose -f docker-compose.yml -f docker-compose.server.yml --profile worker \
  run --rm --entrypoint /app/.venv/bin/alembic radar-worker upgrade head

# 4. 用新镜像重建并重启 worker
docker compose -f docker-compose.yml -f docker-compose.server.yml \
  --profile worker up -d radar-worker
```

> 基础设施容器（postgres / minio / prefect / phoenix / rsshub / morerssplz /
> qdrant / uptime-kuma / homepage / filebrowser / pgweb）都是 stock 镜像，
> **不携带项目代码，无需重建或重启**。只动 `radar-worker`。

## 验收标准

```bash
# A. worker 在跑且新镜像
docker compose -f docker-compose.yml -f docker-compose.server.yml --profile worker ps
docker image inspect isbe/radar:latest --format '{{.Created}}'   # 应是今天

# B. 启动日志干净
docker logs isbe-radar-worker --tail 60
```

- `isbe-radar-worker` 状态 `Up`，镜像构建时间为今天
- 日志显示 `scheduler serve` 启动、cron 已注册
- **日志中无 `ModuleNotFoundError: tenacity` 或其它 ImportError**
- Prefect UI（`http://192.168.0.156:4200`）中 worker 在线、flow 正常

## 回滚

新镜像异常时：`git checkout <上个提交>` → 重复 step 2 + 4。
旧镜像若未被覆盖可直接 `docker compose ... up -d` 回退（建议重建前先
`docker tag isbe/radar:latest isbe/radar:backup-9d` 留底）。

## 参考

- 部署命令出处：`docker-compose.server.yml` 文件头注释
- tenacity 依赖引入：commit `a3769a9`（2026-05-18）
- 关联特性：[ft-001 邮件 digest 投递](../features/ft-001-email-digest-delivery.md)
  —— 其线上验证依赖本次部署

## 结果（2026-05-22）✅ completed

- worker 重建并重启成功，`isbe-radar-worker` 日志输出
  `Your deployments are being served and polling for scheduled runs!`
- 16 个 deployment 全部注册（6 topic 的 collectors + digesters）
- **无 `ImportError` / `ModuleNotFoundError`** —— tenacity 已随新镜像 venv 装入
- 验收报告：[rpt-001](rpt-001-deploy-report.md)
- ⚠️ 发现一项：Prefect 版本错配（server 3.6.29 < client 3.7.0）→ 已记入
  ROADMAP Backlog `bl-006`，非阻塞
