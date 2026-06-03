---
status: Draft
date: 2026-06-04
author: liuzhiheng (with Claude)
phase: 重构准备 · 第 4 步（科研检索能力集 · 设计）
scope: 换探针——以「视频生成」科研订阅为探针，构建研究型检索的**能力集**。
probe_domain: video-generation（arxiv cs.CV，6 关键词）
relates:
  - 2026-06-03-retrieval-contract-and-eval.md（复用 contract/qrels/eval harness）
  - 2026-06-02-functional-architecture.md（F2/F5 边界）
supersedes_focus: 把样板域从 motorcycle（开域）换成 video-generation（科研，有界）
---

# 科研检索能力集（以视频生成为探针）

> 思路调整（用户，2026-06-04）：摩托车开域太大，相关性主观、无全集；
> 换成**科研检索**，以**视频生成**为探针，**构建能力集**。

---

## 0. 为什么换探针：科研检索是更好的探针

| 维度 | 开域（motorcycle） | 科研（video-generation） |
|---|---|---|
| 相关性 | 主观、争议大、难标 | **客观**——"是不是视频生成论文"分歧小，用户即权威标注者 |
| 语料 | 无全集（开放世界） | **有界**——arxiv + category，**召回真的可测**（见 §6 池化） |
| 行业标准 | 弱（推荐系统离线指标） | **强**——系统性文献综述 PRISMA/SLR、学术检索工具链（arxiv-sanity / Semantic Scholar SPECTER / Papers with Code / Connected Papers） |
| 能力维度 | 基本就"相关性过滤" | **一组能力**：取词/扩词、跨源覆盖、查重、显著性、子主题归类…探针正好把它们逼出来 |

**核心红利**：有界语料 → **召回可测**。这是开域给不了的，也是上一版（motorcycle）最大的妥协点。见 §6。

---

## 1. 方法学锚点（先锚标准，再落地）

科研检索的"该搜什么/怎么搜/搜多少/质量"在学界是被研究透的，对应两套标准：

**(A) PRISMA / 系统性文献综述（SLR）** —— 给"检索流程"一个标准骨架：

```
Identification（检索）→ Screening（标题/摘要筛）→ Eligibility（按纳排准则细筛）→ Included（纳入）
                      └────────────── PRISMA flow：每步计数 + 排除理由 ──────────────┘
```

- "该搜什么/怎么搜" = PRISMA 的 **review question + information sources + search string**（Item 6-7）
- "搜了多少" = PRISMA **flow diagram 的计数**（identified→screened→included + 排除理由）——这就是把"搜了多少"标准化
- "质量如何" = **screening 精度 + eligibility 准则 + quality assessment**

**(B) 学术检索工具链** —— 给每个能力一个现成参照：

| 能力 | 参照工具/方法 |
|---|---|
| 取词/扩词 | SLR search-string 设计（sensitivity vs specificity）、arxiv-sanity tf-idf、query expansion |
| 语义相关（超越关键词） | **SPECTER / SciBERT** 论文嵌入、Semantic Scholar |
| 显著性 | **Papers with Code**（SOTA/benchmark）、引用速度（Semantic Scholar） |
| 查重/谱系 | **Connected Papers** 引文图、SPECTER 近重 |
| 覆盖 | 多库检索（PRISMA 强制）、跨 arxiv category |

> 遵循项目 [`oss-survey-first` ADR](../superpowers/specs/2026-05-14-oss-survey-first.md)：能复用 SPECTER/PwC/Semantic Scholar 就复用，不自造嵌入/引文图。

---

## 2. 能力集（本文核心交付）

8 个能力，对齐 PRISMA 阶段，每个标注：做什么 / 标准参照 / ISBE 现状 / 可测契约。

