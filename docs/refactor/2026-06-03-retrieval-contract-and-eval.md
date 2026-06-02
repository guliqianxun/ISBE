---
status: Draft
date: 2026-06-03
author: liuzhiheng (with Claude)
phase: 重构准备 · 第 2 步（检索契约与评估 · 设计）
scope: 给"相关性 / 检索质量"一个可隔离、可测试、可评估的家。本步只出设计，不写实现。
sample_domain: motorcycle（200cc+ 摩托车市场调研）
relates: 2026-06-02-functional-architecture.md（修订其 F2/F5 边界）
revisions:
  - r1 2026-06-03 first-principles 草案
  - r2 2026-06-03 锚定行业标准（Cranfield/TREC qrels+pooling、RAG eval RAGAS/TruLens、LLM-as-judge 校准、级联打分、pytrec_eval）；修正召回框架（triage 真召回 vs 采集召回）
  - r3 2026-06-03 最小闭环检验落地（见 §9）：建 `isbe.triage` 隔离块 + 冻结真实采集集 + 机制测试绿 + 摩托车回归待 qrels
---

# 检索契约与检索评估（设计）

> 起点批评（用户，2026-06-03）：
> *"对于任意一个领域的检索——该搜什么、怎么搜、搜了多少、质量如何——没有一个明确的评估和隔离，这不符合 TDD。"*
>
> 这个批评是对的。本文给出一个让它**可隔离、可断言、可回归**的设计。

---

## 0. 诊断：相关性现在"没有家"

当前管线（见 [功能架构图](2026-06-02-functional-architecture.md)）：

```
F2 采集(raw→facts) → F5 检索(SQL 时间窗) → F4 生成(LLM 读, 隐式过滤)
```

"什么算相关、搜够没、搜准没"这件事被摊在三处，没有一处可测：

1. **collector 配置**——摩托车 `topic.yaml` 里的 `exclude_keywords: [scooter, moped, 50cc, 125cc, e-bike]` 其实是一份相关性契约，但它埋在 exclude 列表里，且只有否定项；
2. **一句假设 + LLM 兜底**——`# dedicated moto media means every entry is on-topic. LLM does relevance filtering` —— 假设对不对没人验，LLM 滤得好不好没人测；`article_reviews` 的逐条评价是 best-effort、不留痕、不打分、不跟人工校准；
3. **你读周报时的脑子**——最终相关性判断在这里，从不落成数据。

**TDD 测不了它，是因为被测对象不存在**：没有 fixture（领域的金标准）、没有指标（可断言的数）、没有隔离（"判断相关"不是一个有边界的函数）。

---

## 1. 四个问题 = 四个被混在一起的关注点

| 问法 | 真概念 | 谁该拥有它 |
|---|---|---|
| 该搜什么 | 检索意图 / 信息需求（声明） | **检索契约**（§2） |
| 怎么搜 | 检索策略（机制） | collector（不变）+ 由契约驱动取词 |
| 搜了多少 | 覆盖 / 召回（代理指标） | **检索评估**（§4） |
| 质量如何 | 精度 / 相关性 | **Triage 选品层**（§3）+ 评估（§4） |

核心架构动作：在 **F2 采集** 与 **F4 生成** 之间插入一个一等公民块 **Triage（选品与相关性）**，并配一个 **评估（Eval）** 旁路。相关性从此有家。

---

## 2. 检索契约（"该搜什么"的显式化）

每个域**一份**声明式契约，是采集取词、triage 打分、评估打靶三者共享的**单一事实源**。

把摩托车现在散落的隐性知识抬成显式契约：

