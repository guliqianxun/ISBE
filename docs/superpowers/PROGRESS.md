# ISBE 执行进度

> 单一来源：哪些任务做完了、当前 phase 是哪个、卡在哪、下一步是什么。
> 每完成一个 task 更新这里；每开始一个新会话先读这里。

**最后更新**：2026-05-11（P2 NVDA MVP 完成；4 个 active topic；多域 + 自成长系统骨架闭环）

---

## 总览

| Phase | 周期 | 状态 |
|---|---|---|
| Brainstorming + Spec | 完成 | ✅ |
| Writing implementation plan | 完成 | ✅ |
| P0' 评估 + 骨架（14/19；hermes 评估暂搁，路径 B' 直接走单域 MVP） | 1.5 周 | ✅（部分） |
| P1 nowcasting MVP | 2 周 | ✅ tag `p1-nowcasting-mvp` |
| P1.5 make-it-live（cron / topic_runs / Langfuse / curated repos） | 0.5 周 | ✅ |
| P1.6 worker-as-service（Dockerfile / compose worker / GH token / Langfuse verify） | 0.5 周 | ✅ tag `p1.6-worker-service` |
| **P1.7 部署 + Phoenix 改造 + arxiv collector 参数化 + 2 个新论文域** | 1 周 | ✅ |
| **P2 NVDA daily-digest MVP（第二个域，验证抽象）** | 1 天 | ✅ |
| P3 collector 抽象（RSS + HTTP-JSON + crawl4ai 三类适配器）| 1-2 周 | ⏸ 候选下一步 |
| P4' L3 自扩展（沙箱里 agent 自己写 collector）| — | ⏸ 远期 |

---

## P0' Task 进度（19 任务）

执行计划：`docs/superpowers/plans/2026-05-06-p0-evaluation-and-skeleton.md`

| # | 任务 | 状态 | Commit |
|---|---|---|---|
| 1 | Initialize git repo + base layout | ✅ | `c44d596` |
| 2 | Set up Python project with uv | ✅ | `af5c03d` |
| 3 | Memory frontmatter Pydantic models (TDD) | ✅ | `4372c65` |
| 4 | Memory file loader (read-only, TDD) | ✅ | `02adb50` |
| 5 | Memory frontmatter linter (TDD) | ✅ | `f71083d` |
| 6 | Config loader (env + topics.yaml, TDD) | ✅ | `9533dd8` |
| 7 | CLI `radar` + review memory placeholder | ✅ | `0024605` |
| 8 | Memory directory scaffolding | ✅ | `a3637e2` (+ `d9eb548` 修正 .claude ignore) |
| 9 | Prefect 3 hello-world flow (TDD) | ✅ | `9e75e49` |
| 10 | docker-compose Postgres + Qdrant + MinIO | ✅ | `dc6e2cf` |
| 11 | docker-compose Langfuse | ✅ | `213e269` (v2，非 v3 — 见注记 5) |
| 12 | docker-compose Prefect 3 server | ✅ | `6d08634` |
| 13 | docker-compose Uptime Kuma | ✅ | `5ef6d0f` |
| 14 | Compose stack smoke test + tag | ✅ | tag `p0-infra-skeleton` |
| 15 | **★ hermes-agent 评估**（§2.1 清单 8 项） | ⏸ | — |
| 16 | **★ 决策门**（路径 B vs A） | ⏸ | — |
| 17 | (路径 B) Add hermes to docker-compose | ⏸ 待 #16 | — |
| 18 | (路径 B) Verify hermes RPC ↔ Prefect | ⏸ 待 #16 | — |
| 19 | P0' completion — wrap up + tag | ⏸ | — |

**下一步起点**：Task 15 — hermes-agent 评估（§2.1 清单 8 项）。这是研究性任务，需用户参与判断 PASS/FAIL；评估结果决定路径 B（Task 17-18 集成）vs 路径 A（Task 19 回退）。

**Task 2-9 摘要**：19 个 pytest 全绿；CLI `radar review memory` 占位可用；Prefect hello-world flow in-memory 跑通；memory loader/linter 覆盖 .pending/.audit 排除规则与 4KB body 限制。

**Task 10-14 摘要**：docker-compose 7 服务 (Postgres/Qdrant/MinIO/Langfuse-db/Langfuse/Prefect-server/Uptime-Kuma) 全部 healthy；hello-world flow 通过 PREFECT_API_URL 连到 server 跑通并在 UI 可见；6 endpoint smoke 全绿；19 tests 仍全绿；tag `p0-infra-skeleton` 是评估失败时的回退基线。

