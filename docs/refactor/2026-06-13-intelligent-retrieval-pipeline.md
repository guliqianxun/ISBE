---
status: Accepted
date: 2026-06-13
supersedes: 2026-06-03-retrieval-contract-and-eval.md 的"triage = 仅过滤"框架（收紧为三步管道）
revisions:
  - r1 2026-06-13 检索 scope 扩张：从"facts 后加一道过滤"扩为"检索 = 可评估子系统(宽召回→语义筛→显著性排)"
  - r2 2026-06-13 实时雷达纠正（见 §7）：这是实时信息系统、优先最新；采集近窗化、显著性主序改新鲜度、绝对引用降级、锚点改"新发布"
  - r3 2026-06-14 step-E 固化（见 §8）：透明双门（out_of_scope 标题 + require_any 正向）、优先级分层、judge 降级、本地源 adapter 解时效、关键词一词多义硬限；§9 B+A 集成计划
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

## 6. 实时雷达纠正（r2）

**ISBE 是实时信息搜集系统，优先"本期最新"，不是"找领域里程碑/文献综述"。** 早期把检索当成
"按绝对引用排出领域最重要论文"是认知偏差。三处后果与纠正：

| 错的做法 | 为什么错 | 纠正 |
|---|---|---|
| 采集 `year=2023–2026` 跨多年 | 实时雷达要"本周新增" | 近窗 `pub_date`（近 N 天），按时间倒序 |
| RANK 用绝对 `citationCount` | 偏向老论文（5 年攒引用）；本周新论文 cite≈0 无区分度 | **新鲜度为主序**；citation 降为"略陈化尾巴"的弱辅助 |
| `must_not_miss` = DGMR/MetNet 老奠基作 | 老里程碑出现在本周必读里本身是 bug | 锚点 = "本期新发布/新 SOTA/大厂基座模型" |

对新论文的"显著性/值得读"，真信号不是引用量，而是**大厂/知名实验室署名、SOTA/benchmark 声明、
代码发布**——这些是 stage-2 LLM-judge 的料，不是元数据排序。故 RC5-citation 退为辅助，
RC-fresh（新鲜度）扶正为主序。

**数据源诚实声明**：S2 对最新论文有**索引滞后**（几天到数周），近窗召回会偏少；真·实时源是
arxiv recent listings（服务器侧可达，本机被 WAF 封）。生产应 arxiv-recent 为主、S2 作元数据补充。

## 7. 留待用户

- Acquire 的 K（每面截多少）、judge 保守方向（拿不准宁留/宁弃）——定检索工作点。
- facets 增删（contract.yaml 待确认）、bootstrap 标 88 条 qrels（解锁 Step C/D）。

## 8. step-E 固化（r3，2026-06-14 实跑沉淀）

经 nowcasting / video-generation 多轮实跑，检索子系统定型如下。

### 8.1 管线终态

```
源(本地 papers.db / S2) → ACQUIRE 宽网近窗 → out_of_scope(标题) → require_any(正向门)
   → 优先级分层(core/secondary) → 时间倒序 [→ 可选 stage-2 LLM-judge]
```

### 8.2 透明双门（不靠 judge 的相关性，规则可改）

实跑逼出两条**透明规则门**，配合用：

1. **out_of_scope 负向门（只匹配标题）**：命中即弃。**只看标题**——摘要动机句（"reduce
   economic losses"）全文匹配会误杀真域论文（nowcasting 实测 7/7 误杀）。规则须高精度 recall-safe。
2. **require_any 正向门（匹配全文）**：词被一词多义严重占用时（`precipitation`=化学沉淀、
   `convective`=热对流、`radar`=LiDAR/SAR/车载），负向穷举列不完——改要求**正向命中领域判别词**，
   没有即弃。这是**精度/召回旋钮**：用户改一行 `require_any` 即调，偏召回 + 周表眼筛。

### 8.3 优先级分层（"都算 + 显示优先级"）

