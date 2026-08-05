"""Tests for the metrail-enrich flow — DB, service, and run-recording all stubbed."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from isbe.topics._shared import metrail_enrich as flow_mod
from isbe.topics._shared.metrail import MetrailTimeout
from isbe.topics.config import TopicConfig
from isbe.topics.nowcasting.facts import Paper


def _paper(arxiv_id: str, pdf_uri: str | None) -> Paper:
    return Paper(
        arxiv_id=arxiv_id,
        title=f"paper {arxiv_id}",
        authors=["A"],
        abstract="abs",
        primary_category="cs.LG",
        submitted_at=datetime(2026, 7, 1, tzinfo=UTC),
        updated_at=datetime(2026, 7, 1, tzinfo=UTC),
        pdf_uri=pdf_uri,
        source_url=f"https://arxiv.org/abs/{arxiv_id}",
    )


def _cfg(metrail_block: dict | None) -> TopicConfig:
    raw: dict = {
        "id": "nowcasting",
        "label": "t",
        "cadence": "weekly",
        "arxiv": {"categories": ["cs.LG"], "include_keywords": ["paper"]},
    }
    if metrail_block is not None:
        raw["metrail"] = metrail_block
    return TopicConfig.model_validate(raw)


def _fake_session(papers: list[Paper]) -> MagicMock:
    s = MagicMock()
    s.__enter__ = MagicMock(return_value=s)
    s.__exit__ = MagicMock(return_value=False)
    s.scalars.return_value.all.return_value = papers
    return s


@pytest.fixture(autouse=True)
def _no_run_persistence():
    with patch("isbe.observability.runs._persist_run"):
        with patch.object(flow_mod, "_backfill_assets", return_value=(0, 0, 0)):
            yield


@pytest.fixture()
def mirror(tmp_path, monkeypatch):
    monkeypatch.setenv("ISBE_PAPERS_MIRROR", str(tmp_path))
    return tmp_path


def test_noop_when_no_base_url(monkeypatch, mirror):
    monkeypatch.delenv("METRAIL_API_URL", raising=False)
    with patch.object(flow_mod, "load_topic_config_typed", return_value=_cfg(None)):
        with patch.object(flow_mod, "_extract") as extract:
            assert flow_mod.metrail_enrich("nowcasting") == 0
    extract.assert_not_called()


def test_noop_when_disabled_in_yaml(monkeypatch, mirror):
    monkeypatch.setenv("METRAIL_API_URL", "http://metrail.lan:8000")
    cfg = _cfg({"enabled": False})
    with patch.object(flow_mod, "load_topic_config_typed", return_value=cfg):
        with patch.object(flow_mod, "_extract") as extract:
            assert flow_mod.metrail_enrich("nowcasting") == 0
    extract.assert_not_called()


def test_enriches_and_skips_missing_local_pdf(monkeypatch, mirror):
    monkeypatch.setenv("METRAIL_API_URL", "http://metrail.lan:8000")
    have = _paper("2604.11111", "minio://papers-nowcasting/2026-W31/2604.11111.pdf")
    missing = _paper("2604.22222", "minio://papers-nowcasting/2026-W31/2604.22222.pdf")
    pdf = mirror / "nowcasting" / "2026-W31" / "2604.11111.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF fake")

    session = _fake_session([have, missing])
    with patch.object(flow_mod, "load_topic_config_typed", return_value=_cfg({})):
        with patch.object(flow_mod, "make_session_factory", return_value=lambda: session):
            with (
                patch.object(flow_mod, "_extract", return_value=("## corpus", "d1")),
                patch.object(flow_mod, "_save_assets", return_value=(False, 0)),
                patch.object(flow_mod, "_fetch_pdf_from_minio", return_value=False),
            ):
                assert flow_mod.metrail_enrich("nowcasting") == 1

    assert have.fulltext_uri == "nowcasting/2026-W31/2604.11111.metrail.md"
    assert (mirror / have.fulltext_uri).read_text(encoding="utf-8") == "## corpus"
    assert missing.fulltext_uri is None


def test_minio_fallback_recovers_missing_local_pdf(monkeypatch, mirror):
    """PDF absent from the mirror but present in MinIO (e.g. downloaded by the
    proxy-equipped dev machine) → fetched into the mirror and enriched."""
    monkeypatch.setenv("METRAIL_API_URL", "http://metrail.lan:8000")
    p = _paper("2604.88888", "minio://papers-nowcasting/2026-W31/2604.88888.pdf")

    def fake_minio_fetch(pdf_uri, local_pdf):
        local_pdf.parent.mkdir(parents=True, exist_ok=True)
        local_pdf.write_bytes(b"%PDF from minio")
        return True

    session = _fake_session([p])
    with patch.object(flow_mod, "load_topic_config_typed", return_value=_cfg({})):
        with patch.object(flow_mod, "make_session_factory", return_value=lambda: session):
            with (
                patch.object(flow_mod, "_extract", return_value=("## corpus", "d1")),
                patch.object(flow_mod, "_save_assets", return_value=(False, 0)),
                patch.object(flow_mod, "_fetch_pdf_from_minio", side_effect=fake_minio_fetch),
            ):
                assert flow_mod.metrail_enrich("nowcasting") == 1

    assert p.fulltext_uri == "nowcasting/2026-W31/2604.88888.metrail.md"
    assert (mirror / "nowcasting" / "2026-W31" / "2604.88888.pdf").exists()


def test_timeout_on_one_paper_does_not_block_others(monkeypatch, mirror):
    monkeypatch.setenv("METRAIL_API_URL", "http://metrail.lan:8000")
    p1 = _paper("2604.33333", "minio://papers-nowcasting/2026-W31/2604.33333.pdf")
    p2 = _paper("2604.44444", "minio://papers-nowcasting/2026-W31/2604.44444.pdf")
    for p in (p1, p2):
        f = mirror / "nowcasting" / "2026-W31" / f"{p.arxiv_id}.pdf"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"%PDF fake")

    def extract(path, **kwargs):
        if "33333" in path.name:
            raise MetrailTimeout("stuck")
        return "## corpus", "d1"

    session = _fake_session([p1, p2])
    with patch.object(flow_mod, "load_topic_config_typed", return_value=_cfg({})):
        with patch.object(flow_mod, "make_session_factory", return_value=lambda: session):
            with (
                patch.object(flow_mod, "_extract", side_effect=extract),
                patch.object(flow_mod, "_save_assets", return_value=(False, 0)),
            ):
                assert flow_mod.metrail_enrich("nowcasting") == 1

    assert p1.fulltext_uri is None  # retried next run
    assert p2.fulltext_uri == "nowcasting/2026-W31/2604.44444.metrail.md"


def test_timeout_strikes_lead_to_blacklist(monkeypatch, mirror):
    """3 timeouts → .metrail.skip sidecar reaches the cap → paper no longer
    submitted (a stuck PDF must not clog metrail's worker pool every run)."""
    monkeypatch.setenv("METRAIL_API_URL", "http://metrail.lan:8000")
    p = _paper("2604.99999", "minio://papers-nowcasting/2026-W31/2604.99999.pdf")
    pdf = mirror / "nowcasting" / "2026-W31" / "2604.99999.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF fake")

    calls = {"n": 0}

    def extract(path, **kwargs):
        calls["n"] += 1
        raise MetrailTimeout("stuck")

    session_factory = lambda: _fake_session([p])  # noqa: E731
    with patch.object(flow_mod, "load_topic_config_typed", return_value=_cfg({})):
        with patch.object(flow_mod, "make_session_factory", return_value=session_factory):
            with (
                patch.object(flow_mod, "_extract", side_effect=extract),
                patch.object(flow_mod, "_save_assets", return_value=(False, 0)),
            ):
                for _ in range(4):  # 3 strikes + 1 blacklisted run
                    assert flow_mod.metrail_enrich("nowcasting") == 0

    assert calls["n"] == 3  # 4th run never submitted
    assert pdf.with_suffix(".metrail.skip").read_text() == "3"


