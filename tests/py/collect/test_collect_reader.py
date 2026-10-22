"""Source reading: feeds and listing pages, discovery, HTTP limits, GBK feeds, public-only
fetches and omitted end tags."""

import re
from http.server import BaseHTTPRequestHandler

import pytest

from isbe.core import netguard, validators
from isbe.sources import pages, reader
from isbe.sources.reader import (
    Kind,
    ReaderError,
    dated_path,
    discover_html,
    page_entries,
    parse_feed,
    resolve,
)

BASE = "https://example.com/news/index.html"


def test_url_validation() -> None:
    assert reader.validate_url("HTTPS://Example.COM:443/news") == "https://example.com/news"
    for value in [
        "", "file:///tmp/a", "javascript:alert(1)", "https://user:pass@example.com/", "https://@example.com/",
        "https://example.com/#part", "https://example.com/a b", "http:example.com", "http://[bad]/",
        "https://example.com\\other/",
    ]:
        with pytest.raises(validators.Invalid):
            reader.validate_url(value)
    assert resolve(BASE, "https://@example.com/news", True) is None
    assert resolve(BASE, "//user:secret@example.com/news", True) is None
    assert resolve(BASE, "https://example.com/\nnews", True) is None
    assert resolve(BASE, " ../a/b.html#frag ", True) == "https://example.com/a/b.html"
    assert resolve(BASE, "https://example.com:8443/a", True) is None
    assert resolve(BASE, "https://example.com:8443/a", False) == "https://example.com:8443/a"


