from collections.abc import Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler

import pytest

from isbe.collect import feed as feed_parser
from isbe.collect import rss
from isbe.collect.items import CollectError
from isbe.core import netguard
from isbe.topics import Feed, Rss

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Rust Blog</title><link>https://blog.rust-lang.org/</link>
<description>b</description><language>en-us</language>
<item><title>Rust 1.90 released</title><link>https://blog.rust-lang.org/2026/10/10/rust-1.90/</link>
  <pubDate>Sat, 10 Oct 2026 00:00:00 +0000</pubDate><description>&lt;p&gt;New stuff&lt;/p&gt;</description></item>
<item><title>Old post</title><link>https://blog.rust-lang.org/old</link><pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate></item>
<item><title>Sponsored: buy a SCOOTER</title><link>https://blog.rust-lang.org/ad</link></item>
<item><title>Compiler notes</title><link>https://blog.rust-lang.org/inside-rust/x</link></item>
<item><title>Undated</title><link>https://blog.rust-lang.org/undated</link></item>
</channel></rss>"""
FEED = Feed(name="rust", url="https://blog.rust-lang.org/feed.xml")
CFG = Rss(exclude_keywords=["scooter"], exclude_url_patterns=["/inside-rust/"])
NOW = datetime(2026, 10, 13, 6, tzinfo=UTC)


def test_filters_like_v1() -> None:
    items = rss.parse(RSS.encode(), FEED, CFG, NOW)
    assert [(i.id, i.published_at) for i in items] == [
        ("rss:https://blog.rust-lang.org/2026/10/10/rust-1.90/", "2026-10-10T00:00:00Z"),
        ("rss:https://blog.rust-lang.org/undated", "2026-10-13T06:00:00Z"),
    ]
    assert (items[0].lang, items[0].source) == ("en-us", "rss:rust")
    assert items[0].summary == "<p>New stuff</p>"


def test_an_html_page_is_named_as_such() -> None:
    with pytest.raises(CollectError, match="^got an HTML page, not a feed"):
        rss.parse(b"<!doctype html><HTML><body>Access denied</body></html>", FEED, CFG, NOW)


def test_atom_feeds_use_alternate_links_and_content() -> None:
    atom = """<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="zh-CN"><title>T</title>
      <entry><title>  Two\n words </title><updated>2026-10-12T00:00:00+08:00</updated>
        <link rel="self" href="https://x.example/self"/><link href=" https://x.example/a "/>
        <content type="html">&lt;b&gt;body&lt;/b&gt;</content><author><name>Ann</name></author></entry>
    </feed>"""
    [item] = rss.parse(atom.encode(), FEED, Rss(), NOW)
    assert (item.url, item.title, item.lang) == ("https://x.example/a", "Two words", "zh-CN")
    assert (item.summary, item.authors, item.published_at) == ("<b>body</b>", ["Ann"], "2026-10-11T16:00:00Z")


GBK_TITLE = "雷达回波外推"
# Short enough that GBK bytes fall in the first 64 bytes, which quick-xml
# decoded as UTF-8 before the declaration took effect, which fails.
GBK_RSS = (
    "<?xml encoding=\"{decl}\"?>"
    "<rss version=\"2.0\"><channel><title>气象</title><language>zh-cn</language>"
    f"<item><title>{GBK_TITLE}</title><link>https://news.example.cn/a</link>"
    "<pubDate>Sat, 10 Oct 2026 00:00:00 +0800</pubDate><description>降水临近预报</description></item>"
    "</channel></rss>"
)


def _gbk(decl: str) -> bytes:
    raw = GBK_RSS.replace("{decl}", decl).encode("gbk")
    assert GBK_TITLE.encode("utf-8") not in raw  # really GBK bytes, not UTF-8
    return raw


def test_gbk_feed_declared_in_the_prolog() -> None:
    for decl in ("GBK", "gb2312", "GB18030"):
        [item] = rss.parse(_gbk(decl), FEED, Rss(), NOW)
        assert (item.title, item.summary, item.lang) == (GBK_TITLE, "降水临近预报", "zh-cn")


def test_gbk_feed_declared_only_in_content_type(
    serve, local_fetcher: Callable[..., netguard.PublicFetcher]
) -> None:
    raw = _gbk("x").replace(b' encoding="x"', b"")

    def handle(req: BaseHTTPRequestHandler) -> None:
        serve.reply(req, 200, raw, {"Content-Type": "application/rss+xml; charset=GB2312"})

    port = serve(handle).rsplit(":", 1)[1]
    feed = Feed(name="cma", url=f"http://feeds.invalid:{port}/rss")
    [item] = rss.fetch(local_fetcher(timeout=5, max_redirects=10), feed, Rss(), NOW)
    assert item.title == GBK_TITLE
    with pytest.raises(feed_parser.FeedError, match="^unable to parse feed: no root element$"):
        rss.parse(raw, FEED, Rss(), NOW)


def test_feed_fetch_is_public_only_and_revalidates_redirects(serve) -> None:
    def handle(req: BaseHTTPRequestHandler) -> None:
        if req.path == "/moved":
            serve.reply(req, 302, b"", {"Location": "http://10.0.0.1/rss"})
        else:
            serve.reply(req, 200, RSS)

    port = serve(handle).rsplit(":", 1)[1]
    default = rss.fetcher()
    assert (default.timeout, default.max_redirects) == (90.0, 10)
    # A literal private address is refused before any connection.
    with pytest.raises(CollectError, match="^feed URL is not public$"):
        rss.fetch(default, Feed(name="x", url=f"http://127.0.0.1:{port}/rss"), CFG, NOW)
    # A name that resolves to a private address is refused, the address pinned.
    calls: list[str] = []

    def lookup(host: str, port: int, budget: float) -> list[str]:
        calls.append(host)
        return ["127.0.0.1"]

    strict = netguard.PublicFetcher(resolver=netguard.PublicResolver(lookup=lookup))
    with pytest.raises(CollectError, match="^feed DNS contains a non-public address$"):
        rss.fetch(strict, Feed(name="x", url=f"http://feeds.invalid:{port}/rss"), CFG, NOW)
    assert calls == ["feeds.invalid"]
    # Each redirect hop is checked again: a public feed cannot bounce to the LAN.
    permissive = netguard.PublicResolver(
        lookup=lambda host, port, budget: ["127.0.0.1"],
        is_public=lambda ip: ip.is_loopback,
    )
    fetcher = netguard.PublicFetcher(timeout=5, max_redirects=10, resolver=permissive)
    ok = rss.fetch(fetcher, Feed(name="x", url=f"http://feeds.invalid:{port}/rss"), CFG, NOW)
    assert len(ok) == 2
    with pytest.raises(CollectError, match="^feed URL is not public$"):
        rss.fetch(fetcher, Feed(name="x", url=f"http://feeds.invalid:{port}/moved"), CFG, NOW)


def test_failed_status_is_an_error(serve, local_fetcher: Callable[..., netguard.PublicFetcher]) -> None:
    port = serve(lambda req: serve.reply(req, 403, "no")).rsplit(":", 1)[1]
    feed = Feed(name="x", url=f"http://feeds.invalid:{port}/rss")
    with pytest.raises(CollectError, match="^http status: 403$"):
        rss.fetch(local_fetcher(timeout=5), feed, CFG, NOW)
