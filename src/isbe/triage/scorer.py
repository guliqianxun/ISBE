"""triage() — 级联打分器（two-stage retrieval 范式）。

阶段一（已实现）：规则粗筛，召回导向、零 LLM 成本、完全可解释。
    命中 out_of_scope_keywords 即弃；其余留待阶段二。
    这一级在语义上等价于当前 rss_collector 的 exclude_keywords ——
    即把散落在 collector 的相关性过滤接管到这个可测的块里。

阶段二（预留接缝，未实现）：LLM-judge 细判阶段一留下的灰区，精度导向。
    复用 article_reviews 的逐条评价为种子；须按设计 §8 做 judge↔human 校准。

刻意保持纯函数：输入 list[Item] + 契约，输出 TriageResult，无 IO。
"""

from __future__ import annotations

from collections.abc import Iterable

from isbe.triage.contract import RetrievalContract
from isbe.triage.models import Item, RelevanceScore, TriageResult


def _first_hit(text_low: str, keywords: Iterable[str]) -> str | None:
    for kw in keywords:
        if kw and kw.lower() in text_low:
            return kw
    return None


def triage(items: list[Item], contract: RetrievalContract) -> TriageResult:
    """对采集集逐条打分，返回 kept/dropped + scores。"""
    result = TriageResult()
    oos = contract.out_of_scope_keywords
    entities = contract.entity_terms

    for it in items:
        low = it.text.lower()

        # 阶段一：out_of_scope 关键词命中即弃。
        # 只匹配**标题**，不匹配摘要——摘要的动机句（"reduce economic losses"、
        # 跨域对比提到 inflation 等）会让规则误杀真域论文（nowcasting live 实测 7/7 误杀）。
        # 规则阶段必须高精度、recall-safe；语义级 out-of-scope 交 stage-2 LLM-judge。
        hit = _first_hit(it.headline.lower(), oos)
        if hit is not None:
            reason = f"out_of_scope keyword: {hit}"
            result.scores[it.id] = RelevanceScore(
                item_id=it.id, relevant=False, score=0.0, reason=reason,
                matched=(hit,), stage="rule",
            )
            result.dropped.append((it, reason))
            continue

        # 阶段二接缝：此处应交 LLM-judge 细判。当前直通保留（stage-1 keep）。
        ent = _first_hit(low, entities)
        matched = (ent,) if ent else ()
        result.scores[it.id] = RelevanceScore(
            item_id=it.id, relevant=True,
            score=0.6 if ent else 0.5,
            reason="no out_of_scope hit (stage-1 keep; stage-2 LLM pending)",
            matched=matched, stage="rule",
        )
        result.kept.append(it)

    return result
