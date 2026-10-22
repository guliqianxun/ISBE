"""Research briefings: rendering, evidence checks, refusals and layout."""

import copy
from typing import Any

import pytest

from isbe.reports import briefing
from isbe.reports.briefing import BriefingError, render
from isbe.reports.research_compat import DecodeError


def fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    draft = {
        "title": "行业周报",
        "intro": "本期记录银行已披露的融资安排。",
        "sections": [{"title": "绿色融资", "items": [{
            "headline": "银行披露节水融资安排",
            "body": ["银行发布节水融资安排。"],
            "source_ids": ["s1"],
            "evidence": [{"source_id": "s1", "paragraph": 0, "quote": "银行发布节水融资安排"}],
            "data_rows": [{
                "label": "贷款余额", "value": "750亿元", "scope": "截至6月末，全集团",
                "evidence": {"source_id": "s1", "paragraph": 1,
                             "quote": "截至6月末，全集团贷款余额750亿元"},
            }],
        }]}],
        "schedule": [{"when": "10月20日", "arrangement": "举行交流活动", "source_id": "s1",
                      "paragraph": 2, "quote": "10月20日举行交流活动"}],
    }
    snapshot = {
        "week_start": "2026-10-12",
        "week_end": "2026-10-18",
        "plan": {"config": {"id": "shanghai"}},
        "materials": [
            {"id": "s1", "url": "https://example.org/news", "content": {
                "title": "银行融资公告", "publisher": "媒体", "published_at": "2026-10-16",
                "original": {"paragraphs": [
                    "银行发布节水融资安排。", "截至6月末，全集团贷款余额750亿元。",
                    "10月20日举行交流活动。",
                ]},
            }},
            {"id": "s2", "url": "https://example.org/other", "content": {
                "title": "另一篇公告", "original": {"paragraphs": ["另一机构介绍披露要求。"]},
            }},
        ],
    }
    return copy.deepcopy(draft), copy.deepcopy(snapshot)


def test_renders_readable_briefing_with_dates_data_and_named_sources() -> None:
    draft, snapshot = fixture()
    report = render(draft, snapshot)
    assert "## 本周速览" in report
    assert "| 贷款余额 | 750亿元 | 截至6月末，全集团 |" in report
    assert "| 10月20日 | 举行交流活动 | [媒体｜银行融资公告]" in report
    assert "发布于 2026-10-16" in report
    assert "source=search%3Ashanghai" in report
    assert "检索摘要" not in report


def test_rejects_unknown_cross_source_and_missing_original_evidence() -> None:
    draft, snapshot = fixture()
    for id in ["unknown", "s2"]:
        changed = copy.deepcopy(draft)
        changed["sections"][0]["items"][0]["evidence"][0]["source_id"] = id
        with pytest.raises(BriefingError):
            render(changed, snapshot)
    changed = copy.deepcopy(draft)
    changed["sections"][0]["items"][0]["source_ids"] = ["s1", "s2"]
    changed["sections"][0]["items"][0]["evidence"][0]["source_id"] = "s2"
    with pytest.raises(BriefingError):
        render(changed, snapshot)
    missing = copy.deepcopy(snapshot)
    missing["materials"][0]["content"]["original"]["paragraphs"] = []
    with pytest.raises(BriefingError):
        render(draft, missing)
    schedule = copy.deepcopy(draft)
    schedule["schedule"][0]["quote"] = "10月21日举行交流活动"
    with pytest.raises(BriefingError):
        render(schedule, snapshot)


def test_rejects_unsafe_links_empty_bodies_and_unsupported_fields() -> None:
    draft, snapshot = fixture()
    for url in ["javascript:alert(1)", "https://user:secret@example.org/"]:
        unsafe = copy.deepcopy(snapshot)
        unsafe["materials"][0]["url"] = url
        with pytest.raises(BriefingError):
            render(draft, unsafe)
    empty = copy.deepcopy(draft)
    empty["sections"][0]["items"][0]["body"] = []
    with pytest.raises(BriefingError):
        render(empty, snapshot)
    rated = copy.deepcopy(draft)
    rated["sections"][0]["items"][0]["rating"] = "must read"
    with pytest.raises(DecodeError):
        render(rated, snapshot)


def test_escapes_editorial_and_source_text_instead_of_interpreting_markup() -> None:
    draft, snapshot = fixture()
    draft["sections"][0]["items"][0]["body"] = [
        "<script>x</script> [click](javascript:x) **important**"
    ]
    draft["sections"][0]["items"][0]["data_rows"][0]["scope"] = "2026 | <img>"
    snapshot["materials"][0]["content"]["title"] = "[injected]<img>"
    report = render(draft, snapshot)
    assert "<script>" not in report
    assert "\\[click\\](javascript:x)" in report
    assert "2026 \\| &lt;img&gt;" in report
    assert "\\[injected\\]&lt;img&gt;" in report


