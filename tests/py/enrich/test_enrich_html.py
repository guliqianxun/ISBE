"""arXiv HTML extraction (text, figures, limits, tokenizer) and its fetch path against a mock arXiv."""

import httpx
import pytest

from isbe.core import http
from isbe.core.errors import describe
from isbe.pipeline.enrich import arxiv_html
from isbe.pipeline.enrich.arxiv_html import arxiv_id, image_url, parse, resolve_figures
from isbe.pipeline.enrich.calls import EnrichError
from isbe.pipeline.enrich.html_extract import (
    MAX_DEPTH,
    MAX_FIGURES,
    MAX_HTML,
    MAX_PANELS,
)


def test_ids_cannot_change_the_host_or_path() -> None:
    for id in ["2401.0001", "2401.00001v2", "math/0309136", "solv-int/9910001v3", "hep-th/9901001"]:
        assert arxiv_id(f"arxiv:{id}") == id
    for id in [
        "2400.00001", "2413.00001", "2401.00001v0", "2401.00001v1/extra", "2401.00001?x=1",
        "2401.00001#frag", "../2401.00001", "a/../2401.00001", "http://evil.test", "//evil.test",
        "2401%2f00001", "math/../0309136", "math/0309136/", "2401.00001v", "2401.00001\n",
        "2401.00001v99999999999", "２４０１.00001",
    ]:
        assert arxiv_id(f"arxiv:{id}") is None, id
    assert arxiv_id("2401.00001") is None


def test_keeps_source_text_links_tables_and_one_math_representation() -> None:
    text = parse("""<html><nav>Site menu</nav><article class='other ltx_document'>
            <h1>A &amp; B</h1><p>We reach <strong>98.2%</strong> accuracy&nbsp;today.</p>
            <p><a href='https://github.com/a/b?x=1&amp;y=2'>Code</a> <a href='#S1'>Section 1</a></p>
            <table><caption>Results</caption><tr><th>Model</th><th>Score</th></tr><tr><td>Ours</td><td>98.2</td></tr></table>
            <p>Use <math alttext='x &lt; y'><semantics><mi>duplicate</mi><annotation>duplicate</annotation></semantics></math>.</p>
            <form>Ignore form</form><script>let x = '<article class="ltx_document">fake</article>';</script>
            <style>.x {display:none}</style><nav>Ignore navigation</nav></article><p>Footer</p></html>""").body
    assert text.startswith("## A & B\n\nWe reach 98.2% accuracy today."), text
    assert "Code (https://github.com/a/b?x=1&y=2) Section 1" in text, text
    assert "### Table\nResults\n| Model | Score |\n| Ours | 98.2 |" in text, text
    assert "Use x < y." in text, text
    for removed in ["duplicate", "Ignore", "Site menu", "Footer", "fake", "display:none"]:
        assert removed not in text, removed


def test_associates_captions_and_keeps_all_subfigure_panels() -> None:
    paper = parse("""<article class='ltx_document'><h1>Paper</h1>
            <figure class='ltx_figure' id='F1'>
              <figure class='ltx_figure'><img src='2401.00001v2/a.png'><figcaption>(a) First panel.</figcaption></figure>
              <figure class='ltx_figure'><img src='2401.00001v2/b.jpg'><figcaption>(b) Second panel.</figcaption></figure>
              <figcaption>Figure 1. <strong>Model architecture</strong> &amp; workflow.</figcaption>
            </figure>
            <figure class='ltx_figure'><figcaption>Figure 2. Results.</figcaption><img src='./2401.00001v2/results.webp'></figure>
            <figure class='ltx_figure'><img src='uncaptioned.png' alt='[Uncaptioned image]'></figure>
            </article>""")
    assert len(paper.figures) == 2
    assert paper.figures[0].images == ["2401.00001v2/a.png", "2401.00001v2/b.jpg"]
    assert paper.figures[0].caption == (
        "Figure 1. Model architecture & workflow. (a) First panel. (b) Second panel."
    )
    assert paper.figures[1].caption == "Figure 2. Results."
    assert paper.figures[1].images == ["./2401.00001v2/results.webp"]
    assert paper.figure_warnings == ["figure group 3 omitted: missing caption"]


