"""Original-page publication dates; the frozen publisher headers are in test_articles_date_headers.py."""

import time
from collections.abc import Callable

import pytest

from isbe.articles import dates
from isbe.articles.charset import decode_html
from isbe.articles.dates import MAX_EVIDENCE, DateCheck, parse_html


def check(html: str) -> DateCheck:
    return parse_html(html, "https://news.example/a", "2026-10-01", "2026-10-16")


def test_explicit_dates_and_timezone_preserve_calendar_day() -> None:
    result = check(
        '<head><meta property="article:published_time" content="2026-10-16T23:30:00-07:00">'
        '<meta property="article:modified_time" content="2026-10-18T12:00:00+08:00"></head>'
    )
    assert result.date_status == "in_range"
    assert result.published_at is not None and result.published_at.endswith("-07:00")
    assert result.updated_at is not None and result.updated_at.startswith("2026-10-18")
    meta = check('<head><meta name="pubdate" content="2026-10-12T08:00:00"></head>')
    assert meta.published_at == "2026-10-12T08:00:00"
    assert check("<div>发布时间：<span>2026年10月05日</span></div>").published_at == "2026-10-05"
    assert check("<div>发布日期：2024-10-05 12:05:00</div>").date_status == "out_of_range"
    assert check("<div>发布时间：[2026-10-08]</div>").published_at == "2026-10-08"


def test_declared_chinese_charsets_decode_without_guessing_or_replacement(
    fixture: Callable[[str], bytes],
) -> None:
    html = decode_html(fixture("cnr.gb2312.html"), "text/html")
    assert check(html).published_at == "2026-10-16"


@pytest.mark.parametrize("cls", ["related", "related-news", "recommend", "recommendations", "recommend-list"])
def test_recommendation_labels_are_not_article_dates(cls: str) -> None:
    related = f"<div class='{cls}'><div>发布时间：2026-10-18</div><div>更新时间：2026-10-18</div></div>"
    result = check(related)
    assert result.date_status == "unknown" and result.date_evidence == []
    result = check(f"<head><meta name='publishdate' content='2026-10-12'></head>{related}")
    assert result.published_at == "2026-10-12" and len(result.date_evidence) == 1


def test_conflict_is_retained_and_updates_never_become_publication() -> None:
    result = check('<head><meta name="pubdate" content="2026-10-12"></head><p>发布日期：2026-10-13</p>')
    assert result.date_status == "conflict" and result.published_at is None
    assert len(result.date_evidence) == 2
    result = check(
        '<head><meta property="article:modified_time" content="2026-10-12"></head>'
        '<a href="/20261012/a">Article</a>'
    )
    assert result.date_status == "unknown" and result.updated_at is not None
    hidden = check("<footer>发布日期：2026-10-12</footer><p hidden>发布日期：2026-10-12</p>")
    assert hidden.date_status == "unknown"


def test_sina_publication_metadata_conflict_does_not_promote_update_time() -> None:
    result = parse_html(
        """<head>
<meta property="bytedance:published_time" content="2026-09-20T18:08:26+08:00" />
<meta property="bytedance:updated_time" content="2026-09-21T09:35:13+08:00" />
<meta name="weibo: article:create_at" content="2026-09-20 18:08:26" />
<meta name="weibo: article:update_at" content="2026-09-21 09:35:13" />
<meta property="article:published_time" content="2026-09-21T09:35:15+08:00" />
</head>""",
        "https://finance.sina.com.cn/stock/relnews/cn/2026-09-20/doc-inisnnky6627542.shtml",
        "2026-09-19",
        "2026-10-18",
    )
    assert result.date_status == "conflict" and result.published_at is None
    assert result.updated_at == "2026-09-21T09:35:13+08:00"
    assert sum(e.kind == "published" for e in result.date_evidence) == 3
    for name in ["bytedance:updated_time", "weibo: article:update_at"]:
        result = check(f'<head><meta name="{name}" content="2026-10-12 09:35:13" /></head>')
        assert result.date_status == "unknown" and result.published_at is None
        assert result.updated_at == "2026-10-12T09:35:13"
        assert all(e.kind == "updated" for e in result.date_evidence)


