from collections.abc import Callable
from typing import Any

import pytest

from isbe.core.markdown import render_markdown
from isbe.memory import Note
from isbe.pipeline.digest import parse
from isbe.pipeline.digest.load import figure_panels
from isbe.pipeline.digest.model import Card, Fact, Paper, Repo
from isbe.pipeline.digest.render import Input, cell, link_url, paper, report, text
from isbe.pipeline.digest.review import Flag, Review
from isbe.topics import Topic


def make_paper(id: str, title: str) -> Paper:
    return Paper(
        arxiv_id=id,
        title=title,
        summary="We <b>forecast</b> rain.",
        url=f"https://arxiv.org/abs/{id}",
        authors=["A One"],
        categories=["physics.ao-ph"],
        date="2026-10-10",
    )


def render(topic: Topic, reply: str = "", **fields: Any) -> str:
    sections = parse.sections(reply)
    values: dict[str, Any] = {
        "topic": topic,
        "period": "2026-W42",
        "window": ("2026-10-06", "2026-10-13"),
        "sections": sections,
        "blocks": parse.paper_blocks(sections.get("papers", "")),
        "shown": [],
        "listed": [],
        "repos": [],
        "funnel": {},
        "notes": [],
        "drafts": [],
        "review": Review(),
        "call_id": "c1",
        "report_id": "t/2026-W42/weekly",
        "generated_at": "now",
    }
    values.update(fields)
    return report(Input(**values))


def test_digest_tldr_lists_come_from_ratings_and_repo_dates(make_topic: Callable[..., Topic]) -> None:
    topic = make_topic(lang="zh")
    reply = (
        "## TL;DR\n- 扩散继续主导\n## 论文逐篇\n### [2610.00001]\n- 评价: 强相关\n- 推荐: 值得细读\n"
        "- 效果: CSI@SEVIR vs DGMR: 0.41→0.47\n### [2610.00002]\n- 评价: 一般\n- 推荐: 可读\n"
        "## 仓库逐条\n- [metnet] 本期有推送\n## 分析\n正文\n## 蒸馏\n(本期无蒸馏建议)\n"
    )
    shown = [
        make_paper("2610.00001", "DiffCast: diffusion nowcasting"),
        make_paper("2610.00002", "A very long title without any colon at all"),
    ]
    # The card's verified result replaces the reply's 0.41.
    shown[0].card = Card({"results": Fact("CSI@SEVIR vs DGMR: 0.40→0.47", "| DiffCast | 0.47 |")}, ["data"])
    shown[0].figure = (["figures/2610.00001.png"], "Figure 2: Overview of <our> framework.")
    flag = Flag("drift", "全面领先", "仅 CSI")
    repos = [
        # A link target must be an http(s) URL; the second repo's "u" is not one.
        Repo("openclimatefix/metnet", "https://github.com/openclimatefix/metnet", 9, "2026-10-12", True),
        Repo("microsoft/aurora", "u", None, "2026-09-01", False),
    ]
    md = render(
        topic,
        reply,
        shown=shown,
        listed=[make_paper("2610.00003", "Third")],
        repos=repos,
        funnel={"kept": 3, "gate_dropped": 5},
        review=Review(first=[flag], second=[flag], rewritten=True),
    )
    assert "- 本期 3 篇 / 其中 1 篇值得读：DiffCast（逐篇评审前 2 篇，其余列于文末）\n" in md, md
    assert "- 仓库活跃：openclimatefix/metnet 本期窗口内有推送\n- 扩散继续主导\n" in md
    assert "| [openclimatefix/metnet](https://github.com/openclimatefix/metnet) | 9 | 2026-10-12 | 本期有推送 |" in md
    assert "| microsoft/aurora | — | 2026-09-01 | — |" in md
    assert "| CSI | SEVIR | DGMR | 0.40 | 0.47 | +17.5% |" in md
    assert "![配图](../../figures/2610.00001.png)\n<sub>Figure 2: Overview of &lt;our&gt; framework.</sub>\n" in md
    assert "- **效果**: “| DiffCast | 0.47 |”\n- 引文未通过核验、未采用：数据\n" in md
    assert "首轮 1 条存疑 → 已重写 → 残留 1 条\n  - [drift] 全面领先 — 仅 CSI\n" in md
    assert "We &lt;b&gt;forecast&lt;/b&gt; rain." in md
    assert "## 其余入选（1，未展开）\n\n- [2610.00003] Third" in md
    assert "窗口内 8 条 → 规则剔除 5 · 判定剔除 0 · 入选 3" in md
    assert "Memory 草稿" not in md


