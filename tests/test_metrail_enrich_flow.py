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
    raw: dict = {"id": "nowcasting", "label": "t", "cadence": "weekly"}
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
                patch.object(flow_mod, "_save_framework_figure", return_value=False),
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
                patch.object(flow_mod, "_save_framework_figure", return_value=False),
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
                patch.object(flow_mod, "_save_framework_figure", return_value=False),
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
                patch.object(flow_mod, "_save_framework_figure", return_value=False),
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
                patch.object(flow_mod, "_save_framework_figure", return_value=True) as fig,
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
        patch.object(flow_mod, "download_asset", side_effect=fake_download),
    ):
        assert flow_mod._save_framework_figure("d", pdf, base_url="http://x") is True

    assert downloaded == ["/api/documents/d/assets/f1.png"]  # architecture wins
    assert pdf.with_suffix(".metrail.fig.png").read_bytes() == b"\x89PNG fake bytes"
    import json

    meta = json.loads(pdf.with_suffix(".metrail.assets.json").read_text(encoding="utf-8"))
    assert "architecture" in meta["caption"]


def test_save_framework_figure_skips_oversized(mirror):
    pdf = mirror / "2604.77777.pdf"
    pdf.write_bytes(b"%PDF fake")
    atoms = [{"id": "f0", "text": "architecture", "image_url": "/a.png"}]
    with (
        patch.object(flow_mod, "fetch_figure_atoms", return_value=atoms),
        patch.object(flow_mod, "download_asset", return_value=b"x" * 600_000),
    ):
        assert flow_mod._save_framework_figure("d", pdf, base_url="http://x") is False
    assert not pdf.with_suffix(".metrail.fig.png").exists()


def test_dispatch_resolves_metrail_enrich():
    from isbe.topics.dispatch import resolve_flow

    flow, params = resolve_flow("nowcasting", "metrail_enrich")
    assert flow.name == "metrail-enrich"
    assert params == {"topic_id": "nowcasting"}
