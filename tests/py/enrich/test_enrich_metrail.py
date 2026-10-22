"""Full text and figures through the metrail service: submission, polling, atoms and figure downloads."""

from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import pytest

from isbe.core import http
from isbe.core.errors import describe
from isbe.pipeline.enrich import metrail
from isbe.pipeline.enrich.calls import EnrichError
from isbe.pipeline.enrich.metrail import (
    Atom,
    body,
    finished,
    pick_figure,
    strip_overlap,
    submit,
)
from isbe.topics import Metrail

PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"


def atom(page: int, kind: str, text: str, metadata: Any) -> Atom:
    return Atom(page, kind, text, metadata, f"/img/{page}.png")


@pytest.mark.parametrize("upload", [False, True])
def test_pdf_submission_errors_identify_download_or_upload(serve: Any, upload: bool) -> None:
    seen: list[str] = []

    def handle(req: BaseHTTPRequestHandler) -> None:
        seen.append(req.command)
        if upload and len(seen) == 1:
            serve.reply(req, 200, "%PDF-1.4\nfixture")
        else:
            serve.reply(req, 400, "unavailable")

    base = serve(handle)
    with pytest.raises(EnrichError) as caught:
        submit(http.Http(2), base, Metrail(), base, "arxiv:1", {"path": "fixture.pdf"})
    assert str(caught.value) == ("submit PDF to metrail" if upload else "download paper PDF")
    assert describe(caught.value).endswith("http status: 400")
    assert seen == (["GET", "POST"] if upload else ["GET"])


@pytest.mark.parametrize("atoms", [False, True])
def test_metrail_result_errors_identify_status_or_atoms(serve: Any, atoms: bool) -> None:
    seen: list[str] = []

    def handle(req: BaseHTTPRequestHandler) -> None:
        seen.append(req.path)
        if atoms and len(seen) == 1:
            serve.reply(req, 200, '{"state":"done"}')
        else:
            serve.reply(req, 400, "unavailable")

    base = serve(handle)
    with pytest.raises(EnrichError) as caught:
        finished(http.Http(2), base, "document")
    assert str(caught.value) == (
        "fetch metrail document atoms" if atoms else "read metrail document status"
    )


def test_submission_uploads_the_form(serve: Any) -> None:
    uploads: list[tuple[str, str, bytes]] = []

    def handle(req: BaseHTTPRequestHandler) -> None:
        if req.command == "GET":
            assert req.path == "/api/file?path=a%2Fb.pdf"
            serve.reply(req, 200, b"%PDF-1.7 bytes")
            return
        length = int(req.headers["Content-Length"])
        uploads.append((req.path, req.headers["Content-Type"], req.rfile.read(length)))
        serve.reply(req, 200, '{"id": "doc-1", "state": "queued"}')

    base = serve(handle)
    cfg = Metrail(backend="docling", ocr=True, figure_backend="docling")
    assert submit(http.Http(2), base, cfg, base + "/", "arxiv:hep-th/9901001", {"path": "a/b.pdf"}) == "doc-1"
    b = metrail.BOUNDARY
    expected = (
        f'--{b}\r\nContent-Disposition: form-data; name="backend"\r\n\r\ndocling\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="ocr"\r\n\r\ntrue\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="figure_backend"\r\n\r\ndocling\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="hep-th_9901001.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n%PDF-1.7 bytes"
        f"\r\n--{b}--\r\n"
    ).encode()
    assert uploads == [("/api/documents", f"multipart/form-data; boundary={b}", expected)]


def test_submission_needs_a_pdf_source_and_a_pdf() -> None:
    with pytest.raises(EnrichError, match="no PDF to fetch"):
        submit(http.Http(2), "http://127.0.0.1:9", Metrail(), None, "rss:x", {"path": "x"})


def test_picks_the_method_figure() -> None:
    def fig(page: int, caption: str) -> Atom:
        return atom(page, "figure", caption, {"caption_detected": "yes"})

    atoms = [
        atom(0, "figure", "[Figure on page 1 of x]", {"caption_detected": "no"}),
        fig(1, "Figure 1: Motivation and overview. (a) Daily fields."),
        fig(3, "Figure 2: Overview of our two-step framework. The workflow cycles."),
        fig(7, "Figure 4: Spatial comparison of forecasts; our framework is sharper."),
    ]
    assert pick_figure(atoms).page == 3  # type: ignore[union-attr]
    assert pick_figure(atoms[:2]).page == 1  # type: ignore[union-attr]
    assert pick_figure(atoms[:1]) is None