def test_digest_figures_render_every_panel_and_one_escaped_caption() -> None:
    for figure, files in [
        ({"file": "figures/old.png", "caption": "Figure <1>"}, ["old.png"]),
        ({"files": ["figures/a.png", "figures/b.jpg"], "caption": "Figure <1>"}, ["a.png", "b.jpg"]),
    ]:
        p = make_paper("2610.00001", "Method")
        p.figure = figure_panels(figure)
        md = paper(False, p, None)
        assert md.count("![Figure]") == len(files)
        for file in files:
            assert f"![Figure](../../figures/{file})\n" in md
        assert md.count("<sub>Figure &lt;1&gt;</sub>") == 1
        assert "<sub>Figure <1></sub>" not in md

    p = make_paper("2610.00001", "Method")
    p.figure = figure_panels({
        "files": ["figures/a.png", "figures/b.jpg"],
        "caption": "Figure 1: ![tracker](https://example.org/track.png) \\[ref]\n<script>alert(1)</script>",
    })
    html = render_markdown(paper(False, p, None))
    assert html.count("<img ") == 2, html
    assert html.count("<sub>") == 1, html
    assert "<script" not in html


def test_digest_report_layout_in_english(make_topic: Callable[..., Topic]) -> None:
    topic = make_topic()
    p = make_paper("2610.00001", "Method")
    p.secondary = True
    p.authors = [f"A{i}" for i in range(12)]
    reply = (
        "## Papers\n### [2610.00001]\n- Verdict: fine\n- Plain: rain\n- Code: https://github.com/a/b_c\n"
        "- Results: PSNR from 30.1 -> 31.4 dB\n## Glossary\n- CSI: a | score\n## Analysis\nBody (memory: x@rev2)\n"
    )
    md = render(
        topic,
        reply,
        shown=[p],
        notes=[Note("t.theses", "topic", 2, "b")],
        drafts=[("topics/t.md", "New thesis")],
        review=Review(first=[Flag("drift", "c", "n")], second=[Flag("drift", "c", "n")] * 2),
    )
    assert md.startswith("# Nowcasting — 2026-W42\n\n> Window: 2026-10-06 → 2026-10-13 (papers by arXiv update date)\n\n")
    assert "- 1 papers / none to read in full\n" in md
    assert "\n### [2610.00001] Method · secondary\n\n> **Verdict**：fine\n>\n> In plain words: rain\n\n" in md
    assert "- **Authors**: " + ", ".join(f"A{i}" for i in range(10)) + " et al. · physics.ao-ph · updated 2026-10-10\n" in md
    assert "- **Link**: https://arxiv.org/abs/2610.00001\n- **Code**: https://github.com/a/b\\_c\n" in md
    assert "- **Results**: PSNR from 30.1 -&gt; 31.4 dB\n" in md
    assert "| CSI | a \\| score |\n" in md
    assert "\n## Analysis\n\nBody (memory: x@rev2)\n" in md
    assert "\n## Memory drafts (pending review)\n\n- DRAFT[topics/t.md]: New thesis\n" in md
    assert "- **memory snapshot**: t.theses@rev2\n" in md
    assert "- **review**: 1 flagged; the rewrite drew 2, so the first draft stands\n  - [drift] c — n\n" in md
    assert md.endswith("- **papers**: 2610.00001\n\n</details>\n")


ATTACKS = [
    "![x](http://evil.example/p.png?q=1)",
    "[x](javascript:alert(1))",
    "<img src=http://evil.example/p.png>",
    "<script>alert(1)</script>",
    "[x]: http://evil.example/p.png\n![y][x]",
    "\\![x](http://evil.example/p.png)",
    "`a`![x](http://evil.example/p.png)",
    "a](http://evil.example/p.png)",
]


