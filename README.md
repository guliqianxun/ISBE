# ISBE — Information System with Backbone of Evolution

自我成长的 AI 信息搜集处理系统。当前实现：临近降水预报科研订阅 topic（端到端跑通：arxiv/github 收集 → 真实 LLM digest → 周报 artifact + 草稿入 review 流）。

## Status

**P1.6 完成**（tag `p1.6-worker-service`）。基础设施 7 容器跑通，单 topic（nowcasting）端到端 verified，Prefect cron schedule 注册到 server，Langfuse trace 接入（需 UI 配 keys），Docker worker 服务可选。详细复盘看 [`docs/superpowers/PROGRESS.md`](docs/superpowers/PROGRESS.md)。

下一步：**Plan #2 — NVDA 金融日报域**，验证 Topic 抽象的复用性。

## 必读

| 文件 | 干嘛用 |
|---|---|
| [`AGENTS.md`](AGENTS.md) | 红线 + 锁定决策 + 模块边界（协作者/AI 的 onboarding） |
| [`docs/superpowers/PROGRESS.md`](docs/superpowers/PROGRESS.md) | 进度跟踪：到哪了、卡在哪、下一步 |
| [`docs/superpowers/specs/2026-05-06-self-growing-info-system-design.md`](docs/superpowers/specs/2026-05-06-self-growing-info-system-design.md) | 系统总览设计 |
| [`docs/superpowers/specs/2026-05-07-p1-sample-topics-design.md`](docs/superpowers/specs/2026-05-07-p1-sample-topics-design.md) | 三层信息模型 + nowcasting / NVDA 域设计 |
| `docs/superpowers/plans/*` | 各 phase 实施计划（P0 / P1 / P1.5 / P1.6） |

## Quickstart

### 一次性配置

```bash
cp .env.example .env
# 编辑 .env，填:
#   DEEPSEEK_API_KEY=sk-...    (或 ANTHROPIC_API_KEY，对应 ISBE_LLM_PROVIDER)
#   GITHUB_TOKEN=ghp-...       (可选，5000 req/hr)
#   LANGFUSE_PUBLIC_KEY=...    (可选，trace LLM 调用)
#   LANGFUSE_SECRET_KEY=...

uv sync --all-extras
docker compose up -d                # 7 服务：postgres/qdrant/minio/langfuse(+db)/prefect-server/uptime-kuma
uv run alembic upgrade head         # 建 facts 表
uv run pytest                        # 52 tests，应全绿
```

### 路线 A — host process（最简）

```bash
# 立即试一次
uv run radar topics run nowcasting --collect                       # arxiv + github → facts
uv run radar topics run nowcasting --download-pdfs --pdf-limit 5   # 5 PDF → MinIO + papers/
uv run radar topics run nowcasting --digest                        # → artifact + .pending memory
uv run radar review memory                                          # 列 .pending
uv run radar review memory --accept topics/nowcasting.theses.md   # accept 一条
uv run radar memory reindex                                         # 重写 MEMORY.md

# 长期跑（自动化）
uv run radar scheduler serve   # 长进程；4 个 cron 自动触发
```

### 路线 B — Docker worker（自动重启）

需在 Docker Desktop → Settings → Resources → File sharing 加 `F:\`，然后：

```bash
docker compose --profile worker up -d --build
docker logs -f isbe-radar-worker
```

### 默认 cron schedule

| Flow | 时间 | 用途 |
|---|---|---|
| `arxiv-collector` | 每日 06:00 | 拉 cs.LG nowcasting 关键词论文（最多 50） |
| `github-collector` | 每日 06:30 | 刷新 6 个跟踪仓库元数据 |
| `arxiv-download-pdfs` | 周一 07:00 | 下载没下过的 PDF（≤20 篇） |
| `nowcasting-weekly-digester` | 周一 08:00 | LLM 生成周报 + 蒸馏建议 |

## 用户可见的产出位置

| 路径 | 内容 |
|---|---|
| `memory/me/topics/`、`feedback/`、`user/` | 你的偏好 + 论点（手编辑或 review accept） |
| `memory/me/MEMORY.md` | 自动生成的 memory 索引 |
| `memory/me/.pending/` | agent 提议、待 review 的 memory 草稿 |
| `papers/<arxiv_id>.pdf` | 下载的论文全文（MinIO 镜像） |
| `artifacts/<topic>/<period>/<id>.md` | 周报 artifact（MinIO + PG 镜像） |

## 服务 UI

| URL | 谁 | 默认登录 |
|---|---|---|
| http://localhost:4200 | Prefect (flow runs / deployments) | — |
| http://localhost:3000 | Langfuse (LLM trace) | 自己注册 |
| http://localhost:9001 | MinIO console | isbe / changeme123 |
| http://localhost:3001 | Uptime Kuma | 自己设置 |

## Layout

```
src/isbe/
├── facts/          # SQLAlchemy ORM 基础（Base, db_url, artifacts/topic_runs）
├── memory/         # 文件式 memory 模型 / loader / lint / lifecycle / pending
├── topics/         # Topic 抽象
│   ├── base.py     # Protocol + dataclasses
│   ├── registry.py # 扫盘发现 topic.yaml
│   └── nowcasting/ # 第一个 topic 实现：collectors + digester + 模板
├── llm/            # anthropic / deepseek 双 provider + Langfuse @observe
├── artifacts/      # MinIO + PG + 本地三写
├── observability/  # topic_run context manager
├── workflows/      # hello_world (P0 烟雾)
├── cli/            # radar 入口 (review/memory/topics/scheduler)
└── scheduler.py    # serve() 4 deployments

memory/me/          # 你的记忆（git tracked，gitignore .pending/.audit/.archive）
papers/             # 论文 PDF 本地镜像（gitignored）
artifacts/          # digest 本地镜像（gitignored）
docs/superpowers/   # spec / plan / progress
docker-compose.yml  # 7 服务 + 可选 radar-worker（profile=worker）
Dockerfile          # uv-based worker image
alembic/            # DB schema migrations
```

## 设计要点（不读 spec 的话也要知道）

1. **三层信息模型**：facts（外部世界，可重抓）/ memory（你的状态、偏好、论点）/ artifacts（产出归档，不进 prompt）—— 永不混用
2. **Digester 三段约定**：每份 digest 必须分 `## 事实 / ## 分析 / ## 蒸馏`，蒸馏段产出 `.pending` 草稿等你 review
3. **Memory 文件即原子**：一条 memory 一文件，frontmatter 校验，git 友好
4. **Topic 接口同构**：collector 写 facts，digester 读 facts × memory 写 artifact + 蒸馏 —— 第二个域加进来不该改这个接口（Plan #2 验证）
5. **调度走 Prefect**：所有自动化是 cron 触发的 Prefect flow，不是 LLM 决策循环

## 开发约定

- **TDD**：每个 task 先写 failing test
- **Conventional commits**：`feat:` / `chore:` / `docs:` / `fix:`
- **Docker**：基础设施用 compose，应用代码用 uv（host）或 worker 容器
- **不 push**：本地 commit 即可；用户决定何时推
- **测试**：`uv run pytest`（52 tests）；ruff `uv run ruff check src/ tests/`

## 已知短板（P2 候选）

- Plan #2 NVDA 域未做（抽象正确性的真正考验）
- Qdrant 未用（MVP 只按时间窗筛 facts，没语义检索）
- hermes-agent 评估补办（P0 Task 15-19，单独 spec 处理）
- accept 时未跑 lint（坏 frontmatter 也能被接进 memory）
- 旧 TRACKED_REPOS 数据未清理逻辑

## License

MIT — see [`LICENSE`](LICENSE).
