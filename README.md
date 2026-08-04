# ISBE — 你的私人研究助理

> Information System with Backbone of Evolution

[![CI](https://github.com/guliqianxun/ISBE/actions/workflows/ci.yml/badge.svg)](https://github.com/guliqianxun/ISBE/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

**中文** | [English](README.en.md)

交付节奏：**日报**（如 NVDA 金融日报）· **周报**（主力，见下）· **月报**（每月 1 号，
汇总当月周报 + 论点演化）。

每周一早上，打开邮箱，你会看到一份周报：
- **你关心的所有领域，过去 7 天发生了什么**（不是简讯堆叠，是 LLM 阅读完所有原文后写的人话总结）
- **逐条点评**：哪几篇值得读、哪些是噪音、为什么
- **跟你"论点库"的对照**：你之前认为"X 趋势会发生"，这周的证据是否在加强 / 削弱它
- **蒸馏建议**：基于这周的新事实，提议给你的论点库加一条 / 改一条 —— 你 accept 或 reject

下一周再来一份。**月复一月，你的论点库就长成了你的研究观。**

---

## 它在解什么问题

你大概是这种人：

- 关注多个领域（科研某个子方向、二级市场、产业新闻、垂类爱好 ...），但**没时间每天巡逻**
- 试过 Feedly / RSS reader / X List —— 信源够了，但**读不过来**，最后变成"未读 999+"
- 试过 ChatGPT 问"最近 X 领域有什么进展" —— 答案泛、信源不可信、**没有持续记忆**
- 真正想要的是：**让信息流向我，而不是我去追**；并且这个系统**长得像我**，记得我相信什么、不信什么

ISBE 就是为此造的。

它不是聊天机器人。它是**一个跑在你机器上的周期性管线**：抓数据 → 用 LLM 把你这周需要看的东西总结好 → 把"你信什么"沉淀成可累积的 memory → 下次周报基于你的 memory 做对照分析。

---

## 它现在能干什么

6 个活跃 topic，每个都是一行 yaml 起步，自动跑、自动出周报：

| Topic                 | 信源                                  | 周期 | 当前用途                          |
|-----------------------|---------------------------------------|------|-----------------------------------|
| `nowcasting`          | arxiv + github                        | 周   | 临近降水预报方向科研订阅          |
| `video-generation`    | arxiv                                  | 周   | 视频生成模型科研订阅              |
| `image-restoration`   | arxiv                                  | 周   | 图像修复科研订阅                  |
| `nvda`                | 股价 + 新闻 + SEC 文件                | 日   | NVDA 金融日报                     |
| `motorcycle`          | 国外摩托车媒体 RSS + 可选爬虫        | 周   | 250cc 摩托买车决策                |
| `china-tech`          | 36 氪 + 知乎专栏（机器之心/量子位）   | 周   | 国内科技 / 创投周报               |

加一个新 topic = 写一份 `topic.yaml` + 选一个内置 collector 套餐。零代码场景一晚上就能起来。

支持的信源套餐（开箱即用，无需写爬虫）：

- **arxiv**：按关键词订阅论文 + 自动下 PDF
- **GitHub**：跟踪指定仓库的 release / stars / commits
- **RSS**：任何标准 RSS feed
- **RSSHub**（自托管）：把没 RSS 的站（微博 / B 站 / 雪球 ...）变成 RSS
- **morerssplz**（自托管）：知乎专栏，无需 cookies
- **Crawl4AI**：headless Chrome + LLM 抽取，应对反爬强的站
- **股价 / SEC**：金融数据接入

---

## 怎么用

### 第一次安装（一杯咖啡）

```bash
git clone <repo> && cd ISBE
cp .env.example .env
# 填两个 key：
#   DEEPSEEK_API_KEY=...      # 或 ANTHROPIC_API_KEY，决定 ISBE_LLM_PROVIDER
#   GITHUB_TOKEN=...          # 可选，给 5000 req/hr 余量

uv sync --all-extras
docker compose up -d                 # 启 6 个基础设施容器
uv run alembic upgrade head          # 建表
uv run pytest                         # 应全绿
```

### 立即试一份周报

```bash
uv run radar topics run nowcasting --collect    # 拉 arxiv + github → 入库
uv run radar topics run nowcasting --digest     # LLM 阅读 → 写周报
```

打开 `artifacts/nowcasting/<本周>/latest.md` —— 这就是你的第一份周报。

### Review 蒸馏建议

周报底部会有"蒸馏"段，系统提议给你的 memory 加 / 改的论点：

```bash
uv run radar review memory                                            # 列待 review 的草稿
uv run radar review memory --accept topics/nowcasting.theses.md       # accept 一条
uv run radar memory reindex                                            # 重建索引
```

Accept 进去的论点会进入 `memory/me/topics/*.md`，下次周报的 prompt 自动带上。

### 让它自己跑

```bash
uv run radar scheduler serve     # 长进程，cron 自动触发所有 topic
# 或用 docker worker（开机自动重启）：
docker compose --profile worker up -d --build
```

默认 cron：

| Flow                       | 时间        | 用途                        |
|----------------------------|-------------|-----------------------------|
| arxiv / RSS collectors     | 每日 06:00  | 各 topic 拉信源             |
| arxiv-download-pdfs        | 周一 07:00  | 补全本周新论文 PDF          |
| `<topic>-weekly-digester`  | 周一 08:00+ | 生成各 topic 周报           |

### 加一个新 topic

```yaml
# src/isbe/topics/my_topic/topic.yaml
id: my-topic
label: 我的新订阅
cadence: weekly
active: true

rss:
  feeds:
    - name: source_a
      url: https://example.com/feed
  exclude_keywords: [广告, 招商]
  lookback_days: 14
  lang: zh

schedules:
  rss_collector: "0 6 * * *"
  my_topic_digester: "0 9 * * 1"
```

无需改任何 Python —— dispatch 由 `src/isbe/topics/dispatch.py` 按 schedule key 自动解析，
直接跑 `radar topics run my-topic --collect --digest` 即可。

> 注：Prefect 需要独立的 `prefect` 数据库。全新 `docker compose up` 会由
> `infra/initdb/` 自动创建；存量部署手动执行一次：
> `docker exec isbe-postgres psql -U isbe -c "CREATE DATABASE prefect;"`
>
> 可选：局域网部署了 metrail-web（PDF 全文提取）时，设 `METRAIL_API_URL`
> 即可让周报附带论文全文摘录（不设则自动降级为摘要-only）。

### 产出在哪

| 路径                                       | 内容                                       |
|--------------------------------------------|--------------------------------------------|
| `artifacts/<topic>/<period>/latest.md`     | 这一期的周报（也镜像在 MinIO + Postgres）  |
| `papers/<arxiv_id>.pdf`                    | 下载的论文 PDF                             |
| `memory/me/topics/*.md`                    | 你已 accept 的论点 / 偏好                  |
| `memory/me/.pending/`                      | 待 review 的 memory 草稿                   |
| `memory/me/MEMORY.md`                      | 自动生成的索引                             |

---

## 怎么设计的

### 三层数据，永不混用

```
facts        外部世界拉来的"原子事实"，可重抓、可删（论文/快讯/股价/SEC...）
memory       你的状态：偏好、论点、profile —— 一条一文件，git 友好
artifacts    LLM 写出来的产出（周报）—— 归档用，不喂回 prompt
```

LLM prompt 永远只读 `facts × memory`，写 `artifacts + .pending memory drafts`。**这是整个系统的 backbone**。

### Topic 接口同构

每个 topic 都遵循同一个抽象：

```
collector(s)  → 把信源写进 facts 表
digester      → 读 facts（按时间窗）× memory → 走 5 段 LLM 模板 → 写 artifact + 蒸馏 .pending
```

5 段周报模板（所有 topic 共享）：

```
1. TL;DR              一段话快速过完本期
2. 逐条点评           对本期每条事实的 1-3 行评论
3. 跨条/上期对照      （金融的"公司动态"、科研的"方向汇总"、消费的"品牌动态"...）
4. 分析               把本期事实对照 memory 里的论点，看哪些被印证 / 削弱
5. 蒸馏               提议给 memory 加 / 改的论点 → 进 .pending 等 review
```

加一个新 domain 不该改这个接口。这是 ISBE 区别于"一次性脚本集合"的核心。

### Self-hosted 一栈打包

```
postgres        facts DB + artifacts/memory metadata
minio           blob (PDF/artifact 全文)
prefect         cron 编排（所有自动化都是 Prefect flow）
phoenix         LLM trace（看每次调用的 prompt + 输出，零登录）
rsshub          非 RSS 站 → RSS（微博/B站/雪球...）
morerssplz      知乎专栏专用，无需 cookies
qdrant          预留语义检索（MVP 还未启用）
uptime-kuma     服务健康
```

UI 端口：Prefect `:4200` / Phoenix `:6006` / MinIO console `:9001` / Uptime Kuma `:3001`。

### LLM provider 可切换

`ISBE_LLM_PROVIDER=deepseek|anthropic` —— 切换就是改一个环境变量。

科研/金融用 DeepSeek 跑出来质量已经够；偶尔用 Claude 做高阶分析。Phoenix 自动 trace 两边。

---

## 进阶 / 协作者入口

| 文件                                                                                 | 谁会读                            |
|--------------------------------------------------------------------------------------|-----------------------------------|
| [`AGENTS.md`](AGENTS.md)                                                              | 协作者 / AI agent onboarding，红线 + 锁定决策 |
| [`docs/superpowers/PROGRESS.md`](docs/superpowers/PROGRESS.md)                        | 进度复盘                          |
| [`docs/superpowers/specs/2026-05-06-self-growing-info-system-design.md`](docs/superpowers/specs/2026-05-06-self-growing-info-system-design.md) | 系统总览设计                      |
| [`docs/superpowers/specs/2026-05-07-p1-sample-topics-design.md`](docs/superpowers/specs/2026-05-07-p1-sample-topics-design.md) | 三层信息模型 + 域设计             |
| [`docs/howto/zhihu-cookies.md`](docs/howto/zhihu-cookies.md)                          | 接入知乎数据源                    |

## 开发约定

- TDD：先写 failing test 再写实现
- Conventional commits：`feat:` / `chore:` / `docs:` / `fix:`
- 不自动 push：本地 commit 即可
- Lint：`uv run ruff check src/ tests/`
- Test：`uv run pytest`

## 已知短板

- 语义检索未接（MVP 用时间窗 + 关键词筛 facts）
- 知乎 `/topic` 和 `/people/answers` 暂无可靠 OSS 路径（专栏可用，详见 [`docs/howto/zhihu-cookies.md`](docs/howto/zhihu-cookies.md)）
- `radar review accept` 不跑 frontmatter lint
- 一键部署 image 还没做

## License

MIT — see [`LICENSE`](LICENSE).
