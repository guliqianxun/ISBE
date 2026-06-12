---
status: Accepted
date: 2026-06-13
supersedes: 2026-06-03-retrieval-contract-and-eval.md 的"triage = 仅过滤"框架（收紧为三步管道）
revisions:
  - r1 2026-06-13 检索 scope 扩张：从"facts 后加一道过滤"扩为"检索 = 可评估子系统(宽召回→语义筛→显著性排)"
---

# 智能检索管道（检索 scope 扩张）

## 0. 触发这次扩张的诊断

用户一句话戳中要害："现在的检索/事实层，我随便问个 AI 都比它好。"

诚实复盘当前设计：**哑抓取 → 哑存储 → 哑 SQL 扫(时间窗 + keyword ilike) → 只在最后写报时用一次 LLM**。
智能被放在管道**错误的一端**——检索环(决定搜什么/留什么/谁重要)零智能，等 LLM 出场时烂检索已定型，救不回来。

但"哑底座"**不是失误，是故意的**：facts 表是 ask-an-AI 唯一给不了的东西——grounded / 可审计 /
可重抓 / 跨期去重 / 记得"上周给你看过啥" / 长得像你。问题不在有哑底座，在底座上**没装智能**。

## 1. Reframe

> **检索不再是一句 SQL WHERE，而是 grounded 底座之上的三步确定式管道：宽召回 → 语义筛 → 显著性排。**
> facts 仍是哑的可审计底座，智能装进这三步，每步可 trace 可回归（非 ReAct，守红线 #3）。

```
F1 契约(RC1)  intent + facets + in/out scope + must_not_miss + quality_bar  (一处声明，三步消费)
                │
F2 ACQUIRE 宽召回(RC2/RC3)  契约 facets → 每面一条 S2 查询(S2 自带语义排序) → top-K 并集
   ← 智能①                  = 语义宽召回，取代"一条关键词字串查 arxiv"。撒大网，召回导向。
                │
F3 哑底座(不变)  Postgres 存"过滤前"宽池 + 元数据(citation/fields/refs) + 跨期去重
                │ 时间窗取全量(不再 keyword ilike)
F5 检索智能     ① TRIAGE 语义筛(RC4)  规则粗筛 → LLM-judge 判 in/out scope + 打 facet 标签  ← 智能②
                ② RANK 显著性排(RC5)   citation 分位 + 引用速度 cite/age + 锚点 → 重要性分  ← 智能③(纯元数据)
                ③ ACCOUNT 覆盖核算(RC8-min)  留X/全Y + 精度 + 锚点命中 + 分面覆盖 → 📊 一行
                │ 喂"已筛·已排·已分面"集合(非 SQL 裸 dump)
F4 生成        digester 读 已排序集 × memory → 5 段，必读置顶 + 按 facet 分组 + 折叠
                ▲ qrels(人标·LLM预标) 校准 TRIAGE 的 judge，eval 闸住精度 + 锚点
```

## 2. 三步装什么 / RC / LLM / 红线

| 步 | 现在 | 变成 | RC | LLM | 红线 |
|---|---|---|---|---|---|
| Acquire 宽召回 | 关键词字串查 arxiv，写法不对就漏 | facets 驱动多查询 + S2 语义排序 + top-K 并集 | RC2/RC3 | 否（S2 干）¹ | 单向流 ✓ |
| Triage 语义筛 | keyword ilike（AVD2 误杀、垃圾漏网） | 规则 → LLM-judge 语义判 + 打 facet | RC4 | 是（固定模板步） | 非 ReAct ✓ trace ✓ |
| Rank 显著性 | **无** | citation + 速度 + 大厂 + 锚点 → 重要性分 | RC5 | 否（纯函数） | 纯函数 ✓ |

¹ S2 API 本身是语义相关性排序，"每面一条查询 + 并集"已是语义宽召回，远胜 arxiv keyword ilike。
   LLM 自动扩词留 v-next，避免把 LLM 塞进采集层。

## 3. 三个锁定决策（2026-06-13 用户拍板）

1. **成本 OK**：论文检索接受宽召回 + 全量 LLM-judge。不为省 judge 成本牺牲召回广度；Acquire 的 top-K 可调但默认放宽。
2. **召回是假指标，不 gate**：开放域"搜了多少 vs 真实世界全集"不可知，标更宽 qrels 测召回增益 = 假指标。
   **不建该测量机器**。eval 只闸 **精度 + 锚点命中(must_not_miss)**；triage 在已标池上的召回数顶多当免费信息量，不追、不标更宽池。
   （收紧了 2026-06-03 设计 §6"召回可测=核心红利"——召回可测降为采集机制副产物，非产品目标。）
3. **采集层产线改动 OK**：collector 退化 + S2 多查询，video-gen 单域先行影子模式，验稳再切，其余域不动（契约缺省→直通）。

## 4. 更新后的 MVP 范围

**MVP = RC1 契约 + RC2 宽召回 + RC4 语义筛 + RC5 显著性 + RC8-min(精度+锚点，无召回 gate)。**
比之前综合的"RC1+RC4±RC5"更激进——因为"智能不该只装过滤一环，采集和排序都得装"。
砍：RC6 谱系/近重(超 arxiv_id 精确去重)、Qdrant 语义、S2 外多源、召回测量。

## 5. 迁移序列（全程保持绿）

- Step A ✅ RC5 显著性(纯函数，不依赖 qrels，已建+测：`significance.py`、`test_significance.py`)
- Step B   apply_triage 直通安全网 + 接缝接入 digester（无契约时行为不变，golden 守）
- Step C   video-gen qrels 人标(bootstrap 88 条) → 失败测试(精度+锚点，无召回 gate)
- Step D   stage-2 LLM-judge 实现到变绿(κ 校准)
- Step E   Acquire 宽召回上生产(facets 驱动 S2 多查询) + collector 退化(video-gen 先行影子)
- Step F   Rank + Account 接进 digester：必读置顶 + 分面分组 + 📊 质量行

## 6. 留待用户

- Acquire 的 K（每面截多少）、judge 保守方向（拿不准宁留/宁弃）——定检索工作点。
- facets 增删（contract.yaml 待确认）、bootstrap 标 88 条 qrels（解锁 Step C/D）。
