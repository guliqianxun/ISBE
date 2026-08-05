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
    assert "全文事实:" in block
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
    assert "事实核验" in out and "1 个字段未过校验" in out
    assert "证据卡" not in out  # internal jargon must stay invisible


def test_cards_writer_prompt_is_opinions_only():
    from isbe.llm.prompts import CARDS_SYSTEM_PROMPT, SYSTEM_PROMPT

    assert "证据卡" in CARDS_SYSTEM_PROMPT
    assert "你不输出事实字段" in CARDS_SYSTEM_PROMPT
    # the slim per-paper contract keeps only opinion rows
    assert "- 评价:" in CARDS_SYSTEM_PROMPT and "- 速览:" in CARDS_SYSTEM_PROMPT
    assert "- 数据: <训练" not in CARDS_SYSTEM_PROMPT  # legacy field spec absent
    assert "- 数据: <训练" in SYSTEM_PROMPT  # legacy untouched


def test_slim_paper_blocks_parse_without_fact_fields():
    from isbe.topics._shared.digester_utils import parse_paper_blocks

    text = "### [2608.01626]\n- 评价: 值得细读\n- 速览: 拆尺度再预测\n"
    blocks = parse_paper_blocks(text)
    blk = blocks["2608.01626"]
    assert blk.verdict == "值得细读"
    assert blk.plain == "拆尺度再预测"
    assert not blk.sota


def test_template_without_cards_keeps_legacy_shape():
    out = _render(None)
    assert "效果（原文）" not in out
    assert "事实核验" not in out
    assert "real-artifact-id" in out  # audit placeholder fixed


def test_weekly_label_is_content_week():
    """Label = the week the window covers (anchored on yesterday): a Monday
    run reports on — and is labeled as — the week that just ended."""
    from datetime import date
    from unittest.mock import MagicMock, patch

    from isbe.cli.topics_cmd import _period_label_for

    cfg = MagicMock()
    cfg.cadence = "weekly"
    with patch("isbe.cli.topics_cmd.load_topic_config_typed", return_value=cfg):
        # Mon 2026-08-03 (first day of W32) → covers W31
        assert _period_label_for("nowcasting", date(2026, 8, 3), None) == "2026-W31"
        # Sun 2026-08-09 (last day of W32) → still inside W32's coverage
        assert _period_label_for("nowcasting", date(2026, 8, 9), None) == "2026-W32"
        # explicit override always wins
        assert _period_label_for("nowcasting", date(2026, 8, 3), "2026-W30") == "2026-W30"


def test_scrub_internal_jargon():
    from isbe.topics._shared.digester import _scrub_internal_jargon

    text = "值得细读（据卡片:结果、复现）。证据卡显示其数据公开。显卡型号无关。"
    out = _scrub_internal_jargon(text)
    assert "卡片" not in out.replace("显卡", "")
    assert "（据原文）" in out
    assert "全文事实显示其数据公开" in out
    assert "显卡型号无关" in out  # unrelated words untouched
