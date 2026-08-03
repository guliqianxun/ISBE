"""Tests for the enriched digester facts block (abstract + budgeted fulltext)."""

from types import SimpleNamespace

from isbe.topics._shared.digester import _build_facts_block, _load_fulltext


def _paper(arxiv_id: str, abstract: str = "摘要内容", fulltext_uri: str | None = None):
    return SimpleNamespace(
        arxiv_id=arxiv_id,
        title=f"paper {arxiv_id}",
        primary_category="cs.LG",
        source_url=f"https://arxiv.org/abs/{arxiv_id}",
        abstract=abstract,
        fulltext_uri=fulltext_uri,
    )


def test_abstract_included_by_default():
    block = _build_facts_block([_paper("1.1")], None)
    assert "摘要: 摘要内容" in block


def test_include_abstract_false_restores_title_only_shape():
    block = _build_facts_block([_paper("1.1")], None, include_abstract=False)
    assert "摘要:" not in block
    assert "- [1.1] paper 1.1 (cs.LG)" in block


def test_fulltext_excerpt_truncated_per_paper():
    block = _build_facts_block(
        [_paper("1.1")],
        None,
        fulltext_for=lambda p: "x" * 10_000,
        per_paper_chars=100,
    )
    assert "全文摘录: " + "x" * 100 in block
    assert "x" * 101 not in block


def test_total_budget_stops_fulltext_but_keeps_abstracts():
    papers = [_paper("1.1"), _paper("2.2"), _paper("3.3")]
    block = _build_facts_block(
        papers,
        None,
        fulltext_for=lambda p: "y" * 3000,
        per_paper_chars=3000,
        total_chars=5000,
    )
    # paper 1: 3000 chars; paper 2: remaining 2000; paper 3: budget exhausted
    assert block.count("全文摘录:") == 2
    assert block.count("摘要:") == 3


def test_row_without_fulltext_attr_does_not_crash():
    p = SimpleNamespace(
        arxiv_id="s2.1",
        title="s2 row",
        primary_category="cs.CV",
        source_url="https://example.org",
        abstract="abs",
    )
    block = _build_facts_block([p], None, fulltext_for=_load_fulltext)
    assert "s2 row" in block


def test_load_fulltext_missing_file_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("ISBE_PAPERS_MIRROR", str(tmp_path))
    assert _load_fulltext(_paper("1.1", fulltext_uri="nowhere/x.metrail.md")) is None


def test_load_fulltext_reads_mirror_relative_path(monkeypatch, tmp_path):
    monkeypatch.setenv("ISBE_PAPERS_MIRROR", str(tmp_path))
    md = tmp_path / "nowcasting" / "2026-W31" / "1.1.metrail.md"
    md.parent.mkdir(parents=True)
    md.write_text("## corpus", encoding="utf-8")
    p = _paper("1.1", fulltext_uri="nowcasting/2026-W31/1.1.metrail.md")
    assert _load_fulltext(p) == "## corpus"
