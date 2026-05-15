# ADR — 5 段 digest 契约（v1）

**Date**: 2026-05-13
**Status**: Accepted
**Supersedes**: 早期"3 段 digest"（事实 / 分析 / 蒸馏），见 `archive/2026-05-07-p1-nowcasting-mvp.md`
**Scope**: 所有 active topic 的 LLM 输出格式

## 决策

所有 topic 的 LLM digest 输出**必须**走 5 段结构。section 顺序固定，名字固定。

| # | Section 名 | DigestSectionKind | 作用 |
|---|---|---|---|
| 1 | `TL;DR` | `tldr` | 一段话快速过完本期（120-200 字） |
| 2 | 逐条事实评价 | `paper_reviews` / `article_reviews` / `news_reviews` ... | 对本期每条 fact 的 1-3 行评论 |
| 3 | （可选）第 2 个 fact 桶 | `repo_updates` / `brand_notes` / `company_notes` / `sec_findings` ... | 跨条聚合：仓库更新表 / 品牌动态 / 公司动态 / SEC 文件解读 |
| 4 | 分析 | `analysis` | 把本期事实**对照 memory 里的论点**，看哪些被印证 / 削弱 |
| 5 | 蒸馏 | `distillation` | 提议给 memory 加 / 改的论点 → 进 `.pending` 等用户 review |

### 形式约束

- 每段以 `## <中文名>` 开头（split_sections 按 markdown H2 切）
- 第 2 桶**可省**（如纯 arxiv topic 只有 papers，没第二类 fact）
- 蒸馏段输出 `DRAFT[<target_path>]:` 块，target_path 必须以 `topics|reading|feedback|user|reference` 之一开头，`.md` 结尾
- 系统 prompt 必须显式说"不要给买卖建议 / 不要算盈亏" —— 金融域强约束，其他域 carry over

### 模板侧

`src/isbe/topics/<topic>/templates/weekly.j2`（或 daily.j2）按以下顺序渲染：

```
1. TL;DR
2. 上期对比（从 _shared/comparison.py 自动产出）
3. <每条 fact 的 card / table>
4. 分析
5. Memory drafts（蒸馏段的 .pending 草稿摘要）
6. <details> audit footer（fingerprint / 跑时长 / token / facts 引用 / memory@rev）
```

## 起因

P1 初版只要求"三段：事实 / 分析 / 蒸馏"（见 `archive/2026-05-07-p1-nowcasting-mvp.md`）。
真跑出来后扫读体验差：

- "事实"段跟 TL;DR 信息重叠
- 没有"上期对比"的话用户读不出"什么变了"
- 多 fact 桶的 topic（NVDA 的 prices/news/SEC）展开后一团乱

2026-05-13 一日冲刺重新设计模板（commit `808e414`）后，单份周报体积从 2.9KB → 9KB，扫读密度反而高很多（因为有清晰的层级和上期对比表）。

第 2 个域（NVDA）验证：5 段结构原样复用、`split_sections` 单一实现解析两种 flavor、`comparison.py` 抽出来后 NVDA 几乎零代码拿到"上日对比"。

到 2026-05-15 已 6 个域都走这个契约：

| Topic | 第 2 桶 (DigestSectionKind) |
|---|---|
| nowcasting | `repo_updates` |
| video-generation | （省略） |
| image-restoration | （省略） |
| nvda | `company_notes` / `sec_findings` |
| motorcycle | `brand_notes` |
| china-tech | `company_notes` |

## 后果

**好的**：
- **可扩展**：加新域不用重新设计模板，只需挑第 2 桶的 kind（或省略）
- **可解析**：`_shared/digester_utils.py::split_sections` 单一实现，name_map 加一行映射即可识别新 kind
- **比较稳定**：`_shared/comparison.py` 对 H2 切分后的桶做 diff，第 2 桶只要 kind 标了就自动有"上期对比"
- **memory 飞轮**：蒸馏段强制产 `.pending`，用户 review accept → memory@rev 进下次 prompt，闭环

**约束**：
- LLM 偶尔会**漏 section**（实测：5 段中蒸馏段有 ~5% 的几率全 `—`，重跑就好）
- 改契约成本高：6 个 topic + 模板 + 测试 + comparison 都耦合到这个结构
- 第 2 桶 kind 仍在膨胀（已经 4 个），需要时再设计上限 / 抽象，现在先线性加

## 反对意见 + 反驳

**Q1**："为什么不让 LLM 自己决定输出几段？"
**A**：试过，结果是每周不一样、template 渲染拼不上、对比也对不上。固定契约 = 可比较 + 可工具化。

**Q2**："5 段是不是过度拘束 LLM？"
**A**：实测没拘束。LLM 在每段内仍有完全自由发挥（评价的角度、分析的视角、蒸馏的方向都是自由的）。

**Q3**："要不要把 section 名都英文 / 都中文？"
**A**：内部 enum (DigestSectionKind) 用英文 (snake_case)，渲染给用户看的 section 标题用中文。两者通过 `digester_utils.py::name_map` 隔离。

## 关键文件 / 入口

- `src/isbe/topics/base.py::DigestSectionKind` — Literal 枚举
- `src/isbe/topics/_shared/digester_utils.py::split_sections` — H2 → DigestSection 的解析
- `src/isbe/topics/_shared/comparison.py` — 上期对比
- `src/isbe/llm/prompts.py` / `finance_prompts.py` / `motorcycle_prompts.py` / `china_tech_prompts.py` — 各域 system prompt（都强约束 5 段）
- `src/isbe/topics/<topic>/templates/*.j2` — 渲染模板

## 给协作者 / 新 topic 作者的 checklist

加一个新 topic 时：

- [ ] system prompt **明确写出 5 段名 + 每段输出要求**
- [ ] 若有第 2 桶：在 `DigestSectionKind` Literal 里加一行（如 `"brand_notes"`），在 `digester_utils.py::name_map` 里加映射（如 `"品牌动态": "brand_notes"`）
- [ ] 模板里按 1-2-3-4-5-audit 顺序渲染
- [ ] 跑一次 e2e，**人工读一份**，确认 5 段都出
- [ ] 蒸馏段至少能产 1 条 `DRAFT[topics/<topic>.theses.md]:` 草稿（否则系统不闭环）

## 历史

- **2026-05-07**: P1 nowcasting MVP 用"3 段"（事实/分析/蒸馏）
- **2026-05-12**: ADR 写了 v1 scope，digest 结构未单独立约
- **2026-05-13**: 模板 v2 重写（`808e414`），实质形成 5 段；c0cc31f 删掉"事实"段（与 TL;DR 重复），最终 5 段固化
- **2026-05-13**: NVDA 第 2 个域复用同结构，验证抽象
- **2026-05-13**: motorcycle 第 3 个域引入 `brand_notes` 第 2 桶
- **2026-05-14**: china-tech 第 4 个域引入 `company_notes` 第 2 桶
- **2026-05-15**: 这份 ADR 把契约从隐式（散在模板）变成显式