def test_body_drops_chunk_overlap_and_keeps_tables() -> None:
    atoms = [
        atom(0, "text", "Title\nAbstract one.\nIntro starts here.", {}),
        atom(0, "text", "Intro starts here.\nIntro continues.", {}),
        atom(0, "figure", "Figure 1: Overview.", {}),
        atom(1, "text", "Intro continues.\nMethod.", {}),
        atom(1, "table", "| a | b |\n| --- | --- |\n| 1 | 2 |", {"caption": "Table 1:\n Results."}),
    ]
    assert body(atoms) == (
        "## Page 1\n\nTitle\nAbstract one.\nIntro starts here.\nIntro continues.\n\n"
        "## Page 2\n\nIntro continues.\nMethod.\n\n"
        "### Table 1: Results.\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n"
    )
    unpaged = [Atom(None, "text", " a ", None, None), Atom(None, "table", "", None, None),
               Atom(None, "table", "t", {"caption": 3}, None)]
    assert body(unpaged) == "a\n\n### Table\nt\n\n"


def test_overlap_ends_at_a_line_break() -> None:
    assert strip_overlap("x\nends here", "ends here\nnew") == "new"
    assert strip_overlap("a\nb\nc", "b\nc\nd") == "d"
    assert strip_overlap("", "a\nb") == "a\nb"
    # the repeated run must end at one of next's line breaks
    assert strip_overlap("ends here", "here and on\nx") == "here and on\nx"


def test_atoms_follow_the_struct_layout() -> None:
    parsed = metrail.atoms([{"kind": "text", "page": 2, "extra": 1}, {"kind": "figure", "metadata": None}])
    assert parsed == [Atom(2, "text", None, None, None), Atom(None, "figure", None, None, None)]
    for bad in [{"page": 1}, {"kind": 1}, {"kind": "t", "page": 1.0}, {"kind": "t", "text": 5}, "x"]:
        with pytest.raises(ValueError):
            metrail.atoms([bad])


def test_figure_urls_stay_on_metrail() -> None:
    base = "http://127.0.0.1:8000"
    assert metrail.figure_url(base, "/img/1.png") == "http://127.0.0.1:8000/img/1.png"
    # A backslash is a path separator to WHATWG; what is sent is that reading.
    assert metrail.figure_url(base, "\\@evil.test") == "http://127.0.0.1:8000/@evil.test"
    for path in ["@evil.test/x.png", ".evil.test/x.png", "x@evil.test/a"]:
        with pytest.raises(EnrichError, match="unsupported figure URL"):
            metrail.figure_url(base, path)


def _figure_server(serve: Any, content_type: str, data: bytes = PNG) -> str:
    def handle(req: BaseHTTPRequestHandler) -> None:
        assert req.path == "/img/3.png?w=960"
        serve.reply(req, 200, data, {"Content-Type": content_type})

    return str(serve(handle))


def test_metrail_image_content_type_with_parameters_is_saved(serve: Any, tmp_path: Path) -> None:
    atoms = [atom(3, "figure", "Figure 2:\n  Our  framework.", {"caption_detected": "yes"})]
    base = _figure_server(serve, "image/png; charset=binary")
    saved = metrail.save_figure(http.Http(2), base, tmp_path, "arxiv:2401.00001", atoms)
    assert saved == {"file": "figures/2401.00001.png", "caption": "Figure 2: Our framework."}
    assert (tmp_path / "figures" / "2401.00001.png").read_bytes() == PNG


def test_metrail_figures_refuse_non_images_and_skip_oversized_ones(serve: Any, tmp_path: Path) -> None:
    atoms = [atom(3, "figure", "Figure", {"caption_detected": "yes"})]
    base = _figure_server(serve, "text/html; charset=utf-8")
    with pytest.raises(EnrichError, match='the figure is Some\\("text/html; charset=utf-8"\\), not an image'):
        metrail.save_figure(http.Http(2), base, tmp_path, "arxiv:1", atoms)
    big = _figure_server(serve, "image/jpeg", bytes(metrail.MAX_FIGURE + 1))
    assert metrail.save_figure(http.Http(2), big, tmp_path, "arxiv:1", atoms) == {}
    assert not (tmp_path / "figures").exists()
    assert metrail.save_figure(http.Http(2), big, tmp_path, "arxiv:1", []) == {}