def test_feed_dates_links_and_plain_text() -> None:
    xml = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>News</title><id>test</id>
        <entry><id>1</id><title type="html">A &amp;amp; B &lt;b&gt;release&lt;/b&gt;</title><link href="/article"/>
        <published>2025-01-02T03:04:05Z</published><updated>2025-02-03T04:05:06Z</updated><summary type="html">&lt;p&gt;New data&lt;/p&gt;</summary></entry>
        <entry><id>2</id><title>Undated item</title><link href="https://other.example/article"/></entry>
        <entry><id>3</id><title>Updated only</title><link href="/updated"/><updated>2025-02-03T04:05:06Z</updated></entry></feed>"""
    entries, truncated = parse_feed(xml, BASE, 20)
    assert not truncated
    assert len(entries) == 3
    assert entries[0].url == "https://example.com/article"
    assert entries[0].title == "A & B release"
    assert entries[0].summary == "New data"
    assert entries[0].published_at == "2025-01-02T03:04:05Z"
    assert entries[0].updated_at == "2025-02-03T04:05:06Z"
    assert entries[1].published_at is None and entries[1].updated_at is None
    assert entries[2].published_at is None and entries[2].updated_at is not None
    assert parse_feed(xml, BASE, 1)[1]
    assert not parse_feed(xml, BASE, 3)[1]
    with pytest.raises(ReaderError, match="^source is not a valid RSS or Atom feed$"):
        parse_feed(b"<html>blocked</html>", BASE, 10)


def test_rss_keeps_undated_items_without_inventing_timestamps() -> None:
    xml = b"""<rss version="2.0"><channel><title>News</title><link>https://example.com/</link><description>News</description>
        <item><title>First policy</title><link>/policy</link><pubDate>Thu, 02 Jan 2025 03:04:05 +0000</pubDate></item>
        <item><title>Unknown date</title><link>/unknown</link></item>
        <item><title>Invalid date</title><link>/invalid</link><pubDate>tomorrow</pubDate></item>
        <item><title>Explicit update</title><link>/same</link><pubDate>Thu, 02 Jan 2025 03:04:05 +0000</pubDate><updated>2025-01-02T03:04:05Z</updated></item>
        <item><title>Invalid update</title><link>/bad-update</link><pubDate>Thu, 02 Jan 2025 03:04:05 +0000</pubDate><updated>tomorrow</updated></item></channel></rss>"""
    entries, truncated = parse_feed(xml, BASE, 20)
    assert not truncated
    assert len(entries) == 5
    assert entries[0].published_at == "2025-01-02T03:04:05Z"
    assert all(item.updated_at is None for item in entries[:3])
    assert all(item.published_at is None for item in entries[1:3])
    assert entries[3].updated_at == "2025-01-02T03:04:05Z"
    assert entries[4].updated_at is None


def test_pages_ignore_scripts_hidden_navigation_and_outside_links() -> None:
    html = """<title>政策 &amp; 动态</title><script>document.write('<a href="/20261018/fake.html">伪造文章标题</a>')</script>
        <style>.a{content:'<a href="/20261018/css.html">伪造样式链接</a>'}</style>
        <nav><a href="/20261018/nav.html">导航链接测试</a></nav><div hidden><a href="/20261018/hide.html">隐藏链接测试</a></div>
        <div style="display: none"><a href="/20261018/css-hide.html">样式隐藏测试</a></div>
        <a href="../2026/10/18/first.html#part">上海 <b>ESG</b> &amp; 绿色金融</a>
        <a href="/2026/10/18/first.html">重复新闻标题</a>
        <a href="https://outside.example/20261018/item.html">外部来源新闻</a>
        <a href="http://example.com/20261018/item.html">不同协议新闻</a>
        <a href="/2026-10-18/second.shtml">另一项政策发布</a><a href="/20261018/image.png">资源文件测试</a>
        <a href="/20261332/bad.html">日期无效链接</a><a href="/undated/policy.html">明确配置收录</a>"""
    page = pages.parse(html.encode())
    entries, truncated = page_entries(page, BASE, "", 20)
    assert not truncated
    assert len(entries) == 2
    assert entries[0].url == "https://example.com/2026/10/18/first.html"
    assert entries[0].title == "上海 ESG & 绿色金融"
    assert all(e.published_at is None and e.updated_at is None and e.summary is None for e in entries)
    assert page_entries(page, BASE, "", 1)[1]
    assert not page_entries(page, BASE, "", 2)[1]
    assert len(page_entries(page, BASE, "/undated/", 20)[0]) == 1
    assert page.title == "政策 & 动态"


@pytest.mark.parametrize("level", range(1, 7))
def test_listing_titles_prefer_visible_headings_without_inventing_dates(level: int) -> None:
    html = f"""<a href="/20261018/article.html">
        <h2 hidden>隐藏标题不可采用</h2><h3> </h3>
        <h{level}>上海 <em>ESG <span>绿色金融</span></em><span hidden>隐藏文字</span></h{level}>
        <p>摘要不应并入标题</p><span>2026-10-18</span><span>浏览量 123</span>
        <h4>后续小标题不应并入标题</h4>
        </a><a href="/20261018/plain.html">没有标题标签 <span>保留原文字</span></a>
        <a href="https://outside.example/20261018/article.html"><h2>外站标题不可收录</h2></a>"""
    entries, truncated = page_entries(pages.parse(html.encode()), BASE, "", 20)
    assert not truncated
    assert [e.title for e in entries] == ["上海 ESG 绿色金融", "没有标题标签 保留原文字"]
    assert all(e.published_at is None and e.updated_at is None for e in entries)


def test_listing_titles_skip_navigation_words() -> None:
    page = pages.parse(
        """<a href="/20261018/basic.html">基本信息</a>
        <a href="/20261018/bid.html"><h2>招标信息</h2><span>2026-10-18</span></a>
        <a href="/20261018/notice.html"><h2>关于绿色金融服务招标信息的公告</h2></a>""".encode()
    )
    entries = page_entries(page, BASE, "", 20)[0]
    assert [e.title for e in entries] == ["关于绿色金融服务招标信息的公告"]


def test_listing_titles_use_explicit_class_or_attribute_with_visible_anchor_text() -> None:
    page = pages.parse("""<a href="/20261018/saif.html" title="备用标题属性">
        <span class="date">2026-10-18</span><p class="title hidden" hidden>隐藏标题</p>
        <p class="article title">绿色金融 <em>研究<span>报告发布</span></em></p><p class="desc">摘要不应并入标题</p></a>
        <a href="/20261018/iigf.html" title="中国绿色金融发展完整标题">中国绿色金融发展…<span>2026-10-18</span></a>
        <a href="/20261018/heading.html" title="备用标题属性"><h2>可见标题优先采用</h2>2026-10-18</a>
        <a href="/20261018/unchanged.html"><span class="subtitle">不是独立 title 类</span> 保留原文字</a>
        <a href="/20261018/icon.html" title="图标链接不应该收录"><img src="icon.png"></a>
        <a href="/20261018/hidden-text.html" title="隐藏正文不应该收录"><span hidden>隐藏正文内容</span></a>
        <a href="/20261018/short.html" title="正文太短不应该收录">↗</a>
        <a href="/20261018/navigation.html" title="导航链接不应该收录">基本信息</a>
        <a href="/20261018/hidden.html" hidden title="隐藏链接不应该收录">隐藏链接内容</a>
        <a href="/20261018/bad-attribute.html" title="更多">保留合格的正文标题</a>
        <a href="/20261018/tooltip.html" title="点击查看详情">真正的新闻标题</a>
        <a href="/20261018/wrong-prefix.html" title="另一篇新闻完整标题">保留原有新闻…</a>
        <a href="/20261018/date.html" title="绿色金融研究报告">绿色金融研究报告<span>2026-10-18</span></a>
        <a href="/20261018/ascii-ellipsis.html" title="绿色建筑项目实践案例">绿色建筑项目...<span>2026-10-18</span></a>""".encode())
    entries, truncated = page_entries(page, BASE, "", 20)
    assert not truncated
    assert [e.title for e in entries] == [
        "绿色金融 研究报告发布", "中国绿色金融发展完整标题", "可见标题优先采用", "不是独立 title 类 保留原文字",
        "保留合格的正文标题", "真正的新闻标题", "保留原有新闻…", "绿色金融研究报告", "绿色建筑项目实践案例",
    ]
    assert all(e.published_at is None and e.updated_at is None for e in entries)


def test_discovery_stays_in_origin_and_prioritizes_explicit_feeds() -> None:
    page = pages.parse(b"""<title>Policy news</title>
        <link rel="alternate" type="application/rss+xml" title="RSS &amp; updates" href="../rss.xml"/>
        <link rel="alternate" type="application/atom+xml" href="//outside.example/feed"/>
        <link rel="alternate" type="application/rss+xml" href="https://example.com:8443/rss"/>
        <a href="/20261018/news.html">Policy released today</a>
        <a href="/policy/index.html">Policy notices</a><a href="/events/">Event updates</a>""")
    candidates = discover_html(page, BASE)
    assert [(c.url, c.name, c.kind) for c in candidates] == [
        ("https://example.com/rss.xml", "RSS & updates", Kind.RSS),
        (BASE, "Policy news", Kind.PAGE),
        ("https://example.com/policy/index.html", "Policy notices", Kind.PAGE),
        ("https://example.com/events/", "Event updates", Kind.PAGE),
    ]


def test_input_limits_and_invalid_dates() -> None:
    with pytest.raises(pages.PageError, match="^HTML exceeds 2 MiB$"):
        pages.parse(b" " * (pages.MAX_BODY + 1))
    with pytest.raises(pages.PageError, match="^source HTML must be UTF-8$") as failure:
        pages.parse(b"\xff")
    assert str(failure.value.__cause__) == "invalid utf-8 sequence of 1 bytes from index 0"
    with pytest.raises(pages.PageError, match="^HTML nesting exceeds 128 levels$"):
        pages.parse(b"<div>" * 129)
    pages.parse(b"<div>" * 128)
    assert not dated_path("/20260230/news.html")
    assert not dated_path("/1232026101800/news.html")
    assert dated_path("/t20261018_123.shtml")
    assert dated_path("/2026/10/18/a") and not dated_path("/2026/10-18/a")
    html = "".join(f"<link rel='alternate' type='application/rss+xml' href='/rss/{n}'>" for n in range(12))
    assert len(discover_html(pages.parse(html.encode()), BASE)) == 9
    assert pages.plain_text("<div>" * 129 + "x") == ""
    assert pages.plain_text("  A <b>bold</b>\n move ") == "A bold move"


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (200, b"<html>source</html>", None),
        (302, b"", "source returned HTTP 302 Found; redirects are not followed"),
        (204, b"", "source returned HTTP 204 No Content; redirects are not followed"),
        (404, b"gone", "http status: 404"),
        (200, b"x" * (reader.MAX_BODY + 1), "read source body (maximum 2 MiB)"),
    ],
    ids=["ok", "redirect", "no-content", "not-found", "too-large"],
)
def test_http_limits_body_and_does_not_follow_redirects_or_retry(
    serve, local_resolver: netguard.PublicResolver, status: int, body: bytes, error: str | None
) -> None:
    seen: list[str] = []

    def handle(req: BaseHTTPRequestHandler) -> None:
        seen.append(req.path)
        headers = {"Location": "/must-not-follow", "Content-Type": "text/html; charset=utf-8"}
        serve.reply(req, status, body, headers)

    port = serve(handle).rsplit(":", 1)[1]
    url = f"http://source.test:{port}/source"
    if error is None:
        assert reader.fetch(url, resolver=local_resolver) == (url, "text/html; charset=utf-8", body)
    else:
        with pytest.raises(ReaderError, match=f"^{re.escape(error)}$"):
            reader.fetch(url, resolver=local_resolver)
    assert seen == ["/source"]  # no retry, no redirect


def test_read_checks_its_arguments_before_fetching() -> None:
    for max_items, pattern in [(0, ""), (201, ""), (1, "x" * 513), (1, "a\x07")]:
        with pytest.raises(ReaderError, match="^max_items must be 1–200; link_pattern"):
            reader.read("http://127.0.0.1:9/", Kind.PAGE, pattern, max_items)


GBK_FEED = (
    '<?xml encoding="GBK"?><rss version="2.0"><channel><title>气象新闻</title>'
    "<item><title>雷达回波外推</title><link>/a</link><description>&lt;p&gt;降水临近预报&lt;/p&gt;</description>"
    "<pubDate>Thu, 02 Jan 2025 03:04:05 +0000</pubDate><updated>2025-01-02T03:04:05Z</updated></item>"
    "</channel></rss>"
)


@pytest.mark.parametrize(
    ("raw", "content_type"),
    [
        (GBK_FEED.encode("gbk"), "application/rss+xml"),
        (GBK_FEED.replace('<?xml encoding="GBK"?>', "").encode("gbk"), "application/rss+xml; charset=GB2312"),
    ],
    ids=["prolog", "content-type"],
)
def test_reader_reads_gbk_rss_feeds(
    serve, local_resolver: netguard.PublicResolver, raw: bytes, content_type: str
) -> None:
    port = serve(lambda req: serve.reply(req, 200, raw, {"Content-Type": content_type})).rsplit(":", 1)[1]
    url = f"http://news.test:{port}/rss"
    result = reader.read(url, Kind.RSS, "", 10, resolver=local_resolver)
    assert result.entries == [
        reader.Entry(f"http://news.test:{port}/a", "雷达回波外推", "降水临近预报",
                     "2025-01-02T03:04:05Z", "2025-01-02T03:04:05Z")
    ]
    assert (result.raw, result.content_type, result.truncated) == (raw, content_type, False)
    # Handed over directly, the feed is its own discovery candidate.
    assert reader.discover(url, resolver=local_resolver) == [reader.Candidate("气象新闻", url, Kind.RSS)]


def test_source_fetches_are_public_only_and_pinned(serve) -> None:
    seen: list[str] = []

    def handle(req: BaseHTTPRequestHandler) -> None:
        seen.append(req.path)
        serve.reply(req, 200, b"<p>ok</p>")

    port = serve(handle).rsplit(":", 1)[1]
    with pytest.raises(ReaderError, match="^source URL is not public$"):
        reader.fetch(f"http://127.0.0.1:{port}/")
    private = netguard.PublicResolver(lookup=lambda host, port, budget: ["127.0.0.1"])
    with pytest.raises(ReaderError, match="^source DNS contains a non-public address$"):
        reader.fetch(f"http://intranet.test:{port}/", resolver=private)
    assert seen == []
    # The connection goes to the vetted answer: `pinned.test` exists in no DNS.
    allowed = netguard.PublicResolver(
        lookup=lambda host, port, budget: ["127.0.0.1"], is_public=lambda ip: True
    )
    assert reader.fetch(f"http://pinned.test:{port}/x", resolver=allowed)[2] == b"<p>ok</p>"
    assert seen == ["/x"]


def test_pages_close_omitted_end_tags() -> None:
    # A hidden paragraph ends where the next block starts; it must not hide the rest of the page.
    page = pages.parse('<p hidden>隐藏<div><a href="/20261018/a.html">可见的新闻标题</a></div>'.encode())
    assert [e.title for e in page_entries(page, BASE, "", 20)[0]] == ["可见的新闻标题"]
    # A long list without </li> is not 300 levels deep, so it must not fail on nesting.
    items = "".join(f'<li><a href="/2026/10/{n % 28 + 1:02}/n{n}.html">第{n}条新闻标题</a>' for n in range(300))
    page = pages.parse(f"<html><head><title>列表</title><body><ul>{items}</ul>".encode())
    entries, truncated = page_entries(page, BASE, "", 200)
    assert (len(entries), truncated, page.title) == (200, True, "列表")
    # Unclosed paragraphs still separate words in the text.
    assert pages.plain_text("<p>first<p>second") == "first second"
