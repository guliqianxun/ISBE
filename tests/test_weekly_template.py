"""Render-path tests for the shared research weekly template.

Renders the REAL `weekly.j2` directly (not via the flow) so the template
contract — verifiability fields, the demoted effect table, the code/repro
entry point, and HTML-escaping of untrusted free-text — is pinned without a
DB/LLM. Complements the digester end-to-end test, which proves the wiring.
"""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Template

from isbe.notify.render import render_html
from isbe.topics._shared.digester_utils import PaperBlock, SotaClaim

_TEMPLATE = (
    Path("src/isbe/topics/_shared/templates/weekly.j2").read_text(encoding="utf-8")
)


def _render(paper_blocks, glossary=None, papers=None):
    papers = papers or [
        SimpleNamespace(
            arxiv_id="2506.01234",
            title="A Paper",
            authors=["L. Chen"],
            primary_category="physics.ao-ph",
            submitted_at=datetime(2026, 6, 14),
            source_url="https://arxiv.org/abs/2506.01234",
            pdf_uri=None,
            abstract="plain abstract text",
        )
    ]
    return Template(_TEMPLATE).render(
        topic_id="nowcasting",
        topic_label="nowcasting",
        period_label="2026-W24",
        tldr="tldr",
        analysis="analysis",
        distillation="",
        memory_refs="m@rev1",
        trace_id="t1",
        generated_at="2026-06-16T08:00:00+00:00",
        artifact_id="a1",
        papers=papers,
        paper_blocks=paper_blocks,
        glossary=glossary or [],
        repos=[],
        repo_reviews={},
        comparison=None,
    )


def test_verifiability_fields_and_code_render():
    blk = PaperBlock(
        arxiv_id="2506.01234",
        verdict="值得细读。",
        plain="大白话。",
        provenance="L. Chen（DeepMind Weather）；延续 DGMR。",
        method="扩散模型；建立在 DGMR 之上。",
        data="SEVIR（公开）。",
        code="https://github.com/example/diffcast-xl",
        sota=(SotaClaim("CSI", "SEVIR", "0.41", "0.47", "raw", "DGMR", "+14.6%"),),
        repro={"开源": "是", "代码完整度": "高"},
    )
    md = _render({"2506.01234": blk})
    assert "- **来源**: L. Chen（DeepMind Weather）；延续 DGMR。" in md
    assert "- **方法/背景**: 扩散模型；建立在 DGMR 之上。" in md
    assert "- **数据**: SEVIR（公开）。" in md
    assert "- **代码**: https://github.com/example/diffcast-xl" in md
    assert "效果（参考指标，非排名结论）" in md
    assert "| CSI | SEVIR | DGMR | 0.41 | 0.47 | +14.6% |" in md


def test_untrusted_fields_are_html_escaped():
    """A malicious arXiv abstract / LLM field must not inject live HTML into the
    email. The template escapes free-text fields, so the markdown source carries
    entities and the final HTML has no executable tag."""
    evil = '<script>alert(1)</script><img src=x onerror=alert(2)>'
    blk = PaperBlock(arxiv_id="2506.01234", verdict=evil, provenance=evil, method=evil)
    papers = [
        SimpleNamespace(
            arxiv_id="2506.01234", title=evil, authors=["x"],
            primary_category="cs.LG", submitted_at=datetime(2026, 6, 14),
            source_url="https://arxiv.org/abs/2506.01234", pdf_uri=None,
            abstract=evil,
        )
    ]
    md = _render({"2506.01234": blk}, papers=papers)
    # markdown source is escaped, not literal tags
    assert "<script>" not in md
    assert "&lt;script&gt;" in md
    # and end-to-end through the HTML email renderer: no live tag survives
    html = render_html(
        topic_label="nowcasting", period_label="2026-W24",
        artifact_md=md, artifact_path=None,
    )
    # no live tags — escaped entities (&lt;img …onerror=…&gt;) as text are fine
    assert "<script" not in html
    assert "<img" not in html


def test_no_blocks_still_renders_papers():
    """Fail-safe: a paper with no PaperBlock still renders title + abstract."""
    md = _render({})
    assert "### [2506.01234] A Paper" in md
    assert "plain abstract text" in md
