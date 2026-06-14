"""本地 arxiv 源 adapter 离线测试（纯函数，无 DB）。"""

from __future__ import annotations

from isbe.topics._shared.local_arxiv import _net_query, _phrase, _row_to_paper
from isbe.triage.contract import RetrievalContract


def test_phrase_sanitizes_special_chars_to_quoted_phrase():
    assert _phrase("text-to-video") == '"text to video"'
    assert _phrase("precipitation forecast") == '"precipitation forecast"'
    assert _phrase("S2S/postproc") == '"S2S postproc"'


def test_net_query_ors_dedup_domain_vocab():
    c = RetrievalContract(
        intent="x", queries=["radar echo extrapolation"],
        entity_terms=["radar", "precipitation"], require_any=["radar", "rainfall"],
    )
    net = _net_query(c)
    # OR 连接、去重（radar 只出现一次）
    assert " OR " in net
    assert net.count('"radar"') == 1
    assert '"rainfall"' in net and '"precipitation"' in net


def test_row_to_paper_maps_fields():
    p = _row_to_paper(("2606.01234", "A Title", "An abstract", "2026-06-12", 7), "video generation")
    assert p.id == "2606.01234" and p.arxiv_id == "2606.01234"
    assert p.title == "A Title" and p.abstract == "An abstract"
    assert p.published_at == "2026-06-12"
    assert p.url == "https://arxiv.org/abs/2606.01234"
    assert p.citation_count is None  # hf_upvotes 不混入 citation
    assert p.query_hit == "video generation"


def test_row_to_paper_empty_abstract_to_none():
    p = _row_to_paper(("id1", "T", "", "2026-06-01", None), "q")
    assert p.abstract is None
