"""Frozen publisher article headers: which header shapes yield a publication date."""

from collections.abc import Callable

import pytest

from isbe.articles.dates import DateCheck, parse_html

type Fixture = Callable[[str], bytes]

MEDIA_HEADERS = [
    ("https://www.sh.chinanews.com.cn/kjjy/2026-10-16/150171.shtml", "chinanews.html", "2026-10-16T17:47:00", "in_range"),
    ("https://m.21jingji.com/article/20260912/herald/a5a9a19490b32fc3c67ef30c7a2a13ac.html", "21jingji.html", "2026-09-12T20:13:00", "out_of_range"),
    ("https://finance.eastmoney.com/a/202610143895482378.html", "eastmoney.html", "2026-10-14T16:12:00", "in_range"),
    ("https://www.jiemian.com/article/11838089.html", "jiemian.html", "2024-10-16T21:23:00", "out_of_range"),
]  # fmt: skip
WRAPPERS = [("<aside>", "</aside>"), ("<nav>", "</nav>"), ("<div hidden>", "</div>"), ("<div class='related-news'>", "</div>")]  # fmt: skip


def text(fixture: Fixture, name: str) -> str:
    return fixture(name).decode("utf-8")


def september(html: str, url: str) -> DateCheck:
    return parse_html(html, url, "2026-09-19", "2026-10-18")


@pytest.mark.parametrize(("url", "name", "expected", "status"), MEDIA_HEADERS)
def test_frozen_main_article_headers_exclude_body_events_and_related_dates(
    fixture: Fixture, url: str, name: str, expected: str, status: str
) -> None:
    html = (
        text(fixture, name)
        + "<p>活动将于2026年10月20日举行。</p><aside><span>2026-10-18 12:00</span></aside>"
        "<script>var publish_time='2020-01-01';</script>"
        "<div class='related-news'>发布时间：2026-10-18</div><div class='recommend'>发布时间：2026-10-17</div>"
    )
    result = september(html, url)
    assert (result.date_status, result.published_at) == (status, expected)
    assert len(result.date_evidence) == 1 and result.date_evidence[0].field.startswith("dom:")


@pytest.mark.parametrize(("url", "name", "expected", "status"), MEDIA_HEADERS)
def test_media_headers_need_known_article_scope_and_preserve_conflicts(
    fixture: Fixture, url: str, name: str, expected: str, status: str
) -> None:
    header = text(fixture, name)
    for open_, close in WRAPPERS:
        assert september(open_ + header + close, url).date_status == "unknown"
    assert september(header, "https://unknown.example/article/a.html").date_status == "unknown"
    assert september(header + header, url).date_status == "unknown"
    updated = (
        header.replace("2026年10月16日", "更新于2026年10月16日")
        .replace("2026-09-12 20:13", "更新于2026-09-12 20:13")
        .replace("2026年10月14日", "更新于2026年10月14日")
        .replace("2024/10/16", "更新于2024/10/16")
    )
    assert september(updated, url).date_status == "unknown"
    conflict = september(f"<head><meta name='publishdate' content='2026-10-01'></head>{header}", url)
    assert conflict.date_status == "conflict" and conflict.published_at is None


def test_eastmoney_update_label_discards_the_header(fixture: Fixture) -> None:
    url, name, _, _ = MEDIA_HEADERS[2]
    header = text(fixture, name).replace('<div class=" item">', '<b>更新时间：</b><div class=" item">')
    assert september(header, url).date_status == "unknown"


def test_jiemian_mobile_explicit_unix_publication_is_scoped_and_traceable(fixture: Fixture) -> None:
    header = text(fixture, "jiemian_mobile.html")
    url = "https://m.jiemian.com/article/15183381.html"

    def parse(html: str) -> DateCheck:
        return parse_html(html, url, "2026-10-13", "2026-10-13")

    result = parse(header)
    assert (result.date_status, result.published_at) == ("in_range", "2026-10-13T11:33:32+00:00")
    assert len(result.date_evidence) == 1
    assert "data-article-publish-time=1791891212;unix-seconds" in result.date_evidence[0].field
    assert result.updated_at is None
    # The original formatter follows each reader's timezone; no fixed +08:00
    # is declared. Keep the UTC day instead of shifting a near-midnight instant.
    boundary = parse(header.replace("1791891212", "1791847800"))
    assert (boundary.date_status, boundary.published_at) == ("out_of_range", "2026-10-12T23:30:00+00:00")
    other_site = parse_html(header, "https://example.com/article/15183381.html", "2026-10-13", "2026-10-13")
    assert other_site.date_status == "unknown"
    for value in ["", "1791891212000", "1791891212.0", "+1791891212", " 1791891212", "179189121x", "0000000000", "4102444800"]:
        assert parse(header.replace("1791891212", value)).date_status == "unknown", value
    for cls in ["article-main", "article-detail", "article-title", "info"]:
        assert parse(header.replace(f'class="{cls}"', 'class="other"')).date_status == "unknown", cls
    for open_, close in [("<div class='related-news'>", "</div>"), ("<div hidden>", "</div>"), ("<nav>", "</nav>")]:
        assert parse(open_ + header + close).date_status == "unknown"
    assert parse(header.replace("data-article-publish-time", "data-article-update-time")).date_status == "unknown"
    assert parse(header.replace('<div class="info"', '<div hidden class="info"')).date_status == "unknown"
    updated = header.replace('<div class="user-title"></div>', '<div class="user-title">更新时间</div>')
    assert parse(updated).date_status == "unknown"
    assert parse(header + header).date_status == "unknown"
    other = header.replace("1791891212", "1791977612")
    conflict = parse(header + other)
    assert conflict.date_status == "conflict" and conflict.published_at is None
    noise = f"{header}<div class='related-news'>{other}</div><div data-article-publish-time='1791977612'></div>"
    assert parse(noise).published_at == result.published_at
    meta = parse(f"<head><meta name='publishdate' content='2026-10-12'></head>{header}")
    assert meta.date_status == "conflict"


