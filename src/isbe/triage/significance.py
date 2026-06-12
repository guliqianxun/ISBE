"""RC5 显著性排序 — 纯元数据，零 LLM。

把"这堆相关论文里哪几篇最重要"操作化：citation 分位 + 引用速度 cite/age + 锚点。
让 digest 从"31 篇平铺"变成"必读置顶 + 其余折叠"——直接回应"ask-an-AI 更好"的
那个症结（前沿模型隐式按重要性排序，本层此前零排序）。

设计要点：
- **引用速度 cite/age** 救新论文——一篇上周的拐点工作 citation 还很低，纯总量会误杀；
  velocity 用论文年龄归一。池跨度越大，velocity 与原始 citation 分歧越明显。
- **set-relative 分位**：在本期入选集内算分位（统计学上自然），仍是纯函数（集合进、分数出）。
- **reference_date 显式传入**：保证可复现（不依赖 wall-clock）。
- citation 缺失（RSS 源）→ routine、score 0，不报显著性。
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date

from isbe.triage.models import Item

# tier 阈值（set-relative 分位）
_MUST_READ_P = 0.90
_NOTABLE_P = 0.50


@dataclass(frozen=True)
class SignificanceScore:
    item_id: str
    score: float          # 0..1，set-relative
    tier: str             # must-read | notable | routine
    citation_count: int | None
    velocity: float | None  # citations / 30d
    is_anchor: bool
    reasons: tuple[str, ...] = ()


def _pct_rank(value: float, sorted_vals: list[float]) -> float:
    """value 在 sorted_vals 中的分位 = (<=value 的个数) / n。"""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    return bisect.bisect_right(sorted_vals, value) / n


def rank_significance(
    items: list[Item],
    reference_date: date,
    anchor_ids: frozenset[str] = frozenset(),
) -> dict[str, SignificanceScore]:
    """对入选集逐条打显著性分。返回 {item_id: SignificanceScore}。"""
    cited = [it for it in items if it.citation_count is not None]
    cite_vals = sorted(float(it.citation_count) for it in cited)  # type: ignore[arg-type]

    velocity: dict[str, float] = {}
    for it in cited:
        if it.published_at is not None:
            age_days = max((reference_date - it.published_at.date()).days, 1)
            velocity[it.id] = it.citation_count / age_days * 30.0  # type: ignore[operator]
    vel_vals = sorted(velocity.values())

    scores: dict[str, SignificanceScore] = {}
    for it in items:
        is_anchor = it.id in anchor_ids
        if it.citation_count is None:
            scores[it.id] = SignificanceScore(
                it.id, 1.0 if is_anchor else 0.0,
                "must-read" if is_anchor else "routine",
                None, None, is_anchor,
                ("must_not_miss anchor",) if is_anchor else ("no citation metadata",),
            )
            continue

        cpct = _pct_rank(float(it.citation_count), cite_vals)
        v = velocity.get(it.id)
        vpct = _pct_rank(v, vel_vals) if v is not None else 0.0
        reasons = [f"cite={it.citation_count} (p{int(cpct * 100)})"]
        if v is not None:
            reasons.append(f"velocity={v:.1f}/30d (p{int(vpct * 100)})")

        if is_anchor:
            tier, score = "must-read", 1.0
            reasons.append("must_not_miss anchor")
        elif cpct >= _MUST_READ_P or vpct >= _MUST_READ_P:
            tier, score = "must-read", max(cpct, vpct)
        elif cpct >= _NOTABLE_P or vpct >= _NOTABLE_P:
            tier, score = "notable", 0.5 * cpct + 0.5 * vpct
        else:
            tier, score = "routine", 0.5 * cpct + 0.5 * vpct

        scores[it.id] = SignificanceScore(
            it.id, round(score, 3), tier, it.citation_count,
            round(v, 2) if v is not None else None, is_anchor, tuple(reasons),
        )
    return scores


def ranked(items: list[Item], scores: dict[str, SignificanceScore]) -> list[Item]:
    """按显著性分降序排列 items（digest 用：必读在前）。"""
    def _key(it: Item) -> float:
        return scores[it.id].score if it.id in scores else 0.0
    return sorted(items, key=_key, reverse=True)