`secondary_terms` 命中 → 次级层（靠后），其余 in_scope → 核心层（置顶）。in_scope 全保留、
只是分层。规则驱动、透明可改。

### 8.4 judge 降级（用户不信任）

LLM-judge（`judge.py`）实测能做语义筛（nowcasting 131→48、video-gen 162→71），但
**跑两次结果不同**（同篇 kept→OUT）、偶尔误杀核心、且解析有串行风险。用户明确不信任、不投入加固。
故 **judge 不作默认门**；相关性靠 §8.2 透明双门，judge 仅作可选项（`--judge`）。
qrels/κ 校准用户判为"不是很用"，不作 gate（仅留 anchor/precision，召回不 gate）。

### 8.5 时效性解决：本地源

S2 索引滞后几天 + 未授权限速（常 3/6 查询挂）→ 不适合实时。改接外部每日项目
`I:\essaies\archive\arxiv-cs.CV\papers.db`（195k cs.CV + 4k physics.ao-ph，FTS5，每日 harvest，
当天新鲜，本地秒回无限速）。**这是 cs.CV/ao-ph 域的生产实时源。** 带 `hf_upvotes`（HF 社区热度，
新论文显著性信号，惜当前富集滞后 ~2 周）。

### 8.6 一条不可消除的硬限

`radar`/`precipitation`/`convective` 一词多义 = 纯关键词的最后一公里，**本质需语义判**。
透明门把噪音从大头清掉，但残留少量同词异义（气象雷达 vs LiDAR）只能靠 judge 或眼筛。
对 niche 周报（~12 条），眼筛可接受。

### 8.7 模块清单（isbe.triage + 源 adapter，均纯增量、29 tests）

| 模块 | 职责 |
|---|---|
| `contract.py` | RetrievalContract（intent/in·out_scope/facets/queries/entity_terms/require_any/secondary_terms/must_not_miss） |
| `scorer.py` | stage-1 级联：out_of_scope(标题) + require_any(全文) 双门 |
| `judge.py` | stage-2 LLM-judge（可选，默认不启用） |
| `significance.py` | RC5 显著性（citation+速度+锚点；实时下退为辅助） |
| `priority.py` | core/secondary 分层 |
| `pipeline.py` | retrieve() 组合 Acquire→Triage→Rank→Tier；接受预取 papers |
| `eval.py` | P/R/F1/anchor（对 qrels；非 gate） |
| `_shared/semantic_scholar.py` | S2 源 adapter（退避抗 429） |
| `_shared/local_arxiv.py` | 本地 papers.db 源 adapter（宽 OR 网、近窗、FTS5） |
| `scripts/eval/{freeze_papers,freeze_collection,retrieve}.py` | 冻结 fixture + 端到端 demo runner（--source/--judge/--dump） |

## 9. B + A 集成计划（待施工）

**B 本地源设为 cs.CV/ao-ph 生产默认**：① `local_arxiv.DEFAULT_DB` 改环境变量
`ISBE_LOCAL_ARXIV_DB` 可配；② topic.yaml 加 `source: local`，dispatch 据此选 adapter。

**A 接进真实 digester**：digester 取 facts 处改调 `retrieve()`（已预留 apply_triage 接缝，
契约缺省直通保证向后兼容），周报正文按 core/secondary 分层渲染、时间倒序。

**⚠ 待用户拍板的架构选择（A 的前置）**：本地 DB 是外部读源，与 ISBE facts 层（Postgres）关系两种模型——
- **模型一**：本地 DB → 一个 collector upsert 进 ISBE `papers` facts，digester 照旧读 facts。**守 facts=单一事实源红线**，但多一个导入 flow。
- **模型二**：digester 直接读本地 DB（绕过 facts 层）。简单，但破"采集→存储→检索"单向流红线。
**建议模型一**（守红线）。需用户确认后再写生产代码。

**验证现实**：A 触生产 digester（Prefect+Postgres+MinIO），本机栈未起，只能单元测 + 服务器 smoke。
