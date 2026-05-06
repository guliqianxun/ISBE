# ISBE — Information System with Backbone of Evolution

自我成长的 AI 信息搜集处理系统。

详见 `docs/superpowers/specs/2026-05-06-self-growing-info-system-design.md`。

## Status
P0' — Evaluation + Skeleton（进行中）

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