def ld(json: str) -> str:
    return f'<script type="application/ld+json">{json}</script>'


def test_jsonld_selects_main_article_not_recommendations() -> None:
    result = check(
        ld(
            '{"@graph":[{"@type":"NewsArticle","url":"https://news.example/a","datePublished":"2026-10-11"},'
            '{"@type":"NewsArticle","url":"https://news.example/b","datePublished":"2020-01-01"},'
            '{"@type":"Organization","datePublished":"2000-01-01"}]}'
        )
    )
    assert result.published_at == "2026-10-11" and len(result.date_evidence) == 1
    two = '[{"@type":"Article","datePublished":"2026-10-11"},{"@type":"Article","datePublished":"2026-10-12"}]'
    assert check(ld(two)).date_status == "unknown"
    other = '{"@type":"Article","url":"https://news.example/b","datePublished":"2026-10-11"}'
    assert check(ld(other)).date_status == "unknown"
    assert check(ld('{"@type":"Article","datePublished":"2026-10-11"}')).date_status == "in_range"


def test_jsonld_reading_is_strict() -> None:
    def nested(depth: int) -> str:
        return ld('{"@type":"Article","datePublished":"2026-10-11","x":' + "[" * depth + "]" * depth + "}")

    # JSON-LD nesting is limited to 128 levels (the object counts as one); lone surrogates are refused.
    assert check(nested(126)).date_status == "in_range"
    assert check(nested(127)).date_status == "unknown"
    lone = '{"@type":"Article","datePublished":"2026-10-11","x":"\\ud800"}'
    assert check(ld(lone)).date_status == "unknown"
    typed = '{"@type":"http://schema.org/NewsArticle","mainEntityOfPage":{"@id":"/a#top"},"datePublished":"Mon, 12 Oct 2026 08:00:00 +0800"}'
    assert check(ld(typed)).published_at == "2026-10-12T08:00:00+08:00"


def test_limits_do_not_silently_turn_partial_evidence_into_verified_dates() -> None:
    result = check("<head>" + '<meta name="pubdate" content="2026-10-12">' * 33 + "</head>")
    assert result.date_status == "unknown"
    assert result.date_error == "original-page date evidence exceeded parsing limits"
    assert len(result.date_evidence) == MAX_EVIDENCE
    assert check("<p>2026-10-12</p><p style='display: none'>发布时间：2026-10-12</p>").date_status == "unknown"
    two = "<p>发布时间：2026-10-12 12:00:00</p><p>发布时间：2026-10-12 13:00:00</p>"
    assert check(two).date_status == "conflict"


def test_invalid_ranges_are_reported() -> None:
    assert parse_html("", "https://news.example/a", "2026-10-1x", "2026-10-16").date_error == "invalid date range"
    reversed_range = parse_html("", "https://news.example/a", "2026-10-17", "2026-10-16")
    assert reversed_range.date_error == "invalid date range or oversized HTML"
    assert check("x" * (dates.MAX_BODY + 1)).date_error == "invalid date range or oversized HTML"
    assert DateCheck().to_json() == {
        "published_at": None,
        "updated_at": None,
        "date_status": "unknown",
        "date_evidence": [],
        "date_error": None,
    }


def test_omitted_end_tags_do_not_hide_dates() -> None:
    # An unclosed <head> (or <p hidden>) must not stay open over the whole page.
    assert check("<head><title>T</title><p>发布时间：2026-10-12</p>").published_at == "2026-10-12"
    assert check("<p hidden>隐藏<p>发布时间：2026-10-12").published_at == "2026-10-12"
    assert check("<ul><li class='related'>相关<li>发布时间：2026-10-12</ul>").published_at == "2026-10-12"
    # Metadata in an implied head counts, metadata in the body does not.
    assert check("<meta name='pubdate' content='2026-10-12'><p>x</p>").published_at == "2026-10-12"
    assert check("<p>x</p><meta name='pubdate' content='2026-10-12'>").date_status == "unknown"