def test_excludes_interface_hidden_math_and_table_images() -> None:
    paper = parse("""<nav><figure class='ltx_figure'><img src='logo.png'><figcaption>Logo.</figcaption></figure></nav>
            <article class='ltx_document'><h1>Paper</h1><img class='ltx_graphics' src='logo.png' alt='Logo'>
              <figure class='ltx_figure' hidden><img src='hidden.png'><figcaption>Hidden.</figcaption></figure>
              <div style='display: none !important'><figure class='ltx_figure'><img src='hidden-style.png'><figcaption>Hidden.</figcaption></figure></div>
              <figure class='ltx_table'><img src='table.png'><figcaption>Table.</figcaption></figure>
              <table><tr><td><figure class='ltx_figure'><img src='table-nested.png'><figcaption>Table.</figcaption></figure></td></tr></table>
              <figure class='ltx_figure'><figcaption>Figure 1. Visible.</figcaption>
                <img src='good.png'><img src='hidden-panel.png' aria-hidden='true'>
                <math><img src='formula.png'></math><div class='ltx_equation'><img src='equation.png'></div>
              </figure>
            </article>""")
    assert len(paper.figures) == 1
    assert paper.figures[0].images == ["good.png"]
    assert paper.figures[0].caption == "Figure 1. Visible."


def test_resolves_actual_document_relative_images_and_rejects_whole_unsafe_groups() -> None:
    base = "https://arxiv.org/html/2401.00001v2"
    assert image_url(base, "2401.00001v2/a.png") == "https://arxiv.org/html/2401.00001v2/a.png"
    assert image_url("https://arxiv.org/html/2401.00001v2/", "a.png") == (
        "https://arxiv.org/html/2401.00001v2/a.png"
    )
    assert image_url(base, "/html/2401.00001v2/a.JPEG") == (
        "https://arxiv.org/html/2401.00001v2/a.JPEG"
    )
    assert image_url(base, "a.png#frag") == "https://arxiv.org/html/a.png"
    for src in [
        "https://evil.test/html/a.png", "//evil.test/html/a.png", "http://arxiv.org/html/a.png",
        "https://arxiv.org:444/html/a.png", "https://user:pass@arxiv.org/html/a.png", "/abs/a.png",
        "../a.png", "/html/%2f../a.png", "data:image/png;base64,eA==", "a.svg",
        "a.SVG?format=png", "a.gif", "a.png\n", "",
    ]:
        assert image_url(base, src) is None, src
    assert image_url("https://evil.test/html/paper", "https://arxiv.org/html/a.png") is None
    paper = parse("<article class='ltx_document'><h1>Paper</h1><figure class='ltx_figure'><img src='good.png'><img src='https://evil.test/bad.png'><figcaption>Figure 1. Both panels.</figcaption></figure></article>")
    assert len(paper.figures[0].images) == 2
    warnings = paper.figure_warnings
    assert resolve_figures(base, paper.figures, warnings) == []
    assert warnings == ["image candidate 1 omitted: unsupported or unsafe image URL"]


def test_bounds_figures_without_silently_truncating_panels() -> None:
    group = "<figure class='ltx_figure'><img src='a.png'><figcaption>Figure.</figcaption></figure>"
    paper = parse(f"<article class='ltx_document'><h1>Paper</h1>{group * (MAX_FIGURES + 1)}</article>")
    assert len(paper.figures) == MAX_FIGURES
    assert paper.figure_warnings == ["additional figure groups omitted after the 32-group limit"]
    repeated = parse("<article class='ltx_document'><figure class='ltx_figure'><img src='same.png'><img src='same.png'><figcaption>Two panels.</figcaption></figure></article>")
    assert repeated.figures[0].images == ["same.png", "same.png"]
    panels = "".join(f"<img src='{n}.png'>" for n in range(MAX_PANELS + 1))
    paper = parse(f"<article class='ltx_document'><figure class='ltx_figure'>{panels}<figcaption>All panels.</figcaption></figure></article>")
    assert paper.figures == []
    assert paper.figure_warnings == ["figure group 1 omitted: more than 12 panels"]


