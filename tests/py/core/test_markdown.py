import pytest

from isbe.core.markdown import local_figure, render_markdown


def test_report_renders_safe_html_with_figures() -> None:
    html = render_markdown(
        "## TL;DR\n\n![配图](../../figures/2610.1.png)\n<sub>Fig</sub>\n\n<script>alert(1)</script>\n\n"
        '<details markdown="1"><summary>原文依据</summary>\n\n- **方法**: “q”\n\n</details>\n\n'
        "| a | b |\n| --- | --- |\n| 1 | 2 |\n"
    )
    assert '<img src="/figures/2610.1.png" alt="配图">' in html, html
    assert "<script" not in html
    assert "<details><summary>原文依据</summary>" in html
    assert "<td>2</td>" in html
    assert "<sub>Fig</sub>" in html


def test_links_strikethrough_and_unsafe_markup() -> None:
    html = render_markdown(
        "[site](https://example.com/a) [bad](javascript:alert(1)) ~~old~~\n\n"
        '<a href="https://e.com" onclick="x()">e</a> <span style="color:red">s</span> <!-- note -->\n\n'
        "<iframe src=\"https://e.com\"></iframe><style>p{}</style>"
    )
    assert '<a href="https://example.com/a" rel="noopener noreferrer">site</a>' in html
    assert 'href="javascript' not in html and "onclick" not in html and "style" not in html
    assert "<del>old</del>" in html
    assert "<!--" not in html and "<iframe" not in html


@pytest.mark.parametrize(
    "source",
    [
        "https://tracker.example/pixel.png",
        "//tracker.example/pixel.png",
        "http://127.0.0.1/figures/x.png",
        "data:image/png;base64,iVBORw0KGgo=",
        "figures/x.png",
        "/figures/../secret.png",
        "/figures/./x.png",
        "/figures/",
        "/figures/x.png?track=1",
        "/figures/x.png#frag",
        "/figures/x%2F..png",
        "/static/x.png",
        "../../media/x.png",
    ],
)
def test_only_local_figures_render_as_images(source: str) -> None:
    assert not local_figure(source)
    for md in [f"before ![alt]({source}) after", f'before <img src="{source}" alt="alt"> after']:
        html = render_markdown(md)
        assert "<img" not in html, html
        assert "before" in html and "after" in html
    raw = render_markdown(f'<img alt="x>y" title="t" src="{source}"><img src="/figures/ok.png">')
    assert raw.count("<img") == 1 and '<img src="/figures/ok.png">' in raw, raw


def test_local_figures_and_relative_report_links_still_render() -> None:
    for source in ["/figures/2610.1.png", "/figures/topic/run-1/fig_2.svg", "/figures/-x"]:
        assert local_figure(source)
    html = render_markdown(
        "![a](../../figures/topic/run-1/fig_2.svg) ![b](/figures/2610.1.png)\n\n"
        '<img src="/figures/x.png" width="10" onerror="alert(1)">'
    )
    assert html.count("<img") == 3
    assert '<img src="/figures/topic/run-1/fig_2.svg" alt="a">' in html
    assert '<img src="/figures/x.png" width="10">' in html
    assert "onerror" not in html


def test_tables_use_pulldown_cmark_layout() -> None:
    # Expected HTML is the table layout existing reports were rendered with.
    md = (
        "| Left | Center | Right | None |\n|:-----|:------:|------:|------|\n"
        "| a | **b** | `c` | d \\| e |\n| 1 | 2 | 3 |\n\nPara after.\n\n| one |\n|---|\n"
    )
    assert render_markdown(md) == (
        "<table><thead><tr><th>Left</th><th>Center</th><th>Right</th><th>None</th></tr></thead><tbody>\n"
        "<tr><td>a</td><td><strong>b</strong></td><td><code>c</code></td><td>d | e</td></tr>\n"
        "<tr><td>1</td><td>2</td><td>3</td><td></td></tr>\n</tbody></table>\n"
        "<p>Para after.</p>\n<table><thead><tr><th>one</th></tr></thead><tbody>\n</tbody></table>\n"
    )


def test_documents_rewrite_links_and_anchor_headings() -> None:
    md = "# Intro\n\n## Set up `uv`!\n\n## Intro\n\n[a](a.md#x) [b](b.md) [web](https://example.com) [here](#intro)\n"
    html = render_markdown(md, lambda target: None if target == "b.md" else target.replace("a.md", "/help?doc=a"))
    assert '<h1 id="intro">' in html and '<h2 id="set-up-uv">' in html and '<h2 id="intro-1">' in html
    assert '<a href="/help?doc=a#x"' in html and '<a href="https://example.com"' in html and '<a href="#intro"' in html
    assert "b.md" not in html and ">b</a>" in html
    # Reports keep their markup: no heading ids.
    assert render_markdown("# Intro\n") == "<h1>Intro</h1>\n"
    assert '<h1 id="x">' not in render_markdown('<h1 id="x">raw</h1>')
