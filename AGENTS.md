# AGENTS.md — ISBE 项目工作指南

> 给在这个仓库工作的 AI 助手 / 协作者：先读这里。本文描述的是**当前实际架构**；
> 更早的设计史（hermes 路线评估、P0-P2 阶段计划）见 `docs/superpowers/archive/`，
> 仅作历史参考，不再是约束。

---

## 一句话项目定位

**ISBE** = Information System with Backbone of Evolution。
单人优先、Docker-first 的私人研究雷达：topic.yaml 驱动的采集器抓 facts（arxiv /
GitHub / RSS / 股价 / SEC）→ LLM digester 写周报 → 蒸馏建议经人工 review 沉淀为
文件式 memory → 下期周报基于 memory 做对照分析。

---

## 实际架构（数据流单向）

```
topic.yaml (src/isbe/topics/<id>/)
   │  schedules: {schedule_key: cron}
   ▼
dispatch.py  ——  (topic_id, schedule_key) → (flow, params) 唯一接缝
   │            1. _SHARED_COLLECTORS（arxiv/rss/crawl4ai/metrail_enrich）
   │            2. topics/<id>/collectors/*.py 里同名 callable
   │            3. "digester" → topics/<id>/digester.py:digest，缺省回退
   │               _shared/digester.py:weekly_digester
   ▼
collectors → Postgres facts（papers/repos/articles/news/filings）
   ▼
digester → LLM（llm/client.py，deepseek|anthropic 可切）→ 五段周报
   ▼
artifacts（MinIO + 本地镜像 artifacts/<topic>/<period>/latest.md）
   + memory 蒸馏草稿（memory/<uid>/.pending/）
   + 邮件推送（notify/，HTML 经 nh3 消毒）
```

关键目录：

```
src/isbe/
├── topics/          # 每 topic 一个目录；_shared/ 是通用 collector/digester
│   ├── dispatch.py  # 调度接缝（加 topic 不需要改任何 Python）
│   ├── config.py    # topic.yaml 的 typed schema（extra=forbid）
│   └── registry.py  # topic 发现
├── llm/             # provider 切换 + prompts（llm/prompts.py 五段契约）
├── memory/          # frontmatter 解析 / loader / lint / pending 审核流
├── facts/           # SQLAlchemy Base + articles 表
├── artifacts/       # 周报落盘（MinIO + 本地镜像）
├── notify/          # 邮件渲染（学术墨色）+ SMTP
├── triage/          # 检索契约（相关性门 + 分层）
├── observability/   # topic_run 运行记录
├── scheduler.py     # Prefect serve（从 topic.yaml 建 deployment）
└── cli/             # `radar` Typer 入口
alembic/             # 迁移（001-005，线性）
memory/<uid>/        # 用户数据：topics/reading/feedback/user/reference + .pending/.audit
```

---

## 不可违反的约束

1. **单向数据流**：采集 → 处理 → 存储 → 生成；禁止反向调用。
2. **memory 写入永远走草稿审核流**：agent 提议 → `.pending/` → `radar review
   memory` accept/reject → 落正式目录。**绝不写"agent 直接覆盖正式目录"的代码
   路径**；accept 对已有文件做合并 + revision 递增，不做整文件覆盖。
3. **人有绝对优先权**：memory 冲突时 LLM 只给只读建议，用户改动赢。
4. **确定式 Workflow 跑周期任务**：cron 任务全部是 Prefect flow + 固定 prompt
   模板；不用 LLM 决策每步的 ReAct 循环跑日报。
5. **可复现性**：LLM 调用经 `llm/client.py`（Phoenix trace + 重试）；不要绕过它
   直接 POST provider API。
6. **五段周报契约**（TL;DR / 论文逐篇 / 仓库逐条·名词 / 分析 / 蒸馏）是跨 topic
   的统一接口，不要单 topic 私改。
7. **外部输入不可信**：RSS / 爬虫 / LLM 输出都是不可信文本——渲染进邮件前必须
   过 nh3；LLM 给出的文件路径必须过 `_safe_join` 一类的越界校验。

---

## 已锁定的技术决策（当前有效）

| 维度 | 决策 |
|---|---|
| 工作流引擎 | Prefect 3 Python flows（cron 定义在 topic.yaml schedules） |
| 记忆系统 | 文件式 markdown + frontmatter（`memory/<uid>/*.md`），非数据库 |
| LLM 接入 | 自写 `llm/client.py`（deepseek/anthropic + tenacity + Phoenix），无网关框架 |
| PDF 全文 | metrail-web（局域网 HTTP 服务，`METRAIL_API_URL`），未设则摘要-only |
| 通知 | 自写 SMTP（`notify/`），HTML 邮件 premailer 内联 + nh3 消毒 |
| 存储 | Postgres(facts) + MinIO(blob) + 本地镜像目录（人看的入口是本地镜像） |

---

## 开发命令

```bash
uv sync --all-extras
docker compose up -d               # 6 个基础设施容器
uv run alembic upgrade head
uv run pytest                      # 全部单测（integration 标记的需要基础设施）
uv run radar --help
uv run ruff check src/ tests/
```

约定：TDD（先 failing test）；conventional commits；不自动 push。

---

## Environmental quirks

- Windows 平台开发：Bash 工具用 `/e/...` 风格路径，Edit/Read/Write 用 `E:\...`。
- 仓库配有 origin remote；push 前 `git remote -v` 确认。
- Python 3.11+ via uv。

---

## 陷入选择困难时

1. 对照上面"不可违反的约束"——冲突的选项即错。
2. 已锁定决策不重新讨论，除非有新证据。
3. 仍不清楚 → 问用户，不要猜。
