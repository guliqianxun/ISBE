# ISBE — Information System with Backbone of Evolution

自我成长的 AI 信息搜集处理系统。

## 必读

| 文件 | 读它干嘛 |
|---|---|
| [`AGENTS.md`](AGENTS.md) | 红线 + 已锁定决策 + 模块边界（**给协作者/AI 助手的 onboarding**） |
| [`docs/superpowers/PROGRESS.md`](docs/superpowers/PROGRESS.md) | 当前执行到第几个 task、卡在哪、下一步是什么 |
| [`docs/superpowers/specs/2026-05-06-self-growing-info-system-design.md`](docs/superpowers/specs/2026-05-06-self-growing-info-system-design.md) | 完整设计文档 |
| [`docs/superpowers/plans/2026-05-06-p0-evaluation-and-skeleton.md`](docs/superpowers/plans/2026-05-06-p0-evaluation-and-skeleton.md) | P0' 实施计划（19 个 task） |

## Status
P0' — Evaluation + Skeleton（进行中，Task 1/19 完成；详见 PROGRESS.md）

## Quickstart (P0')
```bash
uv sync
docker compose up -d
uv run pytest
uv run radar --help
```

## Layout
- `src/isbe/`  Python 源码
- `docs/superpowers/specs/`  设计文档
- `docs/superpowers/plans/`  实施计划
- `memory/<uid>/`  文件式记忆（用户可手编辑）
- `workflows/`  Prefect 3 flows（P1 起）
- `templates/`  Jinja prompt 模板（P1 起）