---

## Task 1 review 摘要

完成于 brainstorming 会话末。

- **Implementer (haiku)**：DONE — 创建 `.gitignore` (27 行) / `README.md` (24 行) / `.env.example` (18 行)，commit `c44d596`，3 文件 69 行
- **Spec compliance**：✅ byte-level 匹配
- **Code quality**：✅ ready to merge；唯一 minor 注记是 `.gitignore` 引用了尚未存在的 `memory/` 子目录路径（Task 8 才创建），但 gitignore 对不存在路径无害

---

## 已知问题与注记

### 1. 仓库 preexisting git 状态

执行 Task 1 时发现：`F:\codes\ISBE\` **早已是 git 仓库且配了 origin remote**——非本次会话所为。implementer 正确处理（没 re-init、没 push、没碰 LICENSE）。

**待你决定**：
- `git remote -v` 看 remote 是哪里
- 如果是你自己的私仓 → 沿用，最终需要时 `git push`
- 如果是别处遗留 → 决定是否 `git remote remove origin`

### 2. Windows + Bash 路径

- Bash 工具：`/f/codes/ISBE`
- Edit/Read/Write：`F:\codes\ISBE`
- 两种都接受，看具体调用更顺手

### 3. P0 评估 (Task 15) 的真实未知数

Task 15 是研究性任务——我们写计划时**无法预知** hermes-agent 的实际 API surface（RPC URL 形式、env 变量名、镜像 tag、命令名等）。

Task 17-18 中所有 hermes 接口代码标的"以评估实际为准"，意味着 Task 15 的评估报告 (`docs/superpowers/eval/p0-hermes-evaluation-report.md`) 一旦填好，Task 17-18 的具体实现要按它调整。

**关键 ★ 项**：5 (Python RPC 稳定性) / 6 (Memory SQLite 可禁用) / 7 (Sandbox docker backend in compose)。任一 NO 就触发回退到附录 A 路径。

### 5. Langfuse v3 不能用计划里的 postgres-only 配置

计划写的镜像 tag 是 `langfuse/langfuse:3`，但 v3 在启动时强制要求 `CLICKHOUSE_URL` + Redis + S3，会进 restart loop。**已降到 v2**（commit `213e269`），它对应计划里 postgres-only 的资源画像。若以后要升 v3，需在 compose 加 ClickHouse / Redis / 一个 S3-compatible（可复用 MinIO bucket）。

### 4. 持久记忆（跨会话）

用户级长期记忆在 `C:\Users\Administrator\.claude\projects\F--codes\memory\`（Claude Code 自动加载，不在本仓库）：

- `feedback_file_based_memory.md` — 偏好文件式透明系统
- `feedback_workflow_vs_agent_separation.md` — 固定调度禁用 LLM 决策循环
- `reference_hermes_agent_self_evolution.md` — hermes-agent 与本系统高度对齐
- `user_high_doc_density.md` — 用户的记忆/文档量预期快速过千

未来会话开始时，这些记忆会自动注入 Claude Code 上下文。如果换 AI 工具（Cursor/Cline/Codex），需要把这些"翻译"成对应工具的等价机制。

---

## 执行模式

之前选择：**Subagent-Driven Development**（superpowers:subagent-driven-development），每个 task 派 fresh subagent，task 间用户 review。

如果后续你想直接动手而不走 subagent：完全没问题——计划本身是自包含的，每个 task 的 step 都是可独立执行的命令 + 代码块。

---

## 下次开会话时的开场白模板

```
我在 F:\codes\ISBE 这个项目里继续 P0' phase 的实施。
- 当前进度看 docs/superpowers/PROGRESS.md
- 设计依据看 docs/superpowers/specs/...design.md
- 实施计划看 docs/superpowers/plans/...skeleton.md
- 不可违反的约束看 AGENTS.md