@pytest.mark.parametrize("attack", ATTACKS)
def test_untrusted_fields_render_inert(make_topic: Callable[..., Topic], attack: str) -> None:
    p = make_paper("2610.00001", f"Title {attack}")
    p.summary = f"Abstract {attack}"
    p.authors = [f"Author {attack}"]
    p.categories = [attack]
    p.url = attack
    p.card = Card({"method": Fact(f"card {attack}", f"quote {attack}")}, [])
    p.figure = (["figures/ok.png"], f"caption {attack}")
    one = attack.replace("\n", " ")
    fields = "".join(
        f"- {k}: {k} {one}\n" for k in ("Verdict", "Rating", "Plain", "Source", "Data", "Code", "Repro")
    ) + f"- Results: CSI@{one} vs {one}: 0.1→0.2; loose {one} 1→2\n"
    reply = (
        f"## Papers\n### [2610.00001]\n{fields}## Repos\n- [o/r] note {one}\n"
        f"## Glossary\n- term {one}: gloss {one}\n## Analysis\nplain analysis\n"
    )
    md = render(
        make_topic(),
        reply,
        shown=[p],
        listed=[p],
        repos=[Repo("o/r", f"https://github.com/o/r){attack}", 1, attack, True)],
        notes=[Note(f"n {attack}", "topic", 1, "b")],
        drafts=[(f"topics/{attack}.md", f"draft {attack}")],
        review=Review(first=[Flag("drift", f"claim {attack}", f"note {attack}")], error=f"err {attack}"),
    )
    html = render_markdown(md)
    assert html.count("<img") == 1 and '<img src="/figures/ok.png" alt="Figure">' in html, html
    assert "<a " not in html and "href" not in html, html
    assert "<script" not in html and "<code>" not in html
    # The stored Markdown itself embeds no image but the figure, before any sanitizing.
    assert md.count("![") == 1 and "<img" not in md, md
    # The attack is still there to read, as text.
    assert ("evil.example" in html) == ("evil.example" in attack)


@pytest.mark.parametrize("attack", ATTACKS)
def test_free_sections_embed_no_image(make_topic: Callable[..., Topic], attack: str) -> None:
    md = render(make_topic(), f"## TL;DR\n- tldr {attack}\n## Analysis\nanalysis {attack}\n")
    assert md.count("![") == 0 and "<img" not in md.lower(), md
    html = render_markdown(md)
    # Links stay the model's own Markdown, sanitized on display.
    assert "<img" not in html and "<script" not in html and 'href="javascript' not in html


@pytest.mark.parametrize(
    "title",
    ["短临降水预报", "C++ templates", "x_1 and x_2", "a*b and c*d", "~~old~~ new", "`code` [RFC] Foo [v2]",
     "$\\alpha$-net: \\[x\\]", "a & b < c"],
)
def test_ordinary_titles_stay_readable(make_topic: Callable[..., Topic], title: str) -> None:
    p = make_paper("2610.00001", title)
    html = render_markdown(render(make_topic(), shown=[p], listed=[p]))
    readable = title.replace("&", "&amp;").replace("<", "&lt;")
    assert html.count(readable) == 2, html  # the heading and the "also kept" line
    assert "<a " not in html and "<em>" not in html and "<del>" not in html and "<code>" not in html


def test_text_escapes_only_what_can_form_markup() -> None:
    for plain in ["DiffCast: diffusion nowcasting", "短临", "C++", "\\alpha", "100% (n=3)!", "https://arxiv.org/abs/2610.00001v2"]:
        assert text(plain) == plain
    assert text("[a](b)") == "\\[a\\](b)" and text("x_1 a*b ~") == "x\\_1 a\\*b \\~"
    assert text("\\[") == "\\\\\\[" and text("end\\") == "end\\\\"
    assert text("`x`") == "\\`x\\`"
    assert text("a\nb\r\nc") == "a b  c" and text("a\n~b", block=True) == "a\n\\~b"
    assert cell("a|b\nc") == "a\\|b c"
    assert link_url("https://github.com/o/r") and link_url("http://x.org/a?b=1&c=d#e")
    for bad in ["u", "javascript:alert(1)", "https://", "https:///x", "https://x/a b", "https://x/)", "https://x/a|b", "ftp://x"]:
        assert not link_url(bad), bad
