# P1 Sample Topics — 三层信息模型与两个具体域设计

**日期**：2026-05-07
**状态**：spec（已通过 brainstorming 锁定，待 P1 实施计划阶段细化）
**前置 spec**：`docs/superpowers/specs/2026-05-06-self-growing-info-system-design.md`（系统总览）
**驱动场景**：P1 阶段需要两个具体测试域来检验通用 Topic 抽象——本 spec 同时回答"通用骨架长什么样"和"两个域分别落地长什么样"。

---

## 1. 三层信息模型（系统级核心约定）

ISBE 内部所有"被处理的内容"分属三层，**层间不可混用**。这是后续 collector / digester / review 流的唯一根基。

| 层 | 定义 | 例子 | 可重生成 | 用户特定 | 落地位置 |
|---|---|---|---|---|---|
| **外部事实** (facts) | 外部世界可验证、可重抓、不依赖你的可观测数据 | 论文元数据、价格、新闻原文、SEC filing | ✅ 重抓即可 | ❌ | Postgres（结构化）+ MinIO（原文）+ Qdrant（向量） |
| **记忆** (memory) | 你的状态、偏好、演化中的论点；构成 LLM 上下文 | 已读 / 持仓偏好 / bull-bear thesis | ❌ 路径依赖 | ✅ | `memory/<uid>/`（markdown + frontmatter） |
| **产出/归档** (artifacts) | 一次 digest run 的全部输出；事实快照 + 当期分析 + 引用 memory 的指纹 | 周报、日报 markdown + JSON sidecar | ⚠️ 重跑代价高且不一致（LLM 非确定 + memory 已变） | 半是 | MinIO（正文）+ Postgres（索引）+ Langfuse（trace） |

### 1.1 红线
1. **价值判断不进 fact 表**。"重要 / 可复现 / 弃" 一律去 memory。
2. **artifact 不进 memory**。昨日 digest 不喂下次 prompt；只通过 review 流程把"几行结论"蒸出进 .pending memory。
3. **fact 表骨架由人决定，扩展可由 agent 提议**。spec §3 L3 自扩展用此场景。
4. **memory 允许并列与矛盾**（特别是 thesis）。不做自动合并；review 流决定淘汰。
5. **artifact 必须带输入指纹**：引用的 fact ID 列表 + 每条 memory 的 `name@revision` + Langfuse trace_id。未来回看一份 digest 必须能精确重建上下文。

### 1.2 数据流向

```
External world ──> Collector (Prefect flow) ──> facts (PG/MinIO/Qdrant)
                                                  │
User edits memory ──────────────────────┐         │
Agent proposes drafts ──> .pending memory │         │
                                          ▼         ▼
                              Review CLI ──> memory ─┐
                                                     │
                                                     ▼
                                                 Digester (Prefect flow)
                                                     │
                                                     ▼
                                          artifact (digest)
                                                     │
                                                     ├─ 归档 (MinIO + PG index)
                                                     └─ 蒸馏建议 ──> .pending memory（→ review）
```

---

## 2. 通用 Topic 抽象

跨两个域共享的接口（**这是 P1 实施的复用面**）。

### 2.1 Topic 接口

每个 topic 由一个目录承载（`topics/<id>/`），含：

| 文件 | 必需 | 内容 |
|---|---|---|
| `topic.yaml` | ✅ | 元数据：id、label、cadence、active 状态 |
| `collectors/*.py` | ✅ | 一个或多个 Prefect flow，输入 = 增量游标，输出 = 写入 facts 表 |
| `digester.py` | ✅ | Prefect flow，输入 = facts + memory，输出 = artifact + .pending memory 草稿 |
| `templates/*.j2` | ⚠️ | digest 渲染模板（artifact 正文骨架） |
| `fact_schema.sql` | ⚠️ | 该 topic 引入的 fact 表 DDL（若复用通用表则可省） |

### 2.2 Digester 三段结构（强约定）

任何 topic 的 digest artifact 都必须包含三段：