下一步：[Task N — 任务名]
```

---

## 变更记录

- **2026-05-06**：初始化进度文档；Task 1 完成；写入 brainstorming + spec + plan 三件套
- **2026-05-07**：Task 2-9 完成（Python 骨架 + memory + CLI + Prefect hello-world）；19 tests passing；下一步进 docker-compose 段
- **2026-05-07**（晚）：Task 10-14 完成（docker-compose 全栈 7 服务 healthy；Langfuse 降到 v2；hello-world flow 通过 server 跑通）；tag `p0-infra-skeleton`
- **2026-05-07**（深夜）：P1 spec + plan 写就；切到 P1 nowcasting MVP 实施
- **2026-05-07**（更深夜）：P1 Tasks 1-17 完成（19 tasks 中 17 个，subagent-driven）；50 tests 全绿
- **2026-05-07**（凌晨）：T18 E2E smoke 跑通——arxiv 50 papers + github 2 repos 入 PG；DeepSeek（替换 anthropic 跑测试）digester 跑通生成真实 weekly digest artifact 入 MinIO（2KB md，三段结构正确，引用全部 4 条 memory@rev）；tag `p1-nowcasting-mvp`
- **2026-05-07**（凌晨2）：补 5 篇真 PDF 下载（一个 19MB Convective Radar Nowcasting 大论文）；MinIO + papers/ 本地副本；rate-limit 1/3s
- **2026-05-07**（凌晨3）：P1.5 完成——TopicRun observability 接进 4 个 flow（运行档案进 PG）；Langfuse `@observe` 装饰 `complete()`（keys 空时 no-op，填了即上报）；TRACKED_REPOS 精挑 6 个真实 nowcasting 仓库；Prefect `serve()` 注册 4 个 deployment 含 cron（06:00 collect / 周一 08:00 digest）；`radar scheduler serve` CLI 起长进程；52 tests 全绿；tag `p1.5-make-it-live`

## P1 已知 polish 项（P2 处理）
- **prompt 严格度**：system prompt 中 `DRAFT[<target_path>]:` 应给出明确路径 example（`topics/nowcasting.theses.md`），LLM 易把 `<target_path>` 误填为 `name@rev`
- **parser 校验**：`parse_distillation_section` 应校验 target_path 以 `topics|reading|feedback|user|reference` 之一开头，且以 `.md` 结尾，否则 skip + warn
- **review accept 时 lint**：`accept_pending` 落盘前应跑 `lint_file` 校验 frontmatter，避免无效 memory 进入索引

## P0 未完成项（推迟到独立 spec）
- Task 15-19（hermes-agent 评估 + 决策门 + 集成 / 回退）—— 当前走"路径 A 自建"线路，hermes 评估单独 spec 处理

## P1.5 完成清单
- ✅ TopicRun context manager + 4 flow 接入（运行档案进 PG `topic_runs`）
- ✅ Langfuse `@observe` 装饰 `complete()`（设了 keys 自动 trace；空 keys 静默 no-op）
- ✅ TRACKED_REPOS 精挑 6 个仓库（DGMR / MetNet / Modulus / GraphCast / WeatherBench2 / Aurora）
- ✅ Prefect 4 deployment + cron schedule（注册到 server，重启 Prefect 也保留）
- ✅ `radar scheduler serve` CLI（长进程模式做 worker）

## 用户操作清单（要让系统真自动跑）

### 路线 A — Host process（最简，已验证）
1. `docker compose up -d`（基础设施 7 服务）
2. `uv run alembic upgrade head`
3. `uv run radar scheduler serve`（长进程；关终端就停。生产用 nssm / systemd 包成服务）

### 路线 B — Compose service（需 Docker Desktop 配置）
1. **Docker Desktop → Settings → Resources → File sharing**：把 `F:\` 加进共享列表 → Apply & Restart
2. `docker compose --profile worker up -d --build`
3. `docker logs -f isbe-radar-worker` 看 4 个 deployment 注册成功

### Langfuse trace 接通（可选）
1. 打开 http://localhost:3000 → 注册（首个用户是 admin）
2. 创建 organization → 创建 project（如 "isbe-dev"）
3. Settings → API Keys → Create new keys → 复制 public + secret
4. 填进 `.env`：
   ```
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   ```
5. 跑 `bash scripts/verify_langfuse.sh` 自动验证一次 LLM 调用的 trace 是否上报

### GITHUB_TOKEN（可选，建议设）
- `https://github.com/settings/tokens` 生成 fine-grained 或 classic token（无需 scope，public repo 用就行）
- 填进 `.env` 的 `GITHUB_TOKEN=`，限速从 60/hr 升到 5000/hr

### 第一次手动触发（不等 cron）
```bash
uv run radar topics run nowcasting --collect
uv run radar topics run nowcasting --download-pdfs --pdf-limit 5
uv run radar topics run nowcasting --digest
```

