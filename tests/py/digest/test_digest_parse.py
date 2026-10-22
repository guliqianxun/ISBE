from isbe.pipeline.digest.parse import (
    Claim,
    bracketed,
    drafts,
    glossary,
    lines,
    number,
    paper_blocks,
    results,
    sections,
    split_whitespace,
    strip_version,
    trim,
)

REPLY = (
    "## TL;DR\n- 扩散模型继续主导\n\n## 论文逐篇\n### [2610.00001v2]\n- 评价: 强相关。\n- 推荐: 值得细读\n"
    "- 效果: CSI@8mm/h@SEVIR vs DGMR: 0.41→0.47; (无明确数字)\n### 2610.00002\n- Verdict：weak\n\n## 仓库逐条\n"
    "- [openclimatefix/metnet] 本期有推送\n- [id=aurora]: 最近推送 2026-09-01\n\n## 名词\n- CSI: 命中率指标\n(本期无名词)\n\n"
    "## 分析\n正文\n\n## 蒸馏\n- DRAFT[topics/nowcasting.theses.md]: 新论点\n(本期无蒸馏建议)\n"
)


def test_digest_splits_and_reads_every_section() -> None:
    s = sections(REPLY)
    assert s["tldr"] == "- 扩散模型继续主导"
    assert s["analysis"] == "正文"
    b = paper_blocks(s["papers"])
    assert b["2610.00001"]["rating"] == "值得细读"
    assert b["2610.00002"]["verdict"] == "weak"
    assert bracketed(s["repos"])["aurora"] == "最近推送 2026-09-01"
    assert glossary(s["glossary"]) == [("CSI", "命中率指标")]
    assert drafts(s["drafts"]) == [("topics/nowcasting.theses.md", "新论点")]

    claims, notes = results(b["2610.00001"]["results"])
    assert claims == [
        Claim(metric="CSI@8mm/h", dataset="SEVIR", model="DGMR", baseline="0.41", new="0.47", delta="+14.6%")
    ]
    assert notes == []
    assert len(results("PSNR from 30.1 -> 31.4 dB")[1]) == 1
    assert (number("41.2M"), number("x -3."), number("v2.")) == (41.2, -3.0, 2.0)


def test_digest_parse_follows_the_string_rules() -> None:
    assert lines("a\r\n\nb\n") == ["a", "", "b"]
    assert lines("a\rb\r") == ["a\rb\r"] and lines("") == [] and lines("\n") == [""]
    # trim follows Unicode White_Space, which leaves the U+001C–U+001F separators alone.
    assert trim("　 x \n") == "x" and trim("\x1cx") == "\x1cx"
    assert split_whitespace(" a  b\n") == ["a", "b"]
    assert strip_version("2610.00001v12") == "2610.00001"
    assert strip_version("2610.00001v") == "2610.00001v" and strip_version("abc") == "abc"
    # Only ASCII digits make an id; `### Notes` and `### [] x` open no block.
    assert paper_blocks("### Notes\n- 评价: x\n### [] x\n- verdict: y\n### ٣\n- verdict: z") == {}


def test_digest_results_claims_and_deltas() -> None:
    claims, notes = results("| MAE@ERA5 对比 GraphCast: 2.0→1.5 |；FSS@x: 0 → 0.3; acc 0.1→0.2; (无)")
    assert claims[0] == Claim("MAE", "ERA5", "GraphCast", "2.0", "1.5", "-25.0%")
    assert claims[1] == Claim("FSS", "x", "", "0", "0.3", None)
    assert notes == ["acc 0.1→0.2"]
    # " vs." wins over " vs " wherever it appears; the dataset keeps its case.
    assert results("CSI@SEVIR VS. DGMR: 1->2")[0] == [Claim("CSI", "SEVIR", "DGMR", "1", "2", "+100.0%")]
    assert results("a@b: 1→")[0] == [] and results("@b: 1→2")[0] == []
