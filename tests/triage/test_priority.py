"""显示优先级分层测试（规则驱动，无 LLM）。

验：命中 secondary_terms → 次级；纯核心词 → 核心。in_scope 全保留，只是分层。
"""

from __future__ import annotations

from isbe.triage.contract import RetrievalContract
from isbe.triage.models import Item
from isbe.triage.priority import tier_of

CONTRACT = RetrievalContract(
    intent="降水临近预报",
    secondary_terms=["downscaling", "satellite", "flood", "QPE"],
)


def test_core_vs_secondary_split():
    items = [
        Item(id="c1", source="s", headline="Radar echo extrapolation nowcasting with diffusion"),
        Item(id="c2", source="s", headline="DGMR skillful precipitation nowcasting"),
        Item(id="s1", source="s", headline="Global precipitation downscaling network"),
        Item(id="s2", source="s", headline="Multi-satellite precipitation estimation"),
        Item(id="s3", source="s", headline="Continuous flood nowcasting in South Asia"),
    ]
    tiers = tier_of(items, CONTRACT)
    assert tiers["c1"] == "core" and tiers["c2"] == "core"
    assert tiers["s1"] == "secondary" and tiers["s2"] == "secondary" and tiers["s3"] == "secondary"


def test_secondary_term_in_summary_also_demotes():
    it = Item(id="x", source="s", headline="A new nowcasting model",
              summary="...evaluated on satellite precipitation products...")
    assert tier_of([it], CONTRACT)["x"] == "secondary"


def test_empty_secondary_terms_all_core():
    c = RetrievalContract(intent="x")  # 无 secondary_terms
    it = Item(id="a", source="s", headline="anything")
    assert tier_of([it], c)["a"] == "core"