## 已知短板（P2 候选）
- ~~GitHub API 未授权限速~~ → P1.6 T1 修了（GITHUB_TOKEN env）
- ~~Prefect `serve()` 是 foreground~~ → P1.6 T3 修了（compose worker，需 F: 共享）
- ~~LLM trace 无引导~~ → P1.6 T4 提供 `scripts/verify_langfuse.sh` + 文档化步骤
- ~~旧 TRACKED_REPOS 死链~~ → P1.7 修了（refresh + `follow_redirects=True`）
- ~~Langfuse 需要建账号~~ → P1.7 换成 Phoenix（零登录 OTel）
- 旧 nowcasting 历史 `papers` 表里有 `google-research/google-research` 等遗留行，无清理逻辑
- Docker Desktop on Windows F: 盘共享是手动一次性配置，无自动检查脚本
- arxiv PDF 下载 CN→arxiv.org 100% 超时，已默认 `export.arxiv.org` 优先；想更快需走代理（未做）

---

## P1.7 摘要（2026-05-10）

**主题**：服务器化部署 + LLM trace 改造 + 论文 collector 参数化 + 2 个新 topic

**关键决策**
- **Langfuse → Phoenix**：自托管 Langfuse 仍要建本地账号；换成 Arize Phoenix（OTel + OpenInference 属性），单容器零登录
- **服务器部署到 192.168.0.156**：iSCSI LUN (`/mnt/nas_iscsi/`) 当 Docker data-root；新增 server overlay (`docker-compose.server.yml`)
- **不抽象 collector 框架**（先延后到 P3）；但把 arxiv 收集器**参数化**（`topic.yaml.arxiv: {categories, include_keywords}`），降低"加论文 topic"成本
- **加 2 个新 topic（只 yaml，无 Python）**：`video-generation`（cs.CV / text-to-video / video-diffusion / world-model）、`image-restoration`（cs.CV / denoising / dehazing / SR）

**新增基础设施服务**
| 服务 | 端口 | 用途 |
|---|---|---|
| Phoenix | 6006 | LLM trace，零登录 |
| Homepage（hub） | 8082 | 中间站，一键到所有 UI |
| pgweb | 8083 | 浏览器 SQL UI（避开 8081 已占用） |
| filebrowser | 8084 | papers/artifacts 浏览（PDF 内嵌预览） |

**踩过的坑（值得记的）**
1. PyPI cryptography wheel CN 服务器超时 → `PIP_INDEX_URL` build-arg（清华镜像）
2. Prefect 启动需要 `prefect` 数据库，不会自动建（手动 `CREATE DATABASE prefect;`）
3. Homepage v1.x 严格 Host header → `HOMEPAGE_ALLOWED_HOSTS=*`
4. Homepage config 目录不能 `:ro` 挂载（需写 logs/）
5. 浏览器不能 dispatch `smb://` → 用 filebrowser HTTP 链接代替
6. arxiv PDF 下载：`arxiv.org` 在 CN 服务器 100% 超时，每篇白等 9 分钟才 fallback；改默认 mirror 顺序 `export.arxiv.org` 优先

**Commit 链**（13 个）
`0da7ecf` Phoenix · `afc558d` server overlay · `4f10082` PyPI mirror · `60ef109` TRACKED_REPOS 修死链 · `0699a60` `7a38463` pgweb · `97afa58` `20d4bba` `cccd076` homepage · `cff34b6` filebrowser · `d3cc970` ⭐ arxiv 参数化 + 2 新 topic · `c9869a1` PDF 下载重试 · `b4ca568` 进度日志 · `734e8ea` mirror 默认 export 优先

**验证产出**
- 3 个 topic（nowcasting / video-generation / image-restoration）端到端跑通；周日报 markdown 落盘 `/mnt/nas_iscsi/isbe/artifacts/`
- Phoenix UI 看到 `isbe` project 下的 LLM span（OpenInference 属性，prompt+output+token）
- 8 个 Prefect deployment 自动注册（worker 容器启动即加载）

---

## P2 NVDA daily-digest MVP（2026-05-11）

**主题**：第二个域，验证 Topic 抽象的复用性（spec §5 的核心实验）。

**Plan**：`docs/superpowers/plans/2026-05-10-p2-nvda-mvp.md`（12 个 task）

