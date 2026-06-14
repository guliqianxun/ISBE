"""本地 arxiv 源 adapter 离线测试（纯函数，无 DB）。"""

from __future__ import annotations

from isbe.topics._shared.local_arxiv import _fts_query, _row_to_paper


def test_fts_query_sanitizes_special_chars_to_anded_tokens():
    assert _fts_query("text-to-video") == '"text" "to" "video"'
    assert _fts_query("radar echo extrapolation") == '"radar" "echo" "extrapolation"'
    assert _fts_query("S2S/postproc") == '"S2S" "postproc"'


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
