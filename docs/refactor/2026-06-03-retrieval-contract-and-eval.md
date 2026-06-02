---
status: Draft
date: 2026-06-03
author: liuzhiheng (with Claude)
phase: 重构准备 · 第 2 步（检索契约与评估 · 设计）
scope: 给"相关性 / 检索质量"一个可隔离、可测试、可评估的家。本步只出设计，不写实现。
sample_domain: motorcycle（200cc+ 摩托车市场调研）
relates: 2026-06-02-functional-architecture.md（修订其 F2/F5 边界）
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
- **打分实现先不锁**（本步不实现）：可纯规则/关键词、可 LLM-judge、可混合。设计只定**接缝**；测试只对金标准断言，与实现无关——换打分器，测试不动。
- 现有 `article_reviews` 的逐条 LLM 评价是 triage 的天然种子：把它的判断**落成 `RelevanceScore` 数据**（而非只渲染进模板），就有了 LLM-judge 打分器的雏形 + 可与人工标注校准的素材。
- **红线相容**：单向流（采集→triage→生成）；契约是用户手写（同 feedback，红线 #6）；triage 不回写 memory。

---

## 4. 检索评估（"搜了多少 / 质量如何"的测量）

### 4.1 金标准 fixture（一次性人工标注，永久回归）

冻结某域某周的快照 + 人工标注：

```
tests/eval/motorcycle/2026-W19/
  source_items.jsonl   # 该周抓到的原始 articles 快照: {id, source, headline, summary, url, published_at}
  labels.jsonl         # 人工标注: {id, relevant: yes|no|borderline, must_hit: bool, note}
  contract.yaml        # 钉住当时的契约版本
```

- 规模小（几十条/周起步），**标一次复用为永久回归基线**。
- 人（你）是相关性的最终裁判——金标准就是把你脑中的判断**落成数据**这一动作。

### 4.2 指标（诚实，区分能测与不能测）

| 指标 | 定义 | 可断言阈值 | 答哪个问题 |
|---|---|---|---|
| **Precision@kept** | relevant ∩ kept / kept | `>= 0.8` | 质量如何（留下的准不准） |
| **Anchor recall** | must_hit ∩ kept / must_hit | `== 1.0` | 搜了多少（必命中漏没漏，最强信号） |
| **False-drop rate** | relevant ∩ dropped / dropped | `<= 0.1` | 质量如何（有没有误杀） |
| **Entity coverage** | 本周活跃必盯实体中被命中的比例 | 趋势观察 | 搜了多少（代理） |
| **Source liveness** | 配置的优质源里本周真出数的比例 | `== 1.0` | 搜了多少（抓死链/失效 feed） |

**诚实声明（必须写进系统文档）**：开放域**没有真召回率**——"本周相关全集"不可知。"搜了多少"只能用 **anchor recall + source liveness + entity coverage** 这组**代理指标**回答，不假装是 true recall oracle。这一句要大声说，免得给系统一个它给不出的承诺。

---

## 5. TDD 流程（先写失败的测试）

这正是你要的"先有契约、先有失败测试、再有实现"：

1. **冻结 + 标注**：跑一周 motorcycle 采集 → 导出 `source_items.jsonl` → 你人工标 `labels.jsonl`（含 must_hit 锚点）。
2. **写失败测试**：
   ```python
   def test_motorcycle_triage_precision():
       items = load_fixture("motorcycle/2026-W19/source_items.jsonl")
       contract = load_contract("motorcycle/2026-W19/contract.yaml")
       result = triage(items, contract)
       m = evaluate(result, load_labels("motorcycle/2026-W19/labels.jsonl"))
       assert m.anchor_recall == 1.0
       assert m.precision_at_kept >= 0.8
       assert m.false_drop_rate <= 0.1
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
2. **打分器起步形态**：纯规则（快、确定、可解释，但弱）vs LLM-judge（强、贵、需校准）vs 混合（规则粗筛 + LLM 细判）。设计已留接缝，实现期再定。
3. **金标准标注成本**：每域每次标几十条，谁标、多久标一次、是否只标"换契约/换打分器时"的回归周。
4. **与 ADR 的张力**：v1 ADR 说"不做横向抽象除非第 5/6 域有成本"。现已 6 域，且这是质量 backbone 而非花活——我判断该做，但要你确认它**插队**到统一 collector 基类之前。
5. **是否把指标接进 `observability`**：让每期 digest 自带一行"本期 triage：留 X/Y，precision≈Z（按最近金标准）"，使质量可持续被看见。

---

## 附：本设计没做什么（防 scope 蔓延）

- 不实现 triage / eval / 契约解析（本步只设计）。
- 不碰 Qdrant / 语义检索（v2）。
- 不动 collector 抓取机制本身（去重/基类统一是另一条重构线）。
- 不改 5 段 digest 契约（那是 F4 的事）。