def test_warns_about_missing_or_oversize_sources_without_changing_text() -> None:
    html = "<article class='ltx_document'><h1>Paper</h1><figure class='ltx_figure'><img src='good.png'><img><figcaption>Both panels.</figcaption></figure></article>"
    paper = parse(html)
    assert paper.figures == []
    assert paper.figure_warnings == ["figure group 1 omitted: missing image src"]
    valid = parse(html.replace("<img>", "<img src='second.png'>"))
    assert paper.body == valid.body
    oversized = parse(html.replace("<img>", f"<img src='{'secret' * 700}'>"))
    assert oversized.figure_warnings == ["figure group 1 omitted: image src exceeds 4096 bytes"]
    assert oversized.body == valid.body


def test_rejects_error_pages_empty_articles_and_resource_exhaustion() -> None:
    for html in [
        "<h1>Not found</h1>",
        "<article>Not a paper</article>",
        "<article class='ltx_document'> </article>",
        "<article class='ltx_document'><p>Incomplete",
        "<script>\"<article class='ltx_document'>fake</article>\"</script>",
        "<template><article class='ltx_document'>fake</article></template>",
    ]:
        with pytest.raises(EnrichError):
            parse(html)
    with pytest.raises(EnrichError, match="exceeds 10 MiB"):
        parse("x" * (MAX_HTML + 1))
    deep = f"<article class='ltx_document'>{'<div>' * MAX_DEPTH}text{'</div>' * MAX_DEPTH}</article>"
    with pytest.raises(EnrichError, match="extraction limits"):
        parse(deep)


def test_tokenizer_raw_text_and_self_closing_rules() -> None:
    # Only script/style/textarea/title switch the tokenizer, `/>` or not; a
    # self-closing element opens no frame, so its raw text is not ignored.
    text = parse(
        "<article class='ltx_document'><p>Kept <xmp><b>tags</b></xmp> and <iframe>inner</iframe>"
        "<script/><b>raw</b></script> text<div/>after</p></article>"
    ).body
    assert text == "Kept tags and inner<b>raw</b> text\n\nafter", text


def _client(handler: httpx.MockTransport) -> http.Http:
    return http.Http(5, transport=handler)


def test_fetch_reads_arxiv_html_and_resolves_figures_against_the_final_url() -> None:
    page = ("<article class='ltx_document'><p>Body.</p><figure class='ltx_figure'>"
            "<img src='2401.00001v2/x1.png'><figcaption>Figure 1: Framework.</figcaption>"
            "</figure></article>")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/html/2401.00001":
            return httpx.Response(301, headers={"Location": "https://arxiv.org/html/2401.00001v2"})
        assert str(request.url) == "https://arxiv.org/html/2401.00001v2"
        return httpx.Response(200, headers={"Content-Type": "text/html; charset=utf-8"}, text=page)

    paper = arxiv_html.fetch(_client(httpx.MockTransport(handler)), "arxiv:2401.00001")
    assert paper.body.startswith("Body.")
    assert paper.figures[0].images == ["https://arxiv.org/html/2401.00001v2/x1.png"]


@pytest.mark.parametrize(("status", "kind", "message"), [
    (404, "text/html", "fetch arXiv HTML: http status: 404"),
    (200, "application/pdf", "arXiv did not return an HTML paper"),
    (200, None, "arXiv did not return an HTML paper"),
])
def test_fetch_failures_read_like_the_stored_stats(status: int, kind: str | None, message: str) -> None:
    headers = {} if kind is None else {"Content-Type": kind}
    transport = httpx.MockTransport(lambda request: httpx.Response(status, headers=headers, text="x"))
    with pytest.raises(EnrichError) as caught:
        arxiv_html.fetch(_client(transport), "arxiv:2401.00001")
    assert describe(caught.value) == message
    with pytest.raises(EnrichError, match="invalid arXiv paper id"):
        arxiv_html.fetch(_client(transport), "arxiv:../x")