def test_figure_counter_in_payload(monkeypatch, mirror):
    monkeypatch.setenv("METRAIL_API_URL", "http://metrail.lan:8000")
    p = _paper("2604.55555", "minio://papers-nowcasting/2026-W31/2604.55555.pdf")
    f = mirror / "nowcasting" / "2026-W31" / "2604.55555.pdf"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"%PDF fake")

    session = _fake_session([p])
    with patch.object(flow_mod, "load_topic_config_typed", return_value=_cfg({})):
        with patch.object(flow_mod, "make_session_factory", return_value=lambda: session):
            with (
                patch.object(flow_mod, "_extract", return_value=("## corpus", "d1")),
                patch.object(flow_mod, "_save_assets", return_value=(True, 1)) as fig,
            ):
                assert flow_mod.metrail_enrich("nowcasting") == 1
    fig.assert_called_once()


def test_save_framework_figure_prefers_caption_match(mirror):
    pdf = mirror / "2604.66666.pdf"
    pdf.write_bytes(b"%PDF fake")
    atoms = [
        {
            "id": "f0",
            "text": "Figure 2. Results on SEVIR",
            "image_url": "/api/documents/d/assets/f0.png",
        },
        {
            "id": "f1",
            "text": "Figure 1. Overall architecture of DiffCast",
            "image_url": "/api/documents/d/assets/f1.png",
        },
    ]
    downloaded: list[str] = []

    def fake_download(url, **kw):
        downloaded.append(url)
        return b"\x89PNG fake bytes"

    with (
        patch.object(flow_mod, "fetch_figure_atoms", return_value=atoms),
        patch.object(flow_mod, "fetch_atoms", return_value=[]),
        patch.object(flow_mod, "download_asset", side_effect=fake_download),
    ):
        fig_saved, n_tables = flow_mod._save_assets("d", pdf, base_url="http://x")
    assert fig_saved is True and n_tables == 0

    assert downloaded == ["/api/documents/d/assets/f1.png"]  # architecture wins
    assert pdf.with_suffix(".metrail.fig.png").read_bytes() == b"\x89PNG fake bytes"
    import json

    meta = json.loads(pdf.with_suffix(".metrail.assets.json").read_text(encoding="utf-8"))
    assert "architecture" in meta["figure"]["caption"]