def test_size_cap_counts_characters() -> None:
    # 400k CJK characters: 1.2 MB as UTF-8, but within the 1 MiB a GBK page may have.
    html = "<p>发布时间：2026-10-12</p><p>" + "汉" * 400_000 + "</p>"
    assert len(html.encode("gbk")) <= dates.MAX_BODY < len(html.encode("utf-8"))
    result = check(html)
    assert (result.date_error, result.published_at) == (None, "2026-10-12")


def test_label_scan_stops_at_the_evidence_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    real = dates.parse_date

    def counted(value: str):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return real(value)

    monkeypatch.setattr(dates, "parse_date", counted)
    html = "<p>" + "发布时间：2026-10-12 " * 40_000 + "</p>"  # ~800k characters, 40k labels
    started = time.monotonic()
    result = check(html)
    elapsed = time.monotonic() - started
    assert result.date_error == "original-page date evidence exceeded parsing limits"
    assert len(result.date_evidence) == MAX_EVIDENCE
    # Without the stop every label was tried at every prefix length (about a million calls).
    assert calls <= (MAX_EVIDENCE + 1) * (dates.LABEL_WINDOW + 1) * 2
    assert elapsed < 5


def test_label_scan_is_bounded_without_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    # Date-shaped text that never parses: the cap is never reached, the try budget ends it.
    calls = 0
    real = dates.parse_date

    def counted(value: str):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return real(value)

    monkeypatch.setattr(dates, "parse_date", counted)
    html = "<p>" + ("发布时间" + "1" * 35) * 26_000 + "</p>"  # 1 MiB of labels and digits
    started = time.monotonic()
    result = check(html)
    assert time.monotonic() - started < 5
    assert result.date_error == "original-page date evidence exceeded parsing limits"
    assert calls == dates.MAX_LABEL_TRIES
    # A page with a handful of such labels is still read.
    few = "<p>发布时间：" + "1" * 35 + "</p><p>发布时间：2026-10-12</p>"
    assert check(few).published_at == "2026-10-12"


def test_dates_agree_by_instant_across_offsets() -> None:
    # 01:00 in Beijing is 17:00 UTC the day before: the same publication, not a conflict.
    page = (
        "<head><meta property='article:published_time' content='2026-10-12T01:00:00+08:00'></head>"
        + ld('{"@type":"NewsArticle","datePublished":"2026-10-11T17:00:00Z"}')
    )
    result = parse_html(page, "https://news.example/a", "2026-10-12", "2026-10-12")
    assert result.date_status == "in_range"
    assert result.published_at == "2026-10-11T17:00:00+00:00"  # the last longest
    # A naive date is the source's local day: it agrees with an instant in the stated offset.
    labelled = page + "<p>发布日期：2026-10-12</p>"
    assert parse_html(labelled, "https://news.example/a", "2026-10-12", "2026-10-12").date_status == "in_range"
    # Different instants still conflict, and a naive day no stated offset explains does too.
    other = page.replace("17:00:00Z", "18:00:00Z")
    assert check(other).date_status == "conflict"
    utc_only = ld('{"@type":"NewsArticle","datePublished":"2026-10-11T17:00:00Z"}') + "<p>发布日期：2026-10-12</p>"
    assert check(utc_only).date_status == "conflict"
    # Updates use the same rule.
    updated = (
        "<head><meta name='lastmod' content='2026-10-12T04:00:00+08:00'>"
        "<meta name='datemodified' content='2026-10-11T20:00:00Z'></head>"
    )
    assert check(updated).updated_at == "2026-10-11T20:00:00+00:00"
