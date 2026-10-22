"""Original-page reads through core.netguard (`article_date::fetch`, `verify`,
`article::fetch`), against a local server only."""

import socket
from http.server import BaseHTTPRequestHandler

import pytest

from isbe.articles import fetch
from isbe.articles.cache import caused_by
from isbe.articles.fetch import (
    PageError,
    UnsupportedBody,
    fetch_article,
    fetch_page,
    verify,
)
from isbe.core import netguard

ARTICLE = (
    "<head><title>标题</title><meta name='pubdate' content='2026-10-12'></head>"
    "<div itemprop='articleBody'><p>第一段。</p><p>第二段。</p></div>"
)


def site(serve, pages: dict[str, tuple[int, bytes | str, dict[str, str]]]) -> int:  # type: ignore[no-untyped-def]
    def handle(req: BaseHTTPRequestHandler) -> None:
        status, body, headers = pages.get(req.path, (404, "missing", {"Content-Type": "text/plain"}))
        serve.reply(req, status, body, headers)

    return int(serve(handle).rsplit(":", 1)[1])


def test_fetch_reads_html_through_the_public_fetcher(serve, local_fetcher) -> None:  # type: ignore[no-untyped-def]
    html = {"Content-Type": "text/html; charset=utf-8"}
    port = site(serve, {
        "/a": (200, ARTICLE, html),
        "/old": (301, "", {"Location": "/a#top"}),
        "/bare": (200, "<p>x</p>", {}),
        "/json": (200, "{}", {"Content-Type": "application/json"}),
        "/teapot": (418, "", html),
        "/odd": (599, "", html),
        "/empty": (204, "", html),
    })  # fmt: skip
    base = f"http://news.invalid:{port}"
    assert fetch_page(f"{base}/old", fetcher=local_fetcher) == (ARTICLE, f"{base}/a")
    assert fetch_page(f"{base}/bare", fetcher=local_fetcher) == ("<p>x</p>", f"{base}/bare")
    for path, message in [
        ("/json", "original page is not HTML"),
        ("/missing", "http status: 404"),
        ("/teapot", "http status: 418"),
        ("/odd", "http status: 599"),
        ("/empty", "original page returned HTTP 204 No Content"),
    ]:
        with pytest.raises(PageError) as raised:
            fetch_page(base + path, fetcher=local_fetcher)
        assert str(raised.value) == message


def test_verify_reports_every_failure_as_reviewable_evidence(serve, local_fetcher) -> None:  # type: ignore[no-untyped-def]
    port = site(serve, {
        "/a": (200, ARTICLE, {"Content-Type": "text/html"}),
        "/big": (200, b"x" * (fetch.MAX_BODY + 1), {"Content-Type": "text/html"}),
        "/gbk": (200, b"\xff\xff", {"Content-Type": "text/html; charset=gbk"}),
        "/x": (200, "", {"Content-Type": "text/html; charset=invented"}),
    })  # fmt: skip
    base = f"http://news.invalid:{port}"
    result = verify(f"{base}/a", "2026-10-01", "2026-10-16", fetcher=local_fetcher)
    assert (result.published_at, result.date_status, result.date_error) == ("2026-10-12", "in_range", None)
    for path, message in [
        ("/big", "the response body is larger than request limit: 1048576"),
        ("/gbk", "original page bytes do not match the declared charset"),
        ("/x", "original page declares an unsupported charset"),
        ("/missing", "http status: 404"),
    ]:
        failed = verify(base + path, "2026-10-01", "2026-10-16", fetcher=local_fetcher)
        assert (failed.date_status, failed.date_error, failed.date_evidence) == ("unknown", message, [])
    # The default fetcher refuses private addresses before connecting.
    assert verify(f"http://127.0.0.1:{port}/a", "2026-10-01", "2026-10-16").date_error == (
        "original-page URL is not public"
    )
    assert verify("ftp://example.com/a", "2026-10-01", "2026-10-16").date_error == (
        "source URL must use HTTP(S), with a host and without credentials or fragments"
    )


def test_transport_failures_are_worded_as_stored() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]  # closed again before the fetch: refused
    lookup = netguard.PublicResolver(lookup=lambda host, port, budget: ["127.0.0.1"], is_public=lambda ip: True)
    refused = verify(f"http://news.invalid:{port}/a", "2026-10-01", "2026-10-16",
                     fetcher=netguard.PublicFetcher(resolver=lookup))  # fmt: skip
    assert refused.date_error == "io: Connection refused (os error 111)"
    private = netguard.PublicResolver(lookup=lambda host, port, budget: ["10.0.0.1"])
    blocked = verify("http://news.invalid/a", "2026-10-01", "2026-10-16",
                     fetcher=netguard.PublicFetcher(resolver=private))  # fmt: skip
    assert blocked.date_error == "io: original-page DNS contains a non-public address"
    assert fetch.failure_message(netguard.LookupFailed("public DNS returned HTTP 503")) == "http status: 503"
    assert fetch.failure_message(netguard.LookupFailed("host not found")) == "host not found"
    assert fetch.failure_message(TimeoutError("original-page DNS lookup timed out")) == "timeout: global"


def test_gbk_page_within_the_byte_cap_is_read(serve, local_fetcher) -> None:  # type: ignore[no-untyped-def]
    # Just under 1 MiB of GBK bytes is about 1.5 MB as UTF-8; the size cap applies to the bytes received.
    page = "<p>发布时间：2026-10-12</p><p>" + "汉" * 520_000 + "</p>"
    body = page.encode("gbk")
    assert len(body) <= fetch.MAX_BODY < len(page.encode("utf-8"))
    port = site(serve, {"/a": (200, body, {"Content-Type": "text/html; charset=GBK"})})
    result = verify(f"http://news.invalid:{port}/a", "2026-10-01", "2026-10-16", fetcher=local_fetcher)
    assert (result.date_error, result.published_at) == (None, "2026-10-12")


def test_fetch_article_returns_the_record_or_unsupported_body(serve, local_fetcher) -> None:  # type: ignore[no-untyped-def]
    html = {"Content-Type": "text/html"}
    port = site(serve, {"/a": (200, ARTICLE, html), "/card": (200, "<p>no body</p>", html)})
    base = f"http://news.invalid:{port}"
    article = fetch_article(f"{base}/a", fetcher=local_fetcher)
    assert list(article) == ["fetched_at", "final_url", "paragraphs", "title"]
    assert (article["paragraphs"], article["title"], article["final_url"]) == (
        ["第一段。", "第二段。"], "标题", f"{base}/a",
    )  # fmt: skip
    with pytest.raises(UnsupportedBody) as raised:
        fetch_article(f"{base}/card", fetcher=local_fetcher)
    assert str(raised.value) == "No complete supported article body found"
    assert str(raised.value.__cause__) == "No unique supported article body found"
    assert caused_by(raised.value, UnsupportedBody) is raised.value
