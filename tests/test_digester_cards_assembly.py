"""Phase-2 assembly tests: card views, facts-block card preference, and the
weekly template rendering fact rows from cards instead of writer output."""

from types import SimpleNamespace

from jinja2 import Template

from isbe.topics._shared.digester import SHARED_TEMPLATE, _build_facts_block, _card_view
from isbe.topics._shared.paper_cards import CardField, PaperCard


def _card(**fields) -> PaperCard:
    return PaperCard(
        arxiv_id="1.1",
        corpus_sha256="x",
        generated_at="2026-08-05T00:00:00Z",
        code_urls=["https://github.com/a/b"],
        gpu_mentions=["8×A100"],
        dataset_mentions=["SEVIR"],
        fields={
            k: CardField(value=v, anchor="anchor text here", verified=True)
            for k, v in fields.items()
        },
        rejected_fields=["compute"],
    )


def _paper(arxiv_id="1.1"):
    return SimpleNamespace(
        arxiv_id=arxiv_id,
        title="T",
        primary_category="cs.LG",
        source_url="https://arxiv.org/abs/1.1",
        abstract="abs",
        fulltext_uri=None,
        authors=["A"],
        submitted_at=None,
        pdf_uri=None,
    )


def test_card_view_flattens_fields_and_regex_facts():
    view = _card_view(_card(method="小波+flow", results="CSI 0.41→0.47"))
    assert view["method"] == "小波+flow"
    assert view["code"] == "https://github.com/a/b"
    assert view["compute"] == "8×A100"  # falls back to regex gpu_mentions
    assert view["datasets"] == "SEVIR"
    assert view["rejected"] == ["compute"]


def test_facts_block_prefers_card_over_excerpt():
    view = _card_view(_card(method="m", results="r"))
    block = _build_facts_block(
        [_paper()],
        None,
        fulltext_for=lambda p: "x" * 5000,
        cards={"1.1": view},
    )
    assert "证据卡:" in block
    assert "方法=m" in block and "结果=r" in block
    assert "全文摘录" not in block  # card replaces the raw excerpt


def _render(card_view=None):
    tpl = Template(SHARED_TEMPLATE.read_text(encoding="utf-8"))
    return tpl.render(
        topic_id="nowcasting",
        topic_label="t",
        period_label="2026-W32",
        tldr="tl",
        analysis="an",
        distillation="",
        memory_refs="",
        trace_id="abc123",
        papers=[_paper()],
        paper_blocks={},
        paper_assets={},
        paper_cards={"1.1": card_view} if card_view else {},
        glossary=[],
        repos=[],
        repo_reviews={},
        comparison=None,
        generated_at="2026-08-05",
        artifact_id="real-artifact-id",
    )


def test_template_renders_card_fact_rows():
    view = _card_view(
        _card(
            method="小波分解+rectified flow",
            data="KNMI+SEVIR 公开",
            results="CSI 0.41→0.47 vs DGMR",
            limitations="仅在雷达数据验证",
        )
    )
    out = _render(view)
    assert "- **方法/背景**: 小波分解+rectified flow" in out
    assert "- **数据**: KNMI+SEVIR 公开（数据集: SEVIR）" in out
    assert "- **代码**: https://github.com/a/b" in out
    assert "算力=8×A100" in out
    assert "- **效果（原文）**: CSI 0.41→0.47 vs DGMR" in out
    assert "- **局限（作者自述）**: 仅在雷达数据验证" in out
    assert "evidence cards" in out and "拒绝 1 个字段" in out


def test_template_without_cards_keeps_legacy_shape():
    out = _render(None)
    assert "效果（原文）" not in out
    assert "evidence cards" not in out
    assert "real-artifact-id" in out  # audit placeholder fixed