SAMPLES = [
    ("https://www.yicai.com/brief/103397868.html", "yicai_brief.html", "2026-10-18T14:53:00", "in_range",
     "dom:yicai:brief.day-and-time", "m-on", "【", "2026/10/18"),
    ("http://www.chinagongyi.com.cn/esg/18912.html", "chinagongyi.html", "2025-10-20T09:10:23", "out_of_range",
     "dom:chinagongyi:d_picnews_bt.d_pnfubt", "d_picnews_bt", "d_pn_con", "2025-10-20 09:10:23"),
    ("https://paper.xinmin.cn/html/xmwb/2026-08-28/4/246188.html", "xinmin.html", "2026-08-28", "out_of_range",
     "dom:xinmin:edition_date", "dzb-enter-info-box", "dzb-title-box", "2026-08-28"),
    ("http://bond.10jqka.com.cn/20261015/c680742590.shtml", "bond_10jqka.html", "2026-10-15T18:31:48", "in_range",
     "dom:10jqka:bond.article-header.time", "@container", "h1", "2026-10-15 18:31:48"),
]  # fmt: skip


@pytest.mark.parametrize(("url", "name", "expected", "status", "field", "root", "title", "raw"), SAMPLES)
def test_four_frozen_article_headers_keep_publication_and_edition_semantics(
    fixture: Fixture, url: str, name: str, expected: str, status: str, field: str, root: str, title: str, raw: str
) -> None:
    header = text(fixture, name)
    result = september(header, url)
    assert (result.published_at, result.date_status) == (expected, status)
    assert [e.field for e in result.date_evidence] == [field]
    assert result.updated_at is None
    noise = f"<header>2026年10月19日</header>{header}<p>活动时间：2026-10-20 09:00</p><div class='related-news'>发布时间：2026-10-18</div>"
    assert september(noise, url).published_at == result.published_at
    for open_, close in [("<div class='related-news'>", "</div>"), ("<div hidden>", "</div>"), ("<aside>", "</aside>")]:
        assert september(open_ + header + close, url).date_status == "unknown"
    for changed in [
        header.replace(root, "unmatched-root"),
        header.replace(title, "unmatched-title"),
        header.replace(raw, f"更新于{raw}"),
        header.replace(raw, f"活动时间：{raw}"),
        header + header,
    ]:
        assert september(changed, url).date_status == "unknown"
    assert september(header, "https://unknown.example/article.html").date_status == "unknown"
    conflict = september(f"<head><meta name='publishdate' content='2020-01-01'></head>{header}", url)
    assert conflict.date_status == "conflict" and conflict.published_at is None


def test_yicai_and_bond_headers_refuse_near_misses(fixture: Fixture) -> None:
    yicai = text(fixture, "yicai_brief.html")
    for changed in [
        yicai.replace("14:53", "会议时间：14:53"),
        yicai.replace("14:53", "24:53"),
        yicai.replace("<h1>2026/10/18</h1>", "<h1>2026/10/18</h1><h1>2026/10/17</h1>"),
    ]:
        assert september(changed, SAMPLES[0][0]).date_status == "unknown"
    bond = text(fixture, "bond_10jqka.html")
    assert september(bond, "http://bond.10jqka.com.cn/topic/overview.shtml").date_status == "unknown"


@pytest.mark.parametrize("label", ["活动时间：", "活动时间： ", "会议日期 ", "Event time: ", "报名截止： "])
def test_jingji_byline_cannot_discard_an_event_date_label(fixture: Fixture, label: str) -> None:
    url, name, _, _ = MEDIA_HEADERS[1]
    header = text(fixture, name)
    event = header.replace("卢陶然 2026-09-12 20:13", f"{label}2026-10-20 09:00")
    result = september(event, url)
    assert (result.date_status, result.published_at, result.date_evidence) == ("unknown", None, [])
    assert september(header, url).published_at == "2026-09-12T20:13:00"