| # | 能力 | PRISMA 阶段 | 做什么 | 标准参照 | **ISBE 现状** | 可测性 |
|---|------|-----------|--------|---------|--------------|--------|
| **RC1** | 信息需求分解 | Question | 把"视频生成"拆成可追踪**子主题面**（T2V / I2V / 世界模型 / 视频编辑 / 高效化-蒸馏 / 评测-指标 / 数据集-benchmark / 可控生成） | PRISMA review question；faceted search | ❌ 仅 6 个扁平关键词，无子主题 | 分解=契约；按面计覆盖 |
| **RC2** | 取词与扩词 | Identification | 信息需求→各源查询串，含同义/变体扩展控召回（text2video/T2V/video generative/motion synthesis…） | SLR search-string；query expansion | ⚠️ 6 手写关键词、仅 abstract、无扩展 | 查询召回 vs 池化全集（§6） |
| **RC3** | 跨源覆盖 | Sources | 多库/多 category（cs.CV+cs.LG+cs.GR+cs.MM+eess.IV）、Papers with Code、Semantic Scholar、关键实验室/作者 | PRISMA 多库强制 | ❌ 仅 arxiv cs.CV | 已知相关集的来源归因 |
| **RC4** | 相关性筛 | Screening | 论文是否 in-scope（二值/分级）——**复用 triage 块** | PRISMA 摘要筛 | ⚠️ 关键词 include（`world model`→RL 泄漏） | qrels/eval harness（已建） |
| **RC5** | 显著性/质量 | Eligibility | 从"切题"到"值得读"：新颖性、SOTA/benchmark、有无代码、规模、实验室；滤增量噪音 | PRISMA 纳排准则 + quality assessment；PwC SOTA；引用速度 | ❌ 论文一视同仁 | qrels 的 rel=2 高价值 + 锚点 |
| **RC6** | 查重与谱系 | Dedup | 同文 v1/v2、增量跟进、已见谱系；对照 facts + memory 论点库 | SLR 去重；Connected Papers 引文图；SPECTER 近重 | ⚠️ 仅 arxiv_id 精确去重 | 去重 precision/recall（标注集） |
| **RC7** | 子主题归类 | （组织） | 把入选论文归到 RC1 的面 → 支持分面日报 + 分面覆盖 | faceted classification；SPECTER 聚类 | ❌ 无 | 归类准确率 vs 标注 |
| **RC8** | 覆盖核算 | Flow | 输出 PRISMA 式流计数：识别→去重→筛入→合格→呈现 + 排除理由 | **PRISMA flow diagram** | ⚠️ 仅 collector 原始计数 | 由管线算 + 召回 vs 池 |

**RC1–RC8 与四问的归属**：该搜什么 = RC1；怎么搜 = RC2+RC3；搜了多少 = RC8（+RC2 召回）；质量如何 = RC4+RC5（+RC6 去噪）。

---

## 3. 现状 → 能力集 的差距（视频生成探针）

当前 video-gen 检索整条 = **一行 arxiv 查询**：

```
(cat:cs.CV) AND (abs:text-to-video OR abs:video diffusion OR abs:image-to-video
                 OR abs:video generation OR abs:world model OR abs:video synthesis)
sortBy submittedDate desc, max_results=50, 关键词仅匹配 abstract
```

它一次性把 RC2（取词）压成 6 个常量、RC3（覆盖）压成单 category、RC4（相关）压成 ilike 命中，其余 RC1/RC5/RC6/RC7/RC8 **完全不存在**。三个一眼可见的失效：

1. **召回未知（RC2/RC3）**：用别的写法（"video generative model"/"text2video"/"autoregressive video"）或主类在 cs.LG/cs.GR 的视频生成论文，会被静默漏掉——**而当前系统无法知道漏了多少**。
2. **精度泄漏（RC4）**：`world model` 把强化学习/机器人的"世界模型"也捞进来，它们不是视频生成。
3. **无显著性（RC5）**：一篇 Sora 级发布和一篇两页 workshop note 在日报里同权。

> §5 用真实 arxiv 数据把这三条量化。

---

## 4. 复用已建的 harness（不重造）

第 3 步（motorcycle）建的 `isbe.triage`（contract/scorer/eval/Item/Qrel）**域无关**，直接迁移：