1. **事实段**：当周期内 facts 的客观摘要（数字、事件、列表）
2. **分析段**：基于 facts × memory 的当期判断；必须显式引用所用 memory 条目（`name@revision`）
3. **蒸馏段**：从本期产出中提炼出来的"应进 memory 的"候选——写入 `.pending/<uid>/<type>/...`，不直接落地，等 review

### 2.3 Review 路径

`radar review memory` CLI（P0 已占位，P2 实化）：列出 `.pending/`，对每条选择 accept / reject / edit。accept → 移入正式 memory 目录并更新 `MEMORY.md` 索引；reject → 移入 `.audit/rejected/`（不丢弃，备查）。

### 2.4 Memory 文件 lifecycle（科研域 C 方案推广为通用规则）

| 类型 | 主目录 | 归档目录 | 触发归档的规则 |
|---|---|---|---|
| `reading` | `reading/<YYYY>/<W##>/` | `reading/.archive/<YYYY>/W##/` | 周龄 > 8 周自动归档；归档后**不进 prompt 上下文**，仅按需查询 |
| `topic.theses` | `topics/<id>.theses.md`（多 revision） | 旧 revision 留同文件，frontmatter `revision` 递增；`supersedes` 标识被替代条目 | 由 review 流决定 |
| `feedback`、`user`、`reference` | 各自顶层目录 | 不归档（少量稳定） | — |

> **prompt 注入策略**（spec §4.2 扩展）：默认注入 `user/* + feedback/* + topics/<active>/* + reading/{当周, 上周}/*`；`.archive/` 只在 RAG 显式查询时使用。

---

## 3. 域 1：临近降水预报（科研订阅）

### 3.1 问题域定位

跟踪 **nowcasting**（0–6 小时短临降水预报）这一子领域的研究增量：模型架构、benchmark 演化、实现 repo、新数据集。**不**包含中长期数值天气预报、不包含工程化部署论文（除非显式扩展兴趣）。

### 3.2 Facts 骨架

| 表/集合 | 关键字段 |
|---|---|
| `papers` | arxiv_id (PK), title, authors[], abstract, primary_category, submitted_at, updated_at, pdf_uri, source_url |
| `paper_chunks` | paper_id, chunk_idx, text, embedding(Qdrant) |
| `repos` | github_url (PK), title, description, stars, last_commit_at, last_release_at, linked_paper_ids[] |
| `events` | id, type ∈ {preprint, repo_update, conf_accept, blog_post}, payload_json, observed_at, source |

### 3.3 Memory 形态

| 文件 | 类型 | 内容 |
|---|---|---|
| `topics/nowcasting.md` | `topic` | 关键词、子域权重（例 "radar-based" 0.8）、推荐/排除作者机构 |
| `topics/nowcasting.theses.md` | `topic`（多 revision） | 当前研究观点列表，并列允许 |
| `reading/<YYYY>/W##/<arxiv_id>.md` | `reading` | 一篇一文件 + 你的一句话评价；按周分目录归档 |
| `feedback/research_digest_style.md` | `feedback` | 输出偏好（每篇多长、要否 abstract、要否 SOTA 对比表） |
| `user/research_focus.md` | `user` | 硬上下文（"我在写关于 X 的论文"） |

### 3.4 Digest 节奏

- **每日 06:00**：增量 collector，写入 facts，**不**生成 digest
- **每周一 08:00**：周报 digester（覆盖近 7 天 facts × 当前 memory）

### 3.5 Digest 三段示例

```
# 临近降水预报周报 — 2026-W19
（facts: 87 papers / 4 repo updates；
 memory: nowcasting@rev3, research_focus@rev2, digest_style@rev1, theses@rev2）

[事实段]
本周 cs.LG / physics.ao-ph 新增 87 篇相关；NowcastNet 主仓 release v0.4。
SOTA 增量表：SEVIR CSI@30min 0.48 → 0.51。

[分析段]
重点新增（按 兴趣匹配 × 增量价值 排序）：
1. PaperX — diffusion + radar conditioning，CSI@30 0.51（前 SOTA 0.48）
   匹配理由：你的 research_focus@rev2 命中 "lead-time extension"
   复现风险：仅 1 GPU 复现报告
2. ...

[蒸馏段 → .pending]
- 新论点："diffusion 在 lead-time > 90min 仍未解 mode collapse"
- 自动标记已读：3 篇 → reading/2026/W19/<arxiv_id>.md 草稿
```