def test_save_assets_skips_oversized_figure(mirror):
    pdf = mirror / "2604.77777.pdf"
    pdf.write_bytes(b"%PDF fake")
    atoms = [{"id": "f0", "text": "architecture", "image_url": "/a.png"}]
    with (
        patch.object(flow_mod, "fetch_figure_atoms", return_value=atoms),
        patch.object(flow_mod, "fetch_atoms", return_value=[]),
        patch.object(flow_mod, "download_asset", return_value=b"x" * 600_000),
    ):
        fig_saved, n_tables = flow_mod._save_assets("d", pdf, base_url="http://x")
    assert fig_saved is False and n_tables == 0
    assert not pdf.with_suffix(".metrail.fig.png").exists()


def test_save_assets_picks_core_tables(mirror):
    """Result/ablation tables win over dense-but-uncaptioned ones; markdown is
    kept verbatim; cap at 2 tables."""
    pdf = mirror / "2604.12321.pdf"
    pdf.write_bytes(b"%PDF fake")
    tables = [
        {"kind": "table", "page": 3, "text": "| a | b |\n| 1 | 2 |",  # too few numbers
         "metadata": {}},
        {"kind": "table", "page": 5,
         "text": "| model | CSI | POD |\n| DGMR | 0.41 | 0.55 |\n| ours | 0.47 | 0.61 |",
         "metadata": {"caption": "Table 2: Comparison with state-of-the-art"}},
        {"kind": "table", "page": 6,
         "text": "| variant | CSI |\n| w/o wavelet | 0.43 |\n| w/o flow | 0.44 |\n| full | 0.47 |",
         "metadata": {"caption": "Table 3: Ablation study"}},
        {"kind": "table", "page": 9,
         "text": "| id | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |",
         "metadata": {"caption": "Table A1: hyperparameters"}},
    ]
    with (
        patch.object(flow_mod, "fetch_figure_atoms", return_value=[]),
        patch.object(flow_mod, "fetch_atoms", return_value=tables),
        patch.object(flow_mod, "download_asset", return_value=b""),
    ):
        fig_saved, n_tables = flow_mod._save_assets("d", pdf, base_url="http://x")
    assert fig_saved is False and n_tables == 2

    import json

    meta = json.loads(pdf.with_suffix(".metrail.assets.json").read_text(encoding="utf-8"))
    captions = [t["caption"] for t in meta["tables"]]
    assert any("Comparison" in c for c in captions)
    assert any("Ablation" in c for c in captions)
    assert all("hyperparameters" not in c for c in captions)
    assert "| DGMR | 0.41 | 0.55 |" in meta["tables"][0]["markdown"] or \
           "| DGMR | 0.41 | 0.55 |" in meta["tables"][1]["markdown"]