**Scope（核心收敛）**
| 维度 | spec 设计 | MVP 实际 |
|---|---|---|
| Facts 表 | 8 张（prices_daily / prices_intraday / news / news_chunks / sec_filings / events_cal / social_posts / peer_prices_daily） | **3 张**：`prices_daily`（含 watchlist 多 symbol）/ `news_items` / `sec_filings` |
| 数据源 | yfinance / news scrape / SEC EDGAR / social poll | yfinance / **Yahoo+Nasdaq RSS**（不爬）/ SEC EDGAR JSON / **不做 social** |
| 节奏 | daily_after_close + 盘前快讯 | 仅 daily_after_close（22:30 UTC 工作日） |
| Memory | topic / theses / reading / feedback / reference | **4 个 seed**：`topics/nvda.md` + `topics/nvda.theses.md` + `feedback/finance_digest_style.md` + `reference/nvda_event_calendar.md` |

**Task 完成情况**（12/12）
| # | 任务 | Commit |
|---|---|---|
| T1 | yfinance dep | `df9a449` |
| T2 | NVDA facts schema + alembic 003 | `e28277a` |
| T3 | 提取 `_shared/digester_utils.py`（split_sections / parse_drafts / memory loader） | `b41544c` |
| T4 | yfinance prices collector | `958e368` |
| T5 | RSS news collector | `4df4f3d` |
| T6 | SEC EDGAR filings collector | `5f51d74` |
| T7 | topic.yaml + 4 seed memory（含隐私红线 3 条） | `51dcee8` |
| T8 | finance_prompts.py + daily digester（finance flavor） | `b88187f` |
| T9 | scheduler 改用 dispatch table（`schedule_key → flow`） | `11ad013` |
| T10 | CLI 支持 `radar topics run nvda --collect / --digest` | `19f9d2a` |
| T11 | full test 59 green + ruff clean | `5d689df` |
| T12 | 服务器 smoke + 红线人工 review | 用户验收完毕 |

**关键设计选择**
- **不共享 digester**：研究域三段（论文列表 / 分析 / 蒸馏）和金融域三段（价格归因 / thesis 状态 / horizon）虽然结构同构，**facts 形态完全不同**。pure helpers 共享（`_shared/digester_utils.py`），LLM prompt 和 facts 查询各自一套 —— 验证了 spec §5 表格"Collector 实现 ❌ 各写各的"的判断
- **隐私红线两层防御**：seed memory `feedback/finance_digest_style.md` + 系统 prompt `FINANCE_SYSTEM_PROMPT` 双写"禁止给买卖建议 / 禁止算盈亏 / 禁止喊单"
- **scheduler 抽象升级**：从硬编码 4 条 nowcasting deployment → 8 个 `schedule_key → flow` 映射，遍历所有 active topic 的 `schedules:` 块生成。当前 12 个 deployments（4 个 nowcasting + 各 2 个 video-gen/image-rest + 4 个 nvda）

**抽象 vs 实现 — 验证结果**
| spec §5 预测 | P2 实际 | 结论 |
|---|---|---|
| Topic / Digester 接口可共享 | ✅ DigestResult/DigestSection/PendingMemoryDraft 完全复用 | spec 对 |
| 三段结构同构 | ✅ split_sections 单一实现可解析两种 flavor | spec 对 |
| Collector 不共享 | ✅ 4 个 NVDA collector 全部 bespoke，0% 复用 nowcasting | spec 对 |
| Memory lifecycle 同构 | ✅ `.pending` review 流原样跑通 | spec 对 |

**实测得到的新教训**
- 国内服务器 arxiv.org 完全不可达 → 重要的网络依赖必须提供 mirror 配置（P1.7 已落地）
- Windows 终端 cp1252 打印中文炸 → 测试与生产用 docker exec / linux 容器，开发用 `uv run python -c "..."`
- `prefect_test_harness` 是单元测试中处理 `@flow` 装饰函数的正解（避免连真 server）
- Yahoo Finance RSS（`feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA`）格式稳定，feedparser 直接用

**P3 候选（不在本会话做）**
- collector 抽象框架：RSSCollector + HTTPApiJsonCollector + 轻量 crawl4ai 适配器（用户偏好）
- digester 抽象框架：把 NVDA 和 arxiv-weekly 两个 flavor 进一步抽象出 facts_fetcher protocol（可选）
- 当 nvda 域跑 1-2 周后，看实际产出 vs 设计期望，再决定是否要扩 facts schema（intraday / social）

---

## Tag / Commit 索引

| Tag / Commit | 含义 |
|---|---|
| `p0-infra-skeleton` | P0' 基础设施跑通（7 容器） |
| `p1-nowcasting-mvp` | P1 单 topic 端到端 |
| `p1.6-worker-service` | Docker worker 可选服务 |
| `5d689df` | P2 NVDA MVP 完成（无 tag，可补） |