- `Item`：论文 = `{id=arxiv_id, source="arxiv", headline=title, summary=abstract, ...}`，零改动可用。
- `RetrievalContract`：扩 `facets`（RC1 子主题面）字段；`out_of_scope_keywords` 仍可用（如排除 RL world-model 的负词）。
- `triage` / `evaluate`：RC4 直接复用；RC5 显著性是新增打分维度（rel=2 锚点已在 Qrel schema 里）。
- `freeze_collection.py`：新增 **arxiv freezer**（含 §5 验证过的 UA + 退避，应对 WAF），冻结"过滤前"的论文池。
- qrels：论文版每行 `{arxiv_id, rel:0|1|2, facet:<子主题>, must_hit, note}`。

---

## 5. 探针实测（真实 arxiv 数据）

**探针执行状态（2026-06-04）**：从本机直连 `export.arxiv.org` 实测被 WAF 全程挡掉
（6 次 UA+退避重试，全 429 / RemoteProtocolError，`fetched=0`）——即 PROGRESS 反复记录的
"CN 本机 arxiv 网络顽疾"。**故本节量化数字暂缺**，不影响 §3 缺口结论（查询结构分析即确凿证据）。

补数字的可行路径（择一，下一步做）：
- 走**现有 collector**（`_shared/arxiv.py` 自带 tenacity 退避 + `export.arxiv.org`，且生产在服务器侧跑过）冻一份真实池；
- 或在服务器 192.168.0.156 上跑 freezer（PROGRESS：服务器侧 arxiv 可达）。

待回填的三项：① 当前 6-kw 查询本周命中数；② 其中 `world model` 命中样本混入的非视频生成（RL/机器人）比例（RC4 精度泄漏）；③ 跨写法+跨 category 池相对当前窄查询的漏检数（RC2/RC3 召回缺口）。

---

## 6. 核心红利：有界语料 → 召回可测（池化）

开域测不了召回（无全集）；科研域可以，用 **TREC 池化**的标准做法：

```
池 = ⋃( 宽查询多写法 + 跨 category + Papers with Code 列表 + 用户种子已知论文 )
   → 人工标 qrels（rel 0/1/2 + facet + must_hit）
   → 当前窄查询的召回 = 命中相关 / 池中相关   ← 这是相对池的真召回
```

- 池足够宽时，pool-relative recall 是召回的可信下界（TREC 几十年方法）。
- 这把 RC2/RC3/RC8 从"无法评估"变成"有数可断言"——**正是换探针要拿到的东西**。
- 工具：召回/精度/nDCG 用 `pytrec_eval`；池的语义近重用 SPECTER（可选，先关键词池起步）。

---

## 7. 建议建设顺序（每步可测、先写失败测试）

1. **RC1 信息需求分解**（轻、使能）：把 video-gen 写成带 facets 的检索契约（= 扩 `RetrievalContract`）。
2. **RC4 相关性筛 + RC8 覆盖核算**（复用 harness，快赢）：arxiv freezer 冻一周池 → 你标 qrels → 跑 triage + PRISMA 计数。先写失败测试（关键词筛精度不足，如 world-model 泄漏 → 红）。
3. **RC2/RC3 召回**（红利兑现）：构池 → 测当前窄查询召回 → 量化"搜了多少"。
4. **RC5 显著性** → **RC7 归类** → **RC6 查重**（依次，各自标注集）。

> 仍归 v2、本轮不碰：SPECTER 嵌入检索 / 语义近重（Qdrant）——先用关键词池 + LLM-judge 起步，达不到再上向量。

---

## 8. 待你拍板

1. **facets 子主题面**：上面列的 8 个面（T2V/I2V/世界模型/视频编辑/高效化/评测/数据集/可控）是否贴合你对视频生成的实际追踪？要加/删哪个？
2. **建设顺序**：先 RC4+RC8（快赢，复用）还是先 RC2/RC3（直接攻召回红利）？
3. **池的来源**：起步用"宽 arxiv 查询多写法 + 跨 category"够不够，还是要接 Papers with Code？

---

## 附：能力维度术语（防串台）

- **RC1–RC8** = 本文研究型检索能力编号
- 与 [2026-06-02](2026-06-02-functional-architecture.md) 的 F1–F9 功能块正交：RC* 多数落在 FT(Triage)+F2(采集) 内，是其内部能力细分
- 与 spec 的 L/T/C 维度无关