```yaml
# 建议落点：topic.yaml 新增 retrieval: 块（机器读）；也可日后毕业为 memory reference/
retrieval:
  intent: "200cc+ 摩托车市场调研：新车发布、改款年款、定价、上市、第一手评测/对比、影响购买的行业动向"
  version: 1                       # 改契约即 bump，金标准 fixture 钉版本
  in_scope:
    - 排量 >= 200cc 的量产 / 概念摩托车
    - 新车型发布、改款、定价、上市时间
    - 第一手评测、长测、对比
    - 影响购车决策的行业动向（厂商策略、关税、排放法规）
  out_of_scope:
    - 踏板 / 轻便摩托（scooter / moped）、<200cc、纯电助力
    - 赛事八卦 / 车手绯闻（除非影响量产车）
    - 配件 / 服饰促销软文
  key_entities:                    # 用于覆盖度量
    brands: [Kawasaki, Yamaha, Honda, KTM, Aprilia, Suzuki, BMW, Triumph]
    segments: [naked, sport, ADV, cruiser, retro]
  must_not_miss:                   # 锚点：本周若发生，则必须命中（最强召回信号）
    - 任一主流厂商 200-500cc 新车 / 改款发布
  quality_bar: "好内容 = 含可决策信息（参数/价格/上市/对比结论），非纯转发标题党"
```

- 现 `exclude_keywords` → 折进 `out_of_scope`（含义不变，但变成有正有负的完整声明）。
- **一处声明，三处消费**：collector（给 arxiv 这类需要造 query 的域提供取词，摩托车这种纯 RSS 域不造 query）、triage（打分依据）、eval（打靶标准）。这就是"隔离"——领域意图只有一个地方说，不再散落。

---

## 3. Triage 选品层（相关性的家 · 可隔离）

一个尽量纯的函数，是整套设计可 TDD 的关键：

```python
# 签名（设计，未实现）
def triage(items: list[FactItem], contract: RetrievalContract) -> TriageResult: ...

class RelevanceScore:
    item_id: str
    relevant: bool          # 是否留下
    score: float            # 0..1 相关度
    reason: str             # 为什么（可读、可审计）
    matched: list[str]      # 命中的 in_scope / entity / out_of_scope 标记

class TriageResult:
    kept: list[FactItem]
    dropped: list[tuple[FactItem, str]]     # (item, 丢弃理由)
    scores: dict[str, RelevanceScore]
    report: CoverageReport                  # §4
```

要点：

- **输入输出全是数据** → 喂 fixture、断输出，天然可测。
- **打分器形态已定 = 级联 two-stage（行业标准）**：阶段一规则粗筛（`out_of_scope` 关键词 + 实体匹配，召回导向、零 LLM 成本、完全可解释）→ 阶段二 LLM-judge 只细判灰区（精度导向）。这正是 IR 里 `BM25 → cross-encoder reranker` 的级联范式（cheap recall filter → expensive precision stage）。设计只定**接缝**；测试只对金标准（qrels）断言，与实现无关——换打分器，测试不动。
- 现有 `article_reviews` 的逐条 LLM 评价是阶段二 LLM-judge 的天然种子：把它的判断**落成 `RelevanceScore` 数据**（而非只渲染进模板），就有了 LLM-judge 雏形 + 可与人工标注校准的素材。**LLM-judge 必须按文献做校准**（§8）：报告与人工的一致性（Cohen's κ），并缓解 position / verbosity / self-enhancement 偏置。
- **红线相容**：单向流（采集→triage→生成）；契约是用户手写（同 feedback，红线 #6）；triage 不回写 memory。

---

## 4. 检索评估（Cranfield/TREC 范式落地）

采用 Cranfield 范式的标准三件套：**collection（采集集快照）+ topics（检索契约）+ qrels（相关性判定）**。

### 4.1 qrels 金标准（Cranfield relevance judgments）

冻结某域某周的快照 + 人工判定：

```
tests/eval/motorcycle/2026-W19/
  collection.jsonl   # 该周采集集快照: {id, source, headline, summary, url, published_at}
  qrels.jsonl        # 相关性判定: {id, rel: 0|1|2, must_hit: bool, note}
                     #   rel: 0=不相关 1=相关 2=高价值; 二值化规则 rel>=1 算相关(钉死, 防 borderline 漂移)
  contract.yaml      # 钉住当时的契约版本(= TREC topic)
```

