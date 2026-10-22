"""Figure saving and panel downloads on the arXiv HTML side."""

import hashlib
import os
from pathlib import Path

import httpx
import pytest

from isbe.core import http
from isbe.core.errors import describe
from isbe.pipeline.enrich import arxiv_html
from isbe.pipeline.enrich.arxiv_html import Figure
from isbe.pipeline.enrich.calls import EnrichError
from isbe.pipeline.enrich.figures import (
    MAX_HTML_PANEL,
    download_html_image,
    figure_rank,
    image_extension,
    save_html_figure,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"


def test_saves_complete_method_figure_groups_and_bounds_their_size(tmp_path: Path) -> None:
    data = tmp_path / "data"
    figures = [
        Figure(["overview"], "Figure 1: Overview"),
        Figure(["panel-a", "panel-b"], "Figure 2: Our framework (a) encoder, (b) decoder."),
    ]
    downloaded: list[str] = []

    def fake(url: str) -> tuple[str, bytes]:
        downloaded.append(url)
        return "png", url.encode()

    saved = save_html_figure(data, figures, fake)
    assert downloaded == ["panel-a", "panel-b"]
    assert saved["caption"] == figures[1].caption
    for file, expected in zip(saved["files"], downloaded, strict=True):
        assert file == f"figures/{hashlib.sha256(expected.encode()).hexdigest()}.png"
        assert (data / file).read_bytes() == expected.encode()
    mode = (data / saved["files"][0]).stat().st_mode & 0o777
    umask = os.umask(0)
    os.umask(umask)
    assert mode == 0o666 & ~umask, "as Rust's fs::write"

    failed = data / "failed"

    def second_missing(url: str) -> tuple[str, bytes]:
        if url.endswith("b"):
            raise EnrichError("missing second panel")
        return "png", b"\x01"

    with pytest.raises(EnrichError):
        save_html_figure(failed, figures, second_missing)
    assert not failed.exists(), "no partial group is written"
    oversized = [Figure(["a", "b", "c"], "Architecture")]
    with pytest.raises(EnrichError, match="exceeds 5 MiB"):
        save_html_figure(failed, oversized, lambda url: ("png", bytes(MAX_HTML_PANEL)))
    assert not failed.exists()

    def unreachable(url: str) -> tuple[str, bytes]:
        raise AssertionError(url)

    assert save_html_figure(failed, [], unreachable) == {}
    assert save_html_figure(failed, [Figure(["x"], " \n")], unreachable) == {}


def test_figure_rank_reads_the_caption_opening() -> None:
    assert figure_rank("Figure 2: Our 框架") == 0
    assert figure_rank("Figure 1: OVERVIEW of results") == 1
    assert figure_rank("x" * 80 + " framework") == 2


def test_refuses_mislabeled_images_and_external_downloads() -> None:
    assert image_extension("image/png; charset=binary", b"\x89PNG\r\n\x1a\n") == "png"
    assert image_extension("image/jpeg", b"\xff\xd8\xff") == "jpg"
    assert image_extension("image/webp", b"RIFF0000WEBP") == "webp"
    for kind, data in [("image/png", b"<script>bad</script>"), ("image/svg+xml", b"<svg></svg>"),
                       ("text/html", b"\x89PNG\r\n\x1a\n"), ("", PNG)]:
        with pytest.raises(EnrichError, match="not a supported PNG, JPEG or WebP"):
            image_extension(kind, data)

    def offline(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"no request expected: {request.url}")

    client = http.Http(1, transport=httpx.MockTransport(offline))
    for url in ["http://127.0.0.1/picture.png", "https://example.com/picture.png",
                "https://arxiv.org/html/picture.svg"]:
        with pytest.raises(EnrichError, match="unsupported figure URL"):
            download_html_image(client, url)


@pytest.mark.parametrize(("response", "message"), [
    (httpx.Response(302, headers={"Location": "https://evil.test/a.png"}),
     "figure download returned HTTP 302 Found"),
    (httpx.Response(404), "http status: 404"),
    (httpx.Response(200, headers={"Content-Type": "image/png"}, content=bytes(MAX_HTML_PANEL + 1)),
     f"the response body is larger than request limit: {MAX_HTML_PANEL}"),
    (httpx.Response(200, headers={"Content-Type": "text/html"}, content=PNG),
     "figure is not a supported PNG, JPEG or WebP image"),
])
def test_panel_downloads_fail_with_http_errors(response: httpx.Response, message: str) -> None:
    client = http.Http(5, transport=httpx.MockTransport(lambda request: response))
    with pytest.raises(EnrichError) as caught:
        download_html_image(client, "https://arxiv.org/html/2401.00001v2/x1.png")
    assert describe(caught.value) == message


def test_panel_download_returns_the_checked_image() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, headers={"Content-Type": "image/png"}, content=PNG)

    client = http.Http(5, transport=httpx.MockTransport(handler))
    assert download_html_image(client, "https://arxiv.org/html/2401.00001v2/x1.png#f") == ("png", PNG)
    assert seen == ["https://arxiv.org/html/2401.00001v2/x1.png"]


@pytest.mark.skipif(os.environ.get("ISBE_LIVE_ARXIV") != "1",
                    reason="downloads a public arXiv HTML paper and its figure; set ISBE_LIVE_ARXIV=1")
def test_downloads_public_arxiv_figure(tmp_path: Path) -> None:
    with http.Http(45) as client:
        paper = arxiv_html.fetch(client, "arxiv:2402.08954v1")
        assert paper.figures
        figure = save_html_figure(tmp_path, paper.figures, lambda url: download_html_image(client, url))
    assert figure["files"]
    for file in figure["files"]:
        assert (tmp_path / file).stat().st_size > 1000
