"""Tests for the Semantic Scholar open-access PDF fallback in fetch_pdf_bytes."""

from unittest.mock import patch

import httpx
import pytest

from isbe.topics._shared.semantic_scholar import open_access_pdf_url
from isbe.topics.nowcasting.collectors import arxiv as arxiv_mod

PDF = b"%PDF-1.4 fake body"


def _resp(code: int, content: bytes = b"") -> httpx.Response:
    return httpx.Response(code, content=content, request=httpx.Request("GET", "http://x/"))


def test_open_access_pdf_url_parses_link():
    def fake_get(url):
        assert "arXiv:2604.11111" in url and "openAccessPdf" in url
        return {"openAccessPdf": {"url": "https://cdn.example/oa.pdf", "status": "GREEN"}}

    assert open_access_pdf_url("2604.11111", get_fn=fake_get) == "https://cdn.example/oa.pdf"


def test_s2_base_url_env_override(monkeypatch):
    from isbe.topics._shared.semantic_scholar import _s2_paper_url, _s2_search_url

    monkeypatch.setenv("S2_BASE_URL", "https://gw.example.com/k9/s2/")
    assert _s2_paper_url() == "https://gw.example.com/k9/s2/graph/v1/paper"
    assert _s2_search_url() == "https://gw.example.com/k9/s2/graph/v1/paper/search"


def test_arxiv_api_base_url_env_override(monkeypatch):
    from isbe.topics._shared.arxiv import _arxiv_url

    monkeypatch.setenv("ARXIV_API_BASE_URL", "https://gw.example.com/k9/export-arxiv")
    url = _arxiv_url(["cs.LG"], ["nowcasting"], 10)
    assert url.startswith("https://gw.example.com/k9/export-arxiv/api/query?")


def test_open_access_pdf_url_none_when_absent():
    assert open_access_pdf_url("2604.11111", get_fn=lambda u: {"openAccessPdf": None}) is None
    assert open_access_pdf_url("2604.11111", get_fn=lambda u: None) is None


def test_fallback_serves_pdf_when_mirrors_fail():
    def fake_httpx_get(url, **kwargs):
        if "arxiv.org" in url:
            raise httpx.ConnectError("stalled")
        return _resp(200, PDF)

    with patch.object(arxiv_mod.httpx, "get", side_effect=fake_httpx_get):
        with patch.object(
            arxiv_mod, "open_access_pdf_url", return_value="https://cdn.example/oa.pdf"
        ):
            body = arxiv_mod.fetch_pdf_bytes("2604.11111", max_retries=0, timeout=1.0)
    assert body == PDF


def test_no_oa_link_raises_original_error():
    with patch.object(arxiv_mod.httpx, "get", side_effect=httpx.ConnectError("stalled")):
        with patch.object(arxiv_mod, "open_access_pdf_url", return_value=None):
            with pytest.raises(httpx.ConnectError, match="stalled"):
                arxiv_mod.fetch_pdf_bytes("2604.11111", max_retries=0, timeout=1.0)


def test_non_pdf_oa_content_raises_original_error():
    def fake_httpx_get(url, **kwargs):
        if "arxiv.org" in url:
            raise httpx.ConnectError("stalled")
        return _resp(200, b"<html>landing page</html>")

    with patch.object(arxiv_mod.httpx, "get", side_effect=fake_httpx_get):
        with patch.object(
            arxiv_mod, "open_access_pdf_url", return_value="https://cdn.example/oa"
        ):
            with pytest.raises(httpx.ConnectError, match="stalled"):
                arxiv_mod.fetch_pdf_bytes("2604.11111", max_retries=0, timeout=1.0)