def test_full_layout() -> None:
    draft, snapshot = fixture()
    draft["scope_note"] = "仅含已公开材料！!~"
    draft["sections"][0]["items"][0]["source_ids"] = ["s1", "s2"]
    draft["sections"][0]["items"][0]["evidence"].append(
        {"source_id": "s2", "paragraph": 0, "quote": "披露要求"}
    )
    snapshot["materials"][1]["content"]["published_at"] = "2026-10-15T08:00:00Z"
    snapshot["plan"] = {}
    snapshot["plan_id"] = "长三角 plan/1"
    assert render(draft, snapshot) == (
        "# 行业周报\n\n> 2026-10-12 — 2026-10-18\n\n本期记录银行已披露的融资安排。\n\n"
        "## 本周速览\n\n"
        "- [银行披露节水融资安排](<https://example.org/news>)\n"
        "\n## 绿色融资\n\n"
        "### [银行披露节水融资安排](<https://example.org/news>)\n\n"
        "银行发布节水融资安排。\n\n"
        "| 已披露数据 | 数值 | 统计期与范围 |\n| --- | --- | --- |\n"
        "| 贷款余额 | 750亿元 | 截至6月末，全集团 |\n\n"
        "来源：[媒体｜银行融资公告](<https://example.org/news>)（发布于 2026-10-16）；"
        "[example.org｜另一篇公告](<https://example.org/other>)（发布于 2026-10-15）\n\n"
        "## 已公布的后续安排\n\n| 时间 | 安排 | 来源 |\n| --- | --- | --- |\n"
        "| 10月20日 | 举行交流活动 | [媒体｜银行融资公告](<https://example.org/news>) |\n\n"
        "仅含已公开材料！\\!\\~\n\n"
        "---\n\n[查看本专题全部采集材料]"
        "(/sources?view=materials&source=search%3A%E9%95%BF%E4%B8%89%E8%A7%92+plan%2F1)\n"
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d, s: s.__setitem__("week_start", "2026-1-12"), "Invalid report window"),
        (lambda d, s: s.__setitem__("week_start", "2026-10-19"), "Invalid report window"),
        (lambda d, s: s.pop("week_end"), "Missing report window"),
        (lambda d, s: s.__setitem__("week_end", "2026-02-30"), "input is out of range"),
        (lambda d, s: s.__setitem__("week_end", "2026 -10-18"),
         "input contains invalid characters"),
        (lambda d, s: s.pop("plan"), "Missing parent search plan"),
        (lambda d, s: s.__setitem__("materials", {}), "Missing frozen materials"),
        (lambda d, s: s["materials"].append(copy.deepcopy(s["materials"][0])),
         "Duplicate frozen source identity"),
        (lambda d, s: s["materials"][0].__setitem__("url", "not a url"),
         "relative URL without a base"),
        (lambda d, s: s["materials"][0]["content"].pop("title"), "Missing source title"),
        (lambda d, s: d.__setitem__("title", "周" * 201), "Invalid briefing text length"),
        (lambda d, s: d.__setitem__("title", "a\u0000b"), "Invalid briefing text"),
        (lambda d, s: d.__setitem__("sections", []), "Invalid briefing sections"),
        (lambda d, s: d["sections"][0].__setitem__("items", []), "Empty briefing section"),
        (lambda d, s: d["sections"][0]["items"][0].__setitem__("source_ids", ["s1", "s1"]),
         "Duplicate briefing source"),
        (lambda d, s: d["sections"][0]["items"][0]["evidence"][0].__setitem__("paragraph", 9),
         "Unknown original paragraph"),
        (lambda d, s: d["sections"][0]["items"][0]["evidence"][0].__setitem__("quote", "别的"),
         "Evidence does not occur in its cited source paragraph"),
        (lambda d, s: d["sections"][0]["items"][0]["source_ids"].append("s2"),
         "Every item source needs evidence"),
        (lambda d, s: d["sections"][0]["items"][0]["evidence"][0].__setitem__("paragraph", -1),
         "invalid value: integer `-1`, expected usize"),
        (lambda d, s: d.__setitem__("intro", None), "invalid type: null, expected a string"),
        (lambda d, s: d.__setitem__("pad", "x" * 128 * 1024), "Briefing draft is too large"),
    ],
)
def test_refusals_carry_the_established_messages(change: Any, message: str) -> None:
    draft, snapshot = fixture()
    change(draft, snapshot)
    with pytest.raises(ValueError) as error:
        render(draft, snapshot)
    assert str(error.value) == message


def test_md_escapes_image_and_strike_markers() -> None:
    assert briefing.md("![x](y) ~~z~~\n#") == "\\!\\[x\\](y) \\~\\~z\\~\\~ \\#"
