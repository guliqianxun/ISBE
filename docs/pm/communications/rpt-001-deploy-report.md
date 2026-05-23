---
pm_id: rpt-001
pm_type: report
workstream: ops
feature: "-"
dispatch_ref: dsp-001
status: completed
created_at: 2026-05-22
---

# 报告: 服务器更新至最新代码并重启 — 完成

## 摘要

[dsp-001](dsp-001-deploy-latest-code.md) 部署任务已在服务器（`lzhserver`，
`/mnt/nas_iscsi/isbe`）执行完成。`isbe-radar-worker` 已用含最新代码与
依赖的新镜像重建并重启，调度器正常服务。

## 执行记录

- 镜像构建：`uv sync` 慢的问题通过 PyPI 镜像源解决
  （`--build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/`）。
- worker 重建并以新镜像启动。

## 验收结果

| 验收项 | 结果 |
|--------|------|
| `isbe-radar-worker` 在跑 | ✅ `Up`，日志显示 `scheduler serve` 正常 |
| 16 个 deployment 注册 | ✅ 6 topic 的 collectors + digesters 全部 served |
| 无 ImportError（tenacity）| ✅ 日志干净，无 `ModuleNotFoundError` |
| 调度器 polling | ✅ `polling for scheduled runs` |

## 发现 / 遗留

- ⚠️ **Prefect 版本错配**：日志告警 —— `isbe-prefect-server` 跑 3.6.29，
  新 worker 镜像的 Prefect client 是 3.7.0。Prefect 提示
  "may result in unexpected behavior"。
  - 非阻塞，但建议尽快对齐。
  - 修法：`docker compose -f docker-compose.yml -f docker-compose.server.yml pull
    prefect-server && docker compose -f docker-compose.yml -f
    docker-compose.server.yml up -d prefect-server`
  - 已记入 ROADMAP Backlog `bl-006`。

## 状态变更

- `dsp-001`: pending → **completed**
- ROADMAP v1.1 / Ops：服务器部署 📋 Planned → ✅ Completed