- **关键事实：采集集小到可全标**（摩托车一周 ~29 条），不同于 TREC 几百万文档须**池化(pooling)**——这里能对**整个采集集**做穷尽判定，所以 triage 召回是**真召回**，不是池化近似。
- 单标注者（你）即 ground truth；引入 LLM-judge 时按 §8 报告 judge↔human 的 Cohen's κ。
- 标一次复用为永久回归基线；指标计算直接喂 `pytrec_eval`（trec_eval 的 Python 绑定），不自己写。

### 4.2 指标（区分两种召回 —— 这是上一版框糙、本版修正的核心）

**召回必须拆成两层**，各对应不同问题、不同可测性：

| 召回类型 | 定义 | 可测？ | 归因 |
|---|---|---|---|
| **Triage 召回** | 相关∩kept / 全部相关（相对**已采集集**） | ✅ **真召回**（采集集可全标） | triage 打分器漏没漏 |
| **采集召回** | 相对**真实世界**的相关全集 | ❌ 无 oracle（开放域不可知） | 信源覆盖，**与 triage 无关** |

triage 层用标准 IR 指标（喂 qrels 由 `pytrec_eval` 算）：

| 指标 | 标准名 | 定义 | 可断言阈值 | 答哪个问题 |
|---|---|---|---|---|
| **Precision** | set precision (= RAGAS context-precision 同源) | 相关 ∩ kept / kept | `>= 0.8` | 质量如何 |
| **Recall** | set recall（相对采集集，真召回） | 相关 ∩ kept / 全部相关 | `>= 0.9` | 搜了多少（triage 侧） |
| **F1** | — | precision/recall 调和 | 趋势 | 综合 |
| **Anchor recall** | TREC "known-item" 变体 | must_hit ∩ kept / must_hit | `== 1.0` | 搜了多少（硬约束） |

采集召回不可测，用**代理信号**回答（明确标注为代理，非 true recall）：

| 代理信号 | 定义 | 阈值 | 抓什么 |
|---|---|---|---|
| **Source liveness** | 配置优质源里本周真出数的比例 | `== 1.0` | 死链 / 失效 feed |
| **Entity coverage** | 本周活跃必盯实体中被命中比例 | 趋势 | 信源盲区 |

> **诚实声明（写进系统文档）**：triage 召回是真召回（采集集穷尽可标）；采集召回**没有 oracle**，只能用 source liveness + entity coverage 这组**代理信号**逼近，不冒充 true recall。上一版把两者糊成"代理指标"，退让过头——**triage 侧能测真召回**。

### 4.3 精度的统计严谨性（kept 集大时）

若某域 kept 集很大（不可全标），按标准做**分层抽样估计** precision：抽 N 条标注，报 `precision ± 置信区间`，而非声称全标。摩托车现规模无需，但设计要为大域留这条标准路径。

---

## 5. TDD 流程（先写失败的测试）

这正是你要的"先有契约、先有失败测试、再有实现"：

1. **冻结 + 标注**：跑一周 motorcycle 采集 → 导出 `source_items.jsonl` → 你人工标 `labels.jsonl`（含 must_hit 锚点）。
2. **写失败测试**（指标用 `pytrec_eval` 算，断言用标准 IR 指标名）：
   ```python
   def test_motorcycle_triage_meets_bar():
       items    = load_collection("motorcycle/2026-W19/collection.jsonl")
       contract = load_contract("motorcycle/2026-W19/contract.yaml")   # = TREC topic
       qrels    = load_qrels("motorcycle/2026-W19/qrels.jsonl")
       result   = triage(items, contract)
       m = evaluate(result, qrels)          # 内部走 pytrec_eval
       assert m.anchor_recall == 1.0        # 硬约束
       assert m.precision     >= 0.8
       assert m.recall        >= 0.9        # triage 真召回(相对采集集)
   ```
   现状下它**必然红**：当前等价于"全留"，precision 被噪音拖垮；且根本没有 `triage`。
3. **实现 triage 到变绿**，迭代打分器。
4. **回归**：每次改契约/打分器/prompt，重跑；精度跌则测试红。"质量如何"第一次变成有阈值的数 = TDD 成立。

---

## 6. 对功能架构图的修订