---

## 4. 域 2：NVDA 金融日报

### 4.1 问题域定位

跟踪与 **NVDA 投资决策相关的每日信号**：股价归因、产业链事件、社交情绪、关键事件 horizon。**只观察、不持仓**——系统不知道用户的真实头寸（见 §4.3 隐私边界）。

### 4.2 Facts 骨架

| 表/集合 | 关键字段 |
|---|---|
| `prices_daily` | symbol, date, ohlcv, adj_close（hypertable，预留 TimescaleDB） |
| `prices_intraday` | symbol, ts, ohlcv（1m/5m），仅当日归因窗口保留 N 天 |
| `news` | id, source, published_at, headline, body_uri, tickers[], lang |
| `news_chunks` | news_id, embedding(Qdrant) |
| `sec_filings` | accession_no, form_type ∈ {10-Q, 10-K, 8-K, 4}, filed_at, ticker, body_uri |
| `events_cal` | id, ticker, type ∈ {earnings, GTC, product_launch, fed, peer_earnings, capex_guide}, scheduled_at, status |
| `social_posts` | id, platform ∈ {x, reddit}, author, posted_at, text, engagement, embedding |
| `peer_prices_daily` | 同业 (TSM, AMD, AVGO, MU, INTC) + 大客户 (MSFT, GOOG, META, AMZN, AAPL) + 大盘 benchmark (NDX, SOX) |

### 4.3 Memory 形态（隐私边界 = A 档：仅 watchlist）

| 文件 | 类型 | 内容 |
|---|---|---|
| `topics/nvda.md` | `topic` | 范围（NVDA 单只 vs AI 半导体板块）、关注时段、watchlist |
| `topics/nvda.theses.md` | `topic`（多 revision） | bull/bear 论点列表，并列/矛盾允许，每条带证据指针 + 重要性评分 |
| `feedback/finance_digest_style.md` | `feedback` | 是否给买卖建议（默认 NO）、归因深度、术语翻译开关 |
| `reading/<YYYY>/W##/news_<id>.md` | `reading` | 已读关键新闻 + 一句话标记，沿用 §2.4 周分档 |
| `reference/event_calendar.md` | `reference` | 财报日、GTC、Fed 会议等 horizon |
| `reference/trusted_sources.md` | `reference` | 信任 / 不信任的分析师 / 媒体（影响新闻 + 社交加权） |

> **隐私边界（约定，不实施 PII 校验）**：
> - 系统**不**记录持仓股数、平均成本、现金额度、盈亏。
> - 仅记录 watchlist + 抽象风险偏好（如"风险=中"，不带数字）。
> - digest 不算盈亏、不做止损建议、不喊单。
> - 这是 spec §1.5 红线 1（敏感数据不入 memory）的具体投影。

### 4.4 Digest 节奏

- **每交易日 美股盘后 16:30 ET**（北京时间次日 ~05:00）：日报 digester
- **每日 08:00 北京时间**：本地汇总（含隔夜事件 + 亚太市场反应）
- **盘前 09:15 ET**（可选）：盘前快讯，只列事件不做归因

### 4.5 Digest 三段示例

```
# NVDA 日报 — 2026-05-06 (Tue, 盘后)
（facts: 1d OHLCV + 23 news + 2 SEC + 47 social;
 memory: nvda@rev2, theses@rev5, digest_style@rev1, trusted_sources@rev3）

[事实段]
Close $1234.56 (-2.3%) | Vol 1.5σ | TSM -4% / AMD -1.5% / AVGO -3% / NDX -0.8%
SEC: 0 事件
关键新闻 3：TSM Q2 capex 指引 ↓9% / r/NVDA H100 二手价讨论 / Reuters 数据中心订单更新

[分析段]
归因（残差 + 同业 beta 拆解）：
  · 大盘 beta： -0.6%
  · 同业事件： -1.2%（TSM 指引主导）
  · 公司特定： -0.5%
Thesis 更新（基于 theses@rev5）：
| # | 论点 | 今日动作 | 重要性 |
| #1 (bull) | hyperscaler capex 持续高位 | 待 META/MSFT 财报，无新数据 | mid |
| #3 (bear) | 供应链产能缓解→单价下行 | TSM 数据强化 | mid → high |
Horizon：5/22 NVDA earnings（11 工作日，过去 8 次隔夜均 |Δ| > 7%）

[蒸馏段 → .pending]
- thesis #3 重要性 mid → high
- 新论点候选："二手 H100 价格作为供需先行指标"（证据 1，待累积 ≥ 2 周再确认）
```

