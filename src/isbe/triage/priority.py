"""显示/分析优先级分层（规则驱动，透明，无 LLM）。

用户要"都算，但有显示优先级"：in_scope 全部保留，按是否命中契约 secondary_terms
分成 core（核心，置顶）/ secondary（相关次级，靠后）两层。

刻意用透明规则而非 LLM-judge——用户对 judge 的留弃裁决不信任；分层这种"软排序"
要让用户一眼看懂、能直接改契约调整，judge 不掺和。
"""

from __future__ import annotations

from isbe.triage.contract import RetrievalContract
from isbe.triage.models import Item

CORE = "core"
SECONDARY = "secondary"


def tier_of(items: list[Item], contract: RetrievalContract) -> dict[str, str]:
    """{item_id: 'core' | 'secondary'}。命中 secondary_terms → secondary，否则 core。"""
    sec = [t.lower() for t in contract.secondary_terms]
    out: dict[str, str] = {}
    for it in items:
        low = it.text.lower()
        out[it.id] = SECONDARY if any(t in low for t in sec) else CORE
    return out