def test_topic_without_keywords_refuses_unscoped_enrich(monkeypatch, mirror):
    """No arxiv include_keywords → the select would span every topic's papers;
    the flow must refuse instead."""
    monkeypatch.setenv("METRAIL_API_URL", "http://metrail.lan:8000")
    cfg = TopicConfig.model_validate({"id": "x", "label": "x", "cadence": "weekly"})
    with patch.object(flow_mod, "load_topic_config_typed", return_value=cfg):
        with patch.object(flow_mod, "_extract") as extract:
            assert flow_mod.metrail_enrich("x") == 0
    extract.assert_not_called()


def _bf_session(papers):
    s = MagicMock()
    s.scalars.return_value.all.return_value = papers
    return s


def test_backfill_extracts_assets_for_pre_feature_fulltext(mirror):
    """A paper with fulltext but no assets sidecar (enriched before the
    figure/table features) gets assets on the next run."""
    p = _paper("2607.25148", "minio://papers-nowcasting/2026-W31/2607.25148.pdf")
    p.fulltext_uri = "nowcasting/2026-W31/2607.25148.metrail.md"
    pdf = mirror / "nowcasting" / "2026-W31" / "2607.25148.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF fake")

    with (
        patch.object(flow_mod, "submit_pdf", return_value="d9"),
        patch.object(flow_mod, "wait_until_done", return_value={"state": "done"}),
        patch.object(flow_mod, "_save_assets", return_value=(True, 2)) as save,
    ):
        figures, tables, backfilled = flow_mod._backfill_assets(
            _bf_session([p]), None, base_url="http://x", backend="pdfplumber",
            ocr=False, poll_timeout_s=60,
        )
    assert (figures, tables, backfilled) == (1, 2, 1)
    save.assert_called_once()


def test_backfill_marks_assetless_papers_and_skips_done(mirror):
    p = _paper("2607.25149", "minio://papers-nowcasting/2026-W31/2607.25149.pdf")
    p.fulltext_uri = "nowcasting/2026-W31/2607.25149.metrail.md"
    pdf = mirror / "nowcasting" / "2026-W31" / "2607.25149.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF fake")

    with (
        patch.object(flow_mod, "submit_pdf", return_value="d9") as submit,
        patch.object(flow_mod, "wait_until_done", return_value={"state": "done"}),
        patch.object(flow_mod, "_save_assets", return_value=(False, 0)),
    ):
        _, _, backfilled = flow_mod._backfill_assets(
            _bf_session([p]), None, base_url="http://x", backend="pdfplumber",
            ocr=False, poll_timeout_s=60,
        )
        assert backfilled == 1
        assert pdf.with_suffix(".metrail.noassets").exists()
        # second run: marker present → not resubmitted
        _, _, backfilled2 = flow_mod._backfill_assets(
            _bf_session([p]), None, base_url="http://x", backend="pdfplumber",
            ocr=False, poll_timeout_s=60,
        )
    assert backfilled2 == 0
    assert submit.call_count == 1


def test_dispatch_resolves_metrail_enrich():
    from isbe.topics.dispatch import resolve_flow

    flow, params = resolve_flow("nowcasting", "metrail_enrich")
    assert flow.name == "metrail-enrich"
    assert params == {"topic_id": "nowcasting"}
