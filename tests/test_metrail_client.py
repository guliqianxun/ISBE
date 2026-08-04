"""Unit tests for the metrail-web HTTP client — zero LAN access, httpx patched."""

from unittest.mock import patch

import httpx
import pytest

from isbe import http_retry
from isbe.topics._shared import metrail
from isbe.topics._shared.metrail import (
    MetrailError,
    MetrailJobFailed,
    MetrailTimeout,
    extract_pdf_to_markdown,
    metrail_base_url,
    submit_pdf,
    wait_until_done,
)

BASE = "http://metrail.lan:8000"


def _resp(code: int, *, json_body=None, text: str = "") -> httpx.Response:
    req = httpx.Request("GET", f"{BASE}/api/documents")
    if json_body is not None:
        return httpx.Response(code, json=json_body, request=req)
    return httpx.Response(code, text=text, request=req)


@pytest.fixture()
def pdf(tmp_path):
    p = tmp_path / "2507.12345.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    return p


def test_base_url_resolution(monkeypatch):
    monkeypatch.delenv("METRAIL_API_URL", raising=False)
    assert metrail_base_url() is None
    assert metrail_base_url("http://x:8000/") == "http://x:8000"
    monkeypatch.setenv("METRAIL_API_URL", "http://env:8000/")
    assert metrail_base_url() == "http://env:8000"
    assert metrail_base_url("http://override:8000") == "http://override:8000"


def test_submit_pdf_happy_path(pdf):
    with patch.object(metrail.httpx, "post", return_value=_resp(202, json_body={"id": "d1"})):
        assert submit_pdf(pdf, base_url=BASE) == "d1"


def test_submit_pdf_422_raises_without_retry(pdf):
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        return _resp(422, text="only PDF files are supported")

    with patch.object(metrail.httpx, "post", side_effect=fake_post):
        with pytest.raises(MetrailError, match="rejected"):
            submit_pdf(pdf, base_url=BASE)
    assert calls["n"] == 1


def test_submit_pdf_retries_transient_503(pdf):
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        if calls["n"] < 2:
            return _resp(503)
        return _resp(202, json_body={"id": "d2"})

    with patch.object(metrail.httpx, "post", side_effect=fake_post):
        with patch.object(http_retry, "_sleep_between_attempts", lambda s: None):
            assert submit_pdf(pdf, base_url=BASE) == "d2"
    assert calls["n"] == 2


def test_wait_until_done_polls_to_done():
    states = iter(["queued", "processing", "done"])

    def fake_get(*a, **k):
        return _resp(200, json_body={"id": "d1", "state": next(states)})

    slept: list[float] = []
    with patch.object(metrail.httpx, "get", side_effect=fake_get):
        meta = wait_until_done("d1", base_url=BASE, sleep=slept.append)
    assert meta["state"] == "done"
    assert len(slept) == 2


def test_wait_until_done_error_state_raises():
    def fake_get(*a, **k):
        return _resp(200, json_body={"id": "d1", "state": "error", "error": "boom"})

    with patch.object(metrail.httpx, "get", side_effect=fake_get):
        with pytest.raises(MetrailJobFailed, match="boom"):
            wait_until_done("d1", base_url=BASE, sleep=lambda s: None)


def test_wait_until_done_deadline_raises_timeout():
    """A restart-orphaned job stays 'processing' forever — the hard deadline
    must convert that into MetrailTimeout instead of hanging."""

    def fake_get(*a, **k):
        return _resp(200, json_body={"id": "d1", "state": "processing"})

    with patch.object(metrail.httpx, "get", side_effect=fake_get):
        with pytest.raises(MetrailTimeout):
            wait_until_done("d1", base_url=BASE, timeout_s=0.0, sleep=lambda s: None)


def test_fetch_figure_atoms_returns_list():
    atoms = [
        {
            "id": "f0",
            "kind": "figure",
            "text": "Fig 1",
            "image_url": "/api/documents/d1/assets/f0.png",
        }
    ]

    def fake_get(url, **k):
        assert url.endswith("/atoms") and k.get("params", {}).get("kind") == "figure"
        return _resp(200, json_body=atoms)

    with patch.object(metrail.httpx, "get", side_effect=fake_get):
        assert metrail.fetch_figure_atoms("d1", base_url=BASE) == atoms


def test_download_asset_joins_relative_url():
    seen: list[str] = []

    def fake_get(url, **k):
        seen.append(url)
        req = httpx.Request("GET", url)
        return httpx.Response(200, content=b"\x89PNG", request=req)

    with patch.object(metrail.httpx, "get", side_effect=fake_get):
        body = metrail.download_asset("/api/documents/d1/assets/f0.png", base_url=BASE)
    assert body == b"\x89PNG"
    assert seen == [f"{BASE}/api/documents/d1/assets/f0.png"]


def test_extract_pdf_to_markdown_composes(pdf):
    def fake_get(url, **k):
        if url.endswith("/export"):
            return _resp(200, text="## [text] page 1\n\ncorpus body")
        return _resp(200, json_body={"id": "d1", "state": "done"})

    with patch.object(metrail.httpx, "post", return_value=_resp(202, json_body={"id": "d1"})):
        with patch.object(metrail.httpx, "get", side_effect=fake_get):
            md = extract_pdf_to_markdown(pdf, base_url=BASE)
    assert "corpus body" in md
