"""RC5 显著性排序测试。

真实 88 篇池验"里程碑置顶、垃圾下沉"；合成数据验引用速度救新论文 + 锚点强制必读。
不依赖人工 qrels（citation 是 S2 现成元数据），故现在即可绿。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from isbe.triage import rank_significance
from isbe.triage.models import Item

POOL = Path(__file__).parent.parent / "eval" / "video-generation" / "2026-W23" / "collection.jsonl"
REF = date(2026, 6, 8)  # 快照 W23，显式传入保证可复现


def _load_pool() -> tuple[list[Item], dict[str, dict]]:
    lines = [ln for ln in POOL.read_text(encoding="utf-8").splitlines() if ln.strip()]
    rows = [json.loads(ln) for ln in lines]
    items = [
        Item(
            id=r["id"], source=r["source"], headline=r["headline"], summary=r.get("summary"),
            published_at=(
                datetime.fromisoformat(r["published_at"]) if r.get("published_at") else None
            ),
            citation_count=r.get("citation_count"),
            fields_of_study=tuple(r.get("fields_of_study") or ()),
        )
        for r in rows
    ]
    return items, {r["id"]: r for r in rows}


def test_milestones_are_must_read_and_junk_is_routine():
    items, byid = _load_pool()
    scores = rank_significance(items, REF)

    must_read = {i for i, s in scores.items() if s.tier == "must-read"}
    # 真实里程碑（citation 顶部）必须进必读
    assert "2506.08009" in must_read   # Self-Forcing, cite=349
    assert "2503.03751" in must_read   # Gen3C, cite=246
    assert "2506.09113" in must_read   # Seedance, cite=204

    # 必读是小集合（top tier），不是又一份平铺
    assert len(must_read) <= 15, f"must-read 过多: {len(must_read)}"

    # cite=0 无 arxiv 垃圾沉到 routine
    junk = next(it for it in items if it.headline == "Prompt Guided Image to Video Resume")
    assert scores[junk.id].tier == "routine"


def test_velocity_rescues_recent_paper():
    """同 citation，新论文靠引用速度排得更前（救拐点）。"""
    recent = Item(id="recent", source="s", headline="new", citation_count=30,
                  published_at=datetime(2026, 5, 8))   # 1 个月
    old = Item(id="old", source="s", headline="old", citation_count=30,
               published_at=datetime(2024, 12, 8))     # ~18 个月
    scores = rank_significance([recent, old], REF)
    assert scores["recent"].velocity > scores["old"].velocity
    assert scores["recent"].score >= scores["old"].score


def test_anchor_forces_must_read_regardless_of_citation():
    low = Item(id="low", source="s", headline="low cite but pinned", citation_count=1,
               published_at=datetime(2026, 5, 8))
    scores = rank_significance([low], REF, anchor_ids=frozenset({"low"}))
    assert scores["low"].tier == "must-read"
    assert scores["low"].is_anchor
