"""Step B 接缝测试：apply_triage 直通安全网 + 契约缺省检测 + 字段映射。

不碰 DB/LLM——用 duck-typed 假行验 digester_utils 的纯接缝逻辑。
核心保证：契约缺省 → 直通全留（产线 6 域行为逐字节不变）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from isbe.topics._shared.digester_utils import apply_triage, article_to_item, paper_to_item
from isbe.triage import RetrievalContract
from isbe.triage.contract import contract_from_config


@dataclass
class FakePaper:
    arxiv_id: str
    title: str
    abstract: str
    source_url: str
    submitted_at: datetime


@dataclass
class FakeArticle:
    id: str
    source: str
    headline: str
    summary: str
    url: str
    published_at: datetime


def _paper(aid, title):
    return FakePaper(
        aid, title, "abstract body", f"http://x/{aid}", datetime(2026, 6, 1, tzinfo=UTC)
    )


def test_contract_from_config_absent_returns_none():
    assert contract_from_config(None) is None
    assert contract_from_config({}) is None
    assert contract_from_config({"label": "x", "rss": {}}) is None  # 无 retrieval 块


def test_contract_from_config_present():
    c = contract_from_config({"retrieval": {"intent": "x", "out_of_scope_keywords": ["scooter"]}})
    assert isinstance(c, RetrievalContract)
    assert c.out_of_scope_keywords == ["scooter"]


def test_apply_triage_passthrough_when_no_contract():
    rows = [_paper("1", "Self-Forcing"), _paper("2", "RunawayEvil: Jailbreaking")]
    kept, result = apply_triage(rows, None, paper_to_item)
    assert kept == rows            # 原样、同对象、同序
    assert result is None          # 无 triage 痕迹


def test_apply_triage_filters_with_contract():
    contract = RetrievalContract(intent="video gen", out_of_scope_keywords=["jailbreak"])
    rows = [
        _paper("1", "Self-Forcing: autoregressive video"),
        _paper("2", "RunawayEvil: Jailbreaking I2V"),
    ]
    kept, result = apply_triage(rows, contract, paper_to_item)
    assert [p.arxiv_id for p in kept] == ["1"]
    assert result is not None and len(result.dropped) == 1


def test_converters_map_fields():
    p = _paper("2506.08009", "Self-Forcing")
    it = paper_to_item(p)
    assert it.id == "2506.08009" and it.headline == "Self-Forcing" and it.source == "arxiv"

    a = FakeArticle(
        "art1", "cycleworld", "New 450", "body", "http://x", datetime(2026, 6, 1, tzinfo=UTC)
    )
    ia = article_to_item(a)
    assert ia.id == "art1" and ia.headline == "New 450" and ia.source == "cycleworld"