[2026-06-02 架构图](2026-06-02-functional-architecture.md) 里 **F5「检索」= "SQL 时间窗 + memory 过滤"** 这个写法，恰好掩盖了"相关性无主"。修订为：

```
F2 采集(raw→facts, 只管机制, 不声称相关性)
  → 【新】FT Triage 选品与相关性(契约打分 → kept/dropped + scores + 覆盖报告)
  → F5 检索(时间窗 + memory + 读 triage 的 kept 集)
  → F4 生成
            ⤷ 旁路: Eval 评估(金标准 → 指标 → 喂 observability)
```

- 新增块 **FT（Triage & Relevance）** + 旁路 **Eval**，二者共享 §2 检索契约。
- collector 不再隐含"条条相关"的假设；该假设要么被 triage 验证，要么被指标证伪。
- Qdrant / 语义检索**仍归 v2**——triage 打分器起步用规则 + LLM-judge，**不依赖向量**。本设计是质量 backbone，不是提前做语义检索。

---

## 7. 范围与开放问题（待你拍板，下一步前确认）

1. **契约落点**：放 `topic.yaml` 的 `retrieval:` 块（机器读，推荐）vs 独立 `retrieval.yaml` vs memory `reference/`。建议先 topic.yaml，日后可毕业。
2. ~~**打分器起步形态**~~ **已定 = 级联 two-stage（规则粗筛 + LLM 细判）**，见 §3 / §8。
3. **指标计算 adopt vs build**（OSS-first）：`pytrec_eval`（trec_eval 绑定，算 P/R/nDCG/MAP 的事实标准）直接用；LLM-judge 实现复用 RAGAS/TruLens 的 judge 还是自写薄封装——实现期定。**契约/qrels 加载、triage 接缝自写**（无现成件合身）。
4. **金标准标注成本**：每域每次穷尽标采集集（摩托车 ~29 条/周），谁标、多久标一次、是否只标"换契约/换打分器时"的回归周。
5. **与 ADR 的张力**：v1 ADR 说"不做横向抽象除非第 5/6 域有成本"。现已 6 域，且这是质量 backbone 而非花活——我判断该做，但要你确认它**插队**到统一 collector 基类之前。
6. **是否把指标接进 `observability`**：让每期 digest 自带一行"本期 triage：留 X/Y，P≈_ R≈_（按最近 qrels）"，使质量可持续被看见。

---

## 8. 行业标准对齐（本设计的方法学锚点）

本设计**不是自创**，是把成熟范式落到 ISBE 语境。逐项映射：

| 本设计构件 | 锚定的标准 | 采用方式 |
|---|---|---|
| 检索契约 `retrieval:` | **Cranfield / TREC topic**（`title/desc/narrative` ↔ `intent/in_scope/narrative-style scope`） | 采用其结构与语义 |
| qrels 金标准 | **TREC relevance judgments (qrels)** | 采用；格式对齐 `{qid, docid, rel}` |
| 全标采集集而非池化 | TREC **pooling** 的简化——集合够小则免池化、做穷尽判定 | 适配（我们比 TREC 简单） |
| Precision/Recall/F1/nDCG | 经典 IR 指标 + **`trec_eval` / `pytrec_eval`** 工具 | 直接复用工具算 |
| triage 召回 vs 采集召回 | 系统侧 recall vs collection coverage 的区分 | 适配命名 |
| 级联打分器（规则→LLM） | **two-stage retrieval**（first-stage retriever → reranker，如 BM25 → cross-encoder） | 采用范式 |
| triage 相关性指标 | **RAG eval 检索侧**：RAGAS `context precision/recall`、TruLens RAG triad `context relevance`；**ARES** | 复用指标定义 + LLM-judge 实现 |
| LLM-judge 校准 | **LLM-as-judge 文献**：MT-Bench/Chatbot Arena (Zheng 2023)、**G-Eval**；已知偏置 position/verbosity/self-enhancement | 采用：报告 judge↔human **Cohen's κ**，做偏置缓解 |
| 抽样估计 precision | 评测学标准（置信区间） | 大域时采用 |

