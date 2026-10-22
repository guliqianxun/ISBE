"""Media grouping and rendering, plus the calendar."""

from typing import Any

import pytest
from media_helpers import item

from isbe.reports import media_dates as dates
from isbe.reports.media_render import Group, Window, entry, group, md, render, source


def window() -> Window:
    return Window(
        start=dates.date("2020-10-12"),
        end=dates.date("2020-10-18"),
        collection_start=dates.date("2020-09-20"),
        collection_end=dates.date("2020-10-19"),
    )


@pytest.mark.parametrize(
    ("value", "status", "expected"),
    [
        ("2020-10-12", "out_of_range", Group.WEEK),
        ("2020-10-18T14:53:00", "in_range", Group.WEEK),
        ("2020-10-16T17:47:00.123", "in_range", Group.WEEK),
        ("2020-10-11T23:59:59", "in_range", Group.BACKGROUND),
        ("2020-10-18T99:99:99", "in_range", Group.UNKNOWN),
        ("2020-10-18T15:59:59Z", "in_range", Group.WEEK),
        ("2020-10-18T16:00:00Z", "in_range", Group.NEWER),
        ("2020-10-11", "in_range", Group.BACKGROUND),
        ("2020-09-19", "in_range", Group.OUTSIDE),
        ("2020-10-16", "unknown", Group.UNKNOWN),
        ("2020-10-16", "conflict", Group.CONFLICT),
        (None, "in_range", Group.UNKNOWN),
    ],
)
def test_groups_by_publication_in_shanghai_and_preserves_uncertain_materials(
    value: str | None, status: str, expected: Group
) -> None:
    assert group(item("x", value, status), window()) == expected


def test_renders_every_candidate_once_without_promoting_snippets_or_active_markup() -> None:
    items = [
        item("week", "2020-10-16", "in_range"),
        item("earlier", "2020-09-25", "in_range"),
        item("newer", "2020-10-19", "in_range"),
        item("unknown", None, "unknown"),
        item("conflict", None, "conflict"),
        item("old", "2020-09-01", "out_of_range"),
    ]
    items[0]["content"]["title"] = "[click](javascript:x) <script>bad</script>"
    items[0]["content"]["summary"] = "</details><img src=x onerror=bad>"
    text, counts = render({"plan": {"config": {"topic": "测试"}}, "materials": items}, window())
    assert counts["weekly"] == 1
    assert text.count("### ") == 6
    assert text.count("### [") == 0
    assert text.count("**检索摘要：**") == 6
    assert "<script>" not in text
    assert "<img" not in text
    assert "&lt;/details&gt;" in text
    assert "</details>" in text


EXPECTED = """# 测试 \\#1｜媒体周报

> 覆盖窗口：2020-10-12 → 2020-10-18（周一至周日）

本期收录 2 条发布日期在本周的候选报道，按日期整理，尚未经人工选稿。

检索摘要可能截断或混入页面其他内容，请以链接原文为准。事件发生日期可能与报道发布日期不同。

## TL;DR

- 本周：2 条候选报道。
- 检索回看窗口：2020-09-20 → 2020-10-19；周前回看 0 条，周后新增 0 条。
- 日期待核对 1 条、日期冲突 0 条，保留在文末供审阅。

## 本周报道（2）

### 2020-10-16 · [澎湃新闻｜标题 b](<https://www.thepaper.cn/b>)

**检索摘要：** 搜索返回的文字

### 2020-10-12 · [news.example｜标题 a](<https://news.example/a>)

检索未返回摘要，可打开原文查看。

## 本周之前 · 采集窗口回看（0）

本期暂无材料。

## 本周之后 · 新增报道（0）

本期暂无材料。

## 日期待核对（1）

<details>
<summary>展开完整材料</summary>

### 日期待核对 · 标题 c

**检索摘要：** 搜索返回的文字

</details>

## 日期冲突（0）

本期暂无材料。

## 采集窗口外材料（0）

本期暂无材料。

---

本期为冻结版本，后续补充日期或重新检索会生成新版本。相同事件的不同报道仍分别保留。
"""


def test_render_layout_and_order() -> None:
    a = item("a", "2020-10-12", "in_range")
    a["content"]["summary"] = " 　"
    b = item("b", "2020-10-16T09:00:00+08:00", "in_range")
    b["url"] = "https://www.thepaper.cn/b"
    c: dict[str, Any] = item("c", None, "unknown")
    c["url"] = "javascript:alert(1)"
    text, counts = render({"plan": {"config": {"topic": "测试 #1"}}, "materials": [a, c, b]}, window())
    assert text == EXPECTED
    assert counts == {"weekly": 2, "background": 0, "newer": 0, "unknown": 1, "conflict": 0, "outside": 0}


def test_entries_escape_and_order_ties_by_id() -> None:
    same = [item(id, "2020-10-14", "in_range") for id in ("z", "m", "a")]
    text, _ = render({"materials": [*same, item("n", None, "in_range")]}, window())
    assert text.startswith("# 媒体资料｜媒体周报")
    assert text.index("标题 a") < text.index("标题 m") < text.index("标题 z")
    assert md("a\nb\t<c>&|*_`") == "a b &lt;c&gt;&amp;\\|\\*\\_\\`"
    assert md("!~") == "!~"
    assert source("www.21jingji.com") == "21财经" and source("www.www.cnr.cn") == "www.www.cnr.cn"
    credentials = item("x", "2020-10-14", "in_range")
    credentials["url"] = "https://user:pw@news.example/x"
    assert entry(credentials, Group.WEEK).startswith("### 2020-10-14 · 标题 x\n\n")
    markup = item("y", "2020-10-14", "in_range")
    markup["url"] = "https://news.example/a>b"
    assert "(<https://news.example/a%3Eb>)" in entry(markup, Group.WEEK)


def test_calendar_matches_reference_values() -> None:
    assert dates.iso_week(dates.date("2020-10-18")) == (2020, 42)
    assert dates.iso_week(dates.date("2021-01-03")) == (2020, 53)
    assert dates.iso_week(dates.date("2024-12-29")) == (2024, 52)
    assert dates.iso_week(dates.date("2025-12-28")) == (2025, 52)
    assert dates.weekday(dates.date("2020-10-18")) == 6
    assert dates.show(dates.week_start(dates.date("2020-03-01"))) == "2020-02-24"
    assert dates.show(dates.date("+12345-01-01")) == "+12345-01-01"
    assert dates.show(dates.date("0000-01-02")) == "0000-01-02"
    for bad in ("2020-1-01", " 2020-01-01", "2020-02-30", "2020-01-01 "):
        with pytest.raises(ValueError):
            dates.date(bad)
    published = {
        "2020-10-18": "2020-10-18",
        "2020-10-17T16:00:00Z": "2020-10-18",
        "2020-10-18T23:30:00-02:00": "2020-10-19",
        "1988-06-01T15:30:00Z": "1988-06-02",  # Shanghai kept summer time until 1991
        "1988-06-01T16:30:00+01:00": "1988-06-02",
        "2020-10-18 08:00:00+08:00": "2020-10-18",
        "2020-10-18T08:00:00": "2020-10-18",
        "2020-10-18T08:00:60.1234567891": "2020-10-18",
        " 2020-10-18T 8:00:00": "2020-10-18",
    }
    for value, day in published.items():
        found = dates.publication({"content": {"published_at": value}})
        assert found is not None and dates.show(found) == day, value
    for value in ("2020-10-18T08:00:00.", "2020-10-18T08:00", "2020-10-18T08:00:00Z+", "2020/10/18"):
        assert dates.publication({"content": {"published_at": value}}) is None, value
