"""Unit tests for the structured research paper-card parser (digest contract v2).

Covers `parse_paper_blocks` (verdict / 速览 / SOTA table / 复现 badge) and
`parse_glossary` (beginner-lens term table), including graceful degradation on
under-filled blocks, the legacy one-line form, and noisy LLM output.
"""

from isbe.topics._shared.digester_utils import (
    parse_glossary,
    parse_paper_blocks,
)

_FULL = """### [2506.01234]
- 评价: 大厂署名+开源+SOTA 三信号齐备，本期 must-read；单卡实时未给延迟，待核。
- 速览: 用扩散模型做未来 0-3 小时降水预报，比上一代更准且开源。
- SOTA: CSI@8mm/h@SEVIR: 0.41→0.47; CSI@MeteoNet: 0.38->0.44
- 复现: 开源=是 · 权重=是 · 算力=1×A100 · 代码完整度=高

### [2506.05678]
- 评价: 方法稳健但 benchmark 仅 pySTEPS。
- SOTA: (无明确 SOTA 声明)
- 复现: 开源=未知 · 代码完整度=未知
"""


def test_parse_blocks_extracts_two_papers():
    b = parse_paper_blocks(_FULL)
    assert set(b) == {"2506.01234", "2506.05678"}


def test_verdict_and_plain_captured():
    blk = parse_paper_blocks(_FULL)["2506.01234"]
    assert "must-read" in blk.verdict
    assert blk.plain.startswith("用扩散模型")


def test_sota_parsed_as_structured_table_rows():
    blk = parse_paper_blocks(_FULL)["2506.01234"]
    assert len(blk.sota) == 2
    c0 = blk.sota[0]
    assert c0.structured
    assert c0.metric == "CSI@8mm/h"  # metric may itself contain '@'
    assert c0.dataset == "SEVIR"
    assert c0.baseline == "0.41" and c0.new == "0.47"
    # second claim used ASCII '->' and still parses
    assert blk.sota[1].dataset == "MeteoNet" and blk.sota[1].new == "0.44"


def test_repro_parsed_into_keyed_dict():
    blk = parse_paper_blocks(_FULL)["2506.01234"]
    assert blk.repro == {
        "开源": "是",
        "权重": "是",
        "算力": "1×A100",
        "代码完整度": "高",
    }


def test_negative_sota_yields_no_rows():
    """A paper with no SOTA claim must not fabricate table rows."""
    blk = parse_paper_blocks(_FULL)["2506.05678"]
    assert blk.sota == ()
    # partial repro still captured
    assert blk.repro == {"开源": "未知", "代码完整度": "未知"}


def test_prose_without_number_is_dropped():
    """Structured-or-drop: a placeholder / prose claim with no metric number
    must NOT render a junk bullet under the SOTA heading."""
    txt = "### [2506.00001]\n- SOTA: 在内部测试集上较强，但无公开基线\n"
    blk = parse_paper_blocks(txt)["2506.00001"]
    assert blk.sota == ()


def test_numeric_non_canonical_sota_kept_as_raw():
    """A loosely-formatted claim that still has a number + increment arrow is
    real data — keep it as a raw note rather than losing it."""
    txt = "### [2506.00002]\n- SOTA: FID 在 COCO 上 12.3→9.8\n"
    blk = parse_paper_blocks(txt)["2506.00002"]
    assert len(blk.sota) == 1
    assert not blk.sota[0].structured
    assert "12.3" in blk.sota[0].raw


def test_placeholder_sota_yields_nothing():
    txt = "### [2506.00003]\n- SOTA: (无明确 SOTA 声明)\n"
    assert parse_paper_blocks(txt)["2506.00003"].sota == ()


def test_baseline_model_and_delta_computed():
    txt = "### [2506.00004]\n- SOTA: CSI@8mm/h@SEVIR vs DGMR: 0.41→0.47\n"
    c = parse_paper_blocks(txt)["2506.00004"].sota[0]
    assert c.structured
    assert c.metric == "CSI@8mm/h" and c.dataset == "SEVIR"
    assert c.baseline_model == "DGMR"
    assert c.delta_pct == "+14.6%"


def test_underfilled_block_degrades_gracefully():
    """A paper the LLM only gave a verdict for must still parse (no crash,
    empty optional fields) — the card never disappears on a parse miss."""
    txt = "### [2506.42424]\n- 评价: 相关但增量有限。\n"
    blk = parse_paper_blocks(txt)["2506.42424"]
    assert blk.verdict == "相关但增量有限。"
    assert blk.plain == "" and blk.sota == () and blk.repro == {}


def test_legacy_one_line_form_maps_to_verdict():
    """Back-compat: the old `- [id] verdict` shape still yields a verdict."""
    txt = "- [2604.99999] PaperX 强相关，值得细读。"
    blk = parse_paper_blocks(txt)["2604.99999"]
    assert "强相关" in blk.verdict
    assert blk.sota == () and blk.repro == {}


def test_full_width_colon_tolerated():
    txt = "### [2506.07777]\n- 评价：用了全角冒号也要能解析。\n"
    blk = parse_paper_blocks(txt)["2506.07777"]
    assert "全角冒号" in blk.verdict


def test_verifiability_fields_captured():
    """来源/方法/数据 (provenance/method/data) — the researcher-facing
    verifiable fields — must parse, and `效果` must map to the SOTA key."""
    txt = (
        "### [2506.09999]\n"
        "- 评价: 值得细读。\n"
        "- 来源: 第一作者 X（Acme Lab）；延续 DGMR。\n"
        "- 方法: 扩散模型；建立在 DGMR 之上。\n"
        "- 数据: SEVIR（公开）+ 自采雷达。\n"
        "- 复现: 开源=是 · 代码完整度=高\n"
        "- 效果: CSI@SEVIR vs DGMR: 0.40→0.46\n"
    )
    blk = parse_paper_blocks(txt)["2506.09999"]
    assert "Acme Lab" in blk.provenance
    assert "建立在 DGMR" in blk.method
    assert "公开" in blk.data and "自采" in blk.data
    # `效果` label routed to the SOTA field
    assert len(blk.sota) == 1 and blk.sota[0].baseline_model == "DGMR"


def test_code_url_field_captured():
    """代码 (reproduction entry point) parses as its own field."""
    txt = (
        "### [2506.08888]\n"
        "- 评价: ok\n"
        "- 代码: https://github.com/example/diffcast-xl\n"
    )
    blk = parse_paper_blocks(txt)["2506.08888"]
    assert blk.code == "https://github.com/example/diffcast-xl"


def test_glossary_parses_term_pairs():
    g = parse_glossary("- CSI: 命中率指标，越高越准\n- SEVIR: 公开降水数据集\n(本期无名词)")
    assert g == [("CSI", "命中率指标，越高越准"), ("SEVIR", "公开降水数据集")]


def test_glossary_empty_when_placeholder_only():
    assert parse_glossary("(本期无名词)") == []