**与本项目既有原则一致**：[`2026-05-14-oss-survey-first.md`](../superpowers/specs/2026-05-14-oss-survey-first.md) 说"硬方法学问题先调研 OSS 再自建"——评估方法学正属此类，故先锚标准、能复用就复用（pytrec_eval / RAGAS judge），只在无合身件处自写（契约/qrels 加载、triage 接缝）。

### 参考（方法学来源）

- Cleverdon, *Cranfield* evaluation paradigm；TREC qrels & pooling（Voorhees & Harman, *TREC: Experiment and Evaluation in IR*）
- `trec_eval` / `pytrec_eval`（Van Gysel & de Rijke）— 指标计算事实标准
- Two-stage retrieval / reranking（BM25 → cross-encoder，如 monoBERT / ColBERT 线）
- RAG eval：**RAGAS**（Es et al.）、**TruLens** RAG triad、**ARES**（Saad-Falcon et al.）
- LLM-as-judge：**Zheng et al. 2023**（Judging LLM-as-a-Judge, MT-Bench）、**G-Eval**（Liu et al.）；偏置与缓解综述
- 标注一致性：**Cohen's κ** / Krippendorff's α

> 注：以上为方法学定位锚点；实现期需对每个拟复用件做一次合身性 spike（尤其 RAGAS/TruLens 的 per-query 假设 vs ISBE 常驻契约的差异）。

---

## 9. 最小闭环检验（r3，2026-06-03 落地）

按"先检验"做了一个端到端最小切片，验证这套设计**可实现、可测**：

**建的东西**
- `src/isbe/triage/`：隔离的 FT 块（纯函数，无 IO）——`models`（Item/RelevanceScore/TriageResult/Qrel/EvalMetrics）/ `contract`（= TREC topic，pydantic `extra=forbid`）/ `scorer`（级联阶段一规则；阶段二 LLM 留接缝）/ `eval`（set-based P/R/F1/anchor，手算，注明生产委托 pytrec_eval）。
- `scripts/eval/freeze_collection.py`：抓真实 RSS、**过滤前**冻结采集集 + 出 qrels 模板。
- 真实 fixture：`tests/eval/motorcycle/2026-W23/`（**54 条真实采集集** + contract.yaml + qrels 模板）。
- 测试：`tests/triage/test_eval_mechanism.py`（合成数据验机器，**3 绿**）、`tests/eval/test_motorcycle_triage.py`（真实回归，**待 qrels，现 skip**）。

**验证结论**
- 机制可测：keep-all 基线 precision=0.5 < 0.8 门槛、规则 triage=1.0 —— 指标能区分好坏 triage，"质量"成了有阈值的数。✅
- ruff clean；改动**纯增量**（未碰任何现有代码），不影响既有 73 测试。
- **一条实证发现（直接印证设计）**：采集集里 `Klim Badlands Pro Pants Review`（摩托裤评测）属 `out_of_scope`（配件/服饰），但 `out_of_scope_keywords` **抓不到**（无 "pants"）——纯关键词阶段一会让它混进周报。**这就是阶段二 LLM-judge 必要性的活样本**，也说明 `quality_bar` 须由语义判而非关键词判。

**下一步（你来 + 我来）**
1. **你标 qrels**（ground truth 不可程序捏造）：编辑 `tests/eval/motorcycle/2026-W23/qrels.template.jsonl`，每行填 `rel: 0|1|2`、锚点设 `must_hit: true`，另存为 `qrels.jsonl`。54 条，约 15-20 分钟。
2. 标完跑 `uv run pytest tests/eval` —— 大概率 **红**（关键词阶段一精度不够，如摩托裤漏网）。这就是 TDD 的失败测试。
3. 我据红的结果实现**阶段二 LLM-judge**（复用 `article_reviews` 种子 + §8 校准）到变绿。

---

## 附：本设计没做什么（防 scope 蔓延）

- 不实现 triage / eval / 契约解析（本步只设计）。
- 不碰 Qdrant / 语义检索（v2）。
- 不动 collector 抓取机制本身（去重/基类统一是另一条重构线）。
- 不改 5 段 digest 契约（那是 F4 的事）。