---

## 5. 跨域共享 vs 差异（P1 抽象点）

| 维度 | 科研订阅 | NVDA 日报 | 共享? |
|---|---|---|---|
| Collector 实现 | arXiv listing + GitHub poll | yfinance + news scrape + SEC EDGAR + social poll | ❌ 各写各的 |
| 节奏 | 周（+ 每日轻量增量） | 每交易日（+ 盘前快讯） | ❌ |
| Fact 模式 | 文档为主 | 时序 + 文档混合 | ⚠️ 部分（news/social 文档段共享） |
| Memory 类型组合 | topic / theses / reading / feedback / user | topic / theses / reading / feedback / reference | ✅ 都有 `theses` 多 revision |
| Digest 三段结构 | 事实 / 分析 / 蒸馏 | 事实 / 分析 / 蒸馏 | ✅ 完全同构 |
| 蒸馏路径 | reading 自动入档 + thesis 候选 | thesis 重要性变更 + 新论点候选 | ✅ 完全同构 |
| Review 流 | 同 | 同 | ✅ |

**P1 抽象目标**：
- ✅ 抽象 `Topic` / `Digester` 接口 + 三段约定 + review 流
- ✅ 抽象 memory lifecycle（周分档、归档不入 prompt）
- ❌ **不**抽象 collector——两个域差异过大，强制抽象只会变形

---

## 6. 与上层 spec 的 alignment

| 上层 spec 节 | 本 spec 落地情况 |
|---|---|
| §1.3 模块表 | facts 用 PG+MinIO+Qdrant；memory 用文件式；artifact 走 MinIO+PG index+Langfuse；与模块表一致 |
| §1.5 红线 1（敏感数据不入 memory） | NVDA 域 §4.3 隐私边界明确投影 |
| §1.5 红线 5（调度走 Prefect） | 两个域的 digester 都是 Prefect flow |
| §3 L3 自扩展 | fact 表骨架手动；扩展项（如新增"专利监控"列）由 agent 提议→ review→落地 |
| §4.2 加载与注入策略 | §2.4 给出周分档 + 归档不入 prompt 的具体规则 |
| §4.3 文件即原子 | reading 一篇一文件、theses 多 revision 都符合 |
| §4.4 review CLI | §2.3 三段产出 + .pending → review 流路径明确 |
| §5.2 可观测栈 | artifact 必须带 trace_id（§1.1 红线 5） |

---

## 7. 不在本 spec 范围内（明示推迟）

- **PII 校验自动化**：§4.3 隐私边界目前是约定，不做静态分析检测。后续若 memory 量级 > 100 条再考虑。
- **跨 topic 关联**（如"科研论点引用 NVDA 案例"）：P1 不做。
- **collector 故障重试 / 限速 / 反爬**：放 P1 实施计划，本 spec 不规定具体策略。
- **artifact 检索界面**（"给我看上周三的 NVDA 日报"）：P5 看板任务。
- **多用户**：P0' 单用户假设保留；多用户结构（uid 维度）已在路径中预留，但不实化。

---

## 8. 后续

本 spec 通过后，下一步是 **writing-plans skill**：把上述设计拆成 P1 的有序 task 清单，含：
- Topic 接口的 Python 抽象（dataclass / Protocol）
- 周分档 memory lifecycle 的 reindex 工具
- 两个域的 collector + digester 各自骨架（不要求 production-grade，验证三段结构跑通即可）
- Review CLI 实化（替换 P0 的 placeholder）

---

*spec 撰写时间：2026-05-07；brainstorming session 输出。*
