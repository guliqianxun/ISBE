"""RC4 stage-2：LLM-judge 语义相关性细判。

stage-1 规则（标题关键词）只能高精度粗筛；宽召回带来的关键词邻近噪音——
军用/生物医学雷达 vs 降水雷达、经济/疾病 nowcasting vs 降水 nowcasting——
标题里没有可抓的负词，只有语义判能分开。judge 拿契约的 in_scope/out_of_scope/
quality_bar 对每篇判 IN/OUT + facet。

LLM IO 经注入的 complete_fn（默认 lazy-import llm.client.complete），便于 mock 测试，
也让 triage 的纯函数模块（scorer/significance/eval）保持无 IO。
qrels 只用来事后校准（κ），不是 judge 运行前提。
"""

from __future__ import annotations

import re
from collections.abc import Callable

from isbe.triage.contract import RetrievalContract
from isbe.triage.models import Item, RelevanceScore, TriageResult

# complete_fn(system, user) -> str（模型输出文本）
CompleteFn = Callable[[str, str], str]

_SYSTEM = (
    "你是科研论文相关性判定器。给定一个检索范围（intent / in_scope / out_of_scope / "
    "quality_bar）和一批论文（标题 + 摘要），逐篇严格判定它是否属于该范围。"
    "判定铁律：共享关键词但研究对象不同的一律判 OUT —— 例如军用/生物医学雷达 vs 降水雷达、"
    "经济或疾病 nowcasting vs 降水 nowcasting、视频理解/攻击 vs 视频生成。"
    "只输出每篇一行，格式严格为 `序号|IN或OUT|facet|≤15字理由`（OUT 时 facet 留空），"
    "不要任何多余文字、不要表头。"
)

_LINE = re.compile(r"^\s*\[?(\d+)\]?\s*\|\s*(IN|OUT)\b\s*\|?([^|]*)\|?(.*)$", re.IGNORECASE)


def _build_user(contract: RetrievalContract, batch: list[Item]) -> str:
    lines = [f"intent: {contract.intent}"]
    if contract.in_scope:
        lines.append("in_scope:\n" + "\n".join(f"  - {s}" for s in contract.in_scope))
    if contract.out_of_scope:
        lines.append("out_of_scope:\n" + "\n".join(f"  - {s}" for s in contract.out_of_scope))
    if contract.facets:
        lines.append("facets（IN 时从中选一个最贴的）: " + ", ".join(contract.facets))
    if contract.quality_bar:
        lines.append(f"quality_bar: {contract.quality_bar}")
    lines.append("\n逐篇判定（每篇一行 `序号|IN或OUT|facet|理由`）：")
    for i, it in enumerate(batch, 1):
        abstract = (it.summary or "")[:280].replace("\n", " ")
        lines.append(f"[{i}] {it.headline}\n{abstract}")
    return "\n".join(lines)


def _parse(text: str, n: int) -> dict[int, tuple[bool, str, str]]:
    """解析模型输出 → {0-based index: (is_in, facet, reason)}。容忍多余行。"""
    out: dict[int, tuple[bool, str, str]] = {}
    for raw in text.splitlines():
        m = _LINE.match(raw)
        if not m:
            continue
        idx = int(m.group(1)) - 1
        if not (0 <= idx < n):
            continue
        is_in = m.group(2).upper() == "IN"
        facet = m.group(3).strip()
        reason = m.group(4).strip()
        out[idx] = (is_in, facet, reason)
    return out


def _default_complete_fn(tier: str) -> CompleteFn:
    from isbe.llm.client import complete  # lazy：保持 triage 包可无 llm 依赖导入

    def _fn(system: str, user: str) -> str:
        return complete(system=system, user=user, tier=tier, max_tokens=1500).text

    return _fn


def judge_items(
    items: list[Item],
    contract: RetrievalContract,
    *,
    complete_fn: CompleteFn | None = None,
    batch_size: int = 20,
    tier: str = "fast",
    log: Callable[[str], None] = lambda _m: None,
) -> dict[str, RelevanceScore]:
    """对每篇语义判 IN/OUT。整批失败或漏判 → 默认 IN（recall-safe），标低置信。"""
    fn = complete_fn or _default_complete_fn(tier)
    scores: dict[str, RelevanceScore] = {}
    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        try:
            text = fn(_SYSTEM, _build_user(contract, batch))
            verdicts = _parse(text, len(batch))
        except Exception as e:  # noqa: BLE001 — 整批失败不阻断其余
            log(f"  judge batch @{start} failed: {e!r} (recall-safe keep)")
            for it in batch:
                scores[it.id] = RelevanceScore(
                    it.id, True, 0.5, f"judge failed: {type(e).__name__}", stage="llm",
                )
            continue
        for i, it in enumerate(batch):
            v = verdicts.get(i)
            if v is None:
                scores[it.id] = RelevanceScore(
                    it.id, True, 0.5, "judge unparsed (kept, recall-safe)", stage="llm",
                )
            else:
                is_in, facet, reason = v
                reason = reason or ("in-scope" if is_in else "out-of-scope")
                scores[it.id] = RelevanceScore(
                    it.id, is_in, 0.9 if is_in else 0.1, reason,
                    matched=(facet,) if facet else (), stage="llm",
                )
        n_in = sum(1 for it in batch if scores[it.id].relevant)
        log(f"  judged batch @{start}: {n_in}/{len(batch)} IN")
    return scores


def apply_llm_judge(
    result: TriageResult,
    contract: RetrievalContract,
    *,
    complete_fn: CompleteFn | None = None,
    batch_size: int = 20,
    tier: str = "fast",
    log: Callable[[str], None] = lambda _m: None,
) -> TriageResult:
    """级联：对 stage-1 留下的再过 LLM-judge，OUT 的移入 dropped。返回新 TriageResult。"""
    scores = judge_items(
        result.kept, contract, complete_fn=complete_fn,
        batch_size=batch_size, tier=tier, log=log,
    )
    refined = TriageResult()
    refined.dropped.extend(result.dropped)        # 保留 stage-1 弃
    refined.scores.update(result.scores)
    for it in result.kept:
        sc = scores.get(it.id)
        if sc is not None:
            refined.scores[it.id] = sc
        if sc is not None and not sc.relevant:
            refined.dropped.append((it, f"llm-judge OUT: {sc.reason}"))
        else:
            refined.kept.append(it)
    return refined
