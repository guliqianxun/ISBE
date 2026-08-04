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


def _mirror_with_figure(
    tmp_path, arxiv_id="1.1", png=b"\x89PNG fake", caption="Fig 1. architecture"
):
    import json

    d = tmp_path / "nowcasting" / "2026-W31"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{arxiv_id}.metrail.md").write_text("## corpus", encoding="utf-8")
    (d / f"{arxiv_id}.metrail.fig.png").write_bytes(png)
    (d / f"{arxiv_id}.metrail.assets.json").write_text(
        json.dumps({"caption": caption}), encoding="utf-8"
    )
    return f"nowcasting/2026-W31/{arxiv_id}.metrail.md"


def test_load_paper_assets_builds_template_shape(monkeypatch, tmp_path):
    from isbe.topics._shared.digester import _load_paper_assets

    monkeypatch.setenv("ISBE_PAPERS_MIRROR", str(tmp_path))
    uri = _mirror_with_figure(tmp_path)
    assets = _load_paper_assets([_paper("1.1", fulltext_uri=uri)])
    fig = assets["1.1"]["figure"]
    assert fig["caption"] == "Fig 1. architecture"
    assert fig["datauri"].startswith("data:image/png;base64,")


def test_load_paper_assets_includes_tables(monkeypatch, tmp_path):
    import json

    from isbe.topics._shared.digester import _load_paper_assets

    monkeypatch.setenv("ISBE_PAPERS_MIRROR", str(tmp_path))
    d = tmp_path / "nowcasting" / "2026-W32"
    d.mkdir(parents=True)
    (d / "4.4.metrail.md").write_text("## corpus", encoding="utf-8")
    (d / "4.4.metrail.assets.json").write_text(
        json.dumps(
            {
                "figure": {"caption": "Fig 1. framework"},
                "tables": [{"caption": "Table 2: Comparison", "markdown": "| m | 0.47 |"}],
            }
        ),
        encoding="utf-8",
    )
    (d / "4.4.metrail.fig.png").write_bytes(b"\x89PNG ok")
    assets = _load_paper_assets([_paper("4.4", fulltext_uri="nowcasting/2026-W32/4.4.metrail.md")])
    entry = assets["4.4"]
    assert entry["figure"]["caption"] == "Fig 1. framework"
    assert entry["tables"][0]["markdown"] == "| m | 0.47 |"


def test_load_paper_assets_tables_without_figure(monkeypatch, tmp_path):
    import json

    from isbe.topics._shared.digester import _load_paper_assets

    monkeypatch.setenv("ISBE_PAPERS_MIRROR", str(tmp_path))
    d = tmp_path / "nowcasting" / "2026-W32"
    d.mkdir(parents=True)
    (d / "5.5.metrail.md").write_text("## corpus", encoding="utf-8")
    (d / "5.5.metrail.assets.json").write_text(
        json.dumps({"tables": [{"caption": "Ablation", "markdown": "| x | 1 |"}]}),
        encoding="utf-8",
    )
    assets = _load_paper_assets([_paper("5.5", fulltext_uri="nowcasting/2026-W32/5.5.metrail.md")])
    assert "figure" not in assets["5.5"]
    assert assets["5.5"]["tables"][0]["caption"] == "Ablation"


def test_load_paper_assets_skips_missing_and_oversized(monkeypatch, tmp_path):
    from isbe.topics._shared.digester import _load_paper_assets

    monkeypatch.setenv("ISBE_PAPERS_MIRROR", str(tmp_path))
    no_fig = _paper("2.2", fulltext_uri="nowcasting/2026-W31/2.2.metrail.md")  # no files
    big_uri = _mirror_with_figure(tmp_path, arxiv_id="3.3", png=b"x" * 600_000)
    assets = _load_paper_assets([no_fig, _paper("3.3", fulltext_uri=big_uri)])
    assert assets == {}
