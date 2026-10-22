import ipaddress
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler

import httpx
import pytest

from isbe.core import http, netguard, validators
from isbe.core.netguard import Blocked, PublicFetcher, PublicResolver


def loopback_ok(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.is_loopback or netguard.public_ip(ip)


def pinned(addresses: list[str], calls: list[str] | None = None, **options) -> PublicResolver:  # type: ignore[no-untyped-def]
    def lookup(host: str, port: int, budget: float) -> list[str]:
        if calls is not None:
            calls.append(host)
        assert 0 < budget <= netguard.TIMEOUT
        return addresses

    return PublicResolver(lookup=lookup, **options)


def test_private_addresses_and_mixed_dns_are_rejected() -> None:
    for value in [
        "127.0.0.1",
        "10.1.2.3",
        "100.100.100.200",
        "169.254.169.254",
        "172.16.0.1",
        "192.168.0.1",
        "198.18.0.1",
        "224.1.1.1",
        "::1",
        "::ffff:127.0.0.1",
        "fd00::1",
        "fe80::1",
        "2002:7f00:1::1",
        "2001:db8::1",
    ]:
        assert not netguard.public_ip(value), value
    assert netguard.public_ip("8.8.8.8")
    assert netguard.public_ip("2606:4700:4700::1111")
    with pytest.raises(Blocked):
        netguard.checked_addresses(["8.8.8.8", "127.0.0.1"])
    with pytest.raises(netguard.LookupFailed):
        netguard.checked_addresses([])
    for value in [
        "file:///etc/passwd",
        "http://127.1/a",
        "http://2130706433/a",
        "http://user:pass@example.com/a",
        "http://[::1]/",
        "http://example.com\\@127.0.0.1/",
    ]:
        with pytest.raises((Blocked, validators.Invalid)):
            netguard.public_url(value)


def test_other_reserved_ranges_stay_non_public() -> None:
    for value in ["0.1.2.3", "192.0.0.8", "192.0.2.10", "192.88.99.1", "198.19.255.1",
                  "198.51.100.7", "203.0.113.9", "240.0.0.1", "255.255.255.255", "100.64.0.1",
                  "2001::1", "2001:2::1", "2001:1ff::1", "3fff::1", "64:ff9b::808:808", "ff02::1"]:
        assert not netguard.public_ip(value), value
    for value in ["1.1.1.1", "100.128.0.1", "172.32.0.1", "192.0.1.1", "192.0.3.1", "198.20.0.1",
                  "2001:200::1", "2a00:1450::1", "3fff:1000::1"]:
        assert netguard.public_ip(value), value


def test_wordpress_hosts_in_192_0_slash_16_are_public() -> None:
    for value in ["192.0.66.2", "192.0.78.9", "192.0.77.40", "192.0.1.1", "192.0.255.1"]:
        assert netguard.public_ip(value), value
        assert netguard.checked_addresses([value]) == [value]
        assert netguard.public_url(f"https://{value}/post") == f"https://{value}/post"
    for value in ["192.0.0.1", "192.0.0.170", "192.0.2.1", "192.0.2.255"]:
        assert not netguard.public_ip(value), value


def test_every_redirect_is_validated_and_bounded() -> None:
    start = netguard.public_url("https://news.example/a")
    assert netguard.redirect_url(start, "/b#section", 2) == "https://news.example/b"
    with pytest.raises(Blocked):
        netguard.redirect_url(start, "http://169.254.169.254/latest/meta-data/", 0)
    with pytest.raises(Blocked):
        netguard.redirect_url(start, "//127.0.0.1/private", 0)
    with pytest.raises(netguard.RedirectError, match="limit"):
        netguard.redirect_url(start, "/b", 3)
    with pytest.raises((netguard.RedirectError, validators.Invalid)):
        netguard.redirect_url(start, "http://[bad/", 0)


def test_doh_accepts_a_records_and_ignores_cname_without_truncating_private_answers() -> None:
    body = (b'{"Status":0,"TC":false,"Answer":[{"type":5,"data":"cdn.example.com."},'
            b'{"type":1,"data":"1.1.1.1"},{"type":1,"data":"1.1.1.1"},{"type":1,"data":"198.18.0.1"},'
            b'{"type":1,"data":"127.0.0.1"},{"type":28,"data":"2606:4700:4700::1111"}]}')
    assert netguard.parse_doh(body) == ["1.1.1.1", "198.18.0.1", "127.0.0.1"]
    # The caller must reject the entire result because it contains non-public addresses.
    with pytest.raises(Blocked):
        netguard.checked_addresses(netguard.parse_doh(body))


@pytest.mark.parametrize(
    "body",
    [
        '{"Status":3}',
        '{"Status":0}',
        '{"Status":0,"Answer":[]}',
        '{"Status":0,"Answer":[{"type":5,"data":"cdn.example.com."}]}',
        '{"Status":0,"Answer":[{"type":1,"data":"127.1"}]}',
        '{"Status":0,"Answer":[{"type":1,"data":"::1"}]}',
        '{"Status":0,"Answer":[{"type":1,"data":"999.2.3.4"}]}',
        '{"Status":0,"Answer":[{"type":1}]}',
        '{"Status":0,"TC":true,"Answer":[{"type":1,"data":"1.1.1.1"}]}',
        '{"Answer":[{"type":1,"data":"1.1.1.1"}]}',
        '{"Status":0,"TC":null,"Answer":[{"type":1,"data":"1.1.1.1"}]}',
        '{"Status":0,"Answer":[{"type":1.0,"data":"1.1.1.1"}]}',
        "not json",
    ],
)
def test_doh_rejects_errors_malformed_answers_and_empty_results(body: str) -> None:
    with pytest.raises(netguard.LookupFailed):
        netguard.parse_doh(body.encode())


def test_doh_invalid_hosts_and_expired_budgets_never_issue_requests() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    client = http.Http(1, transport=httpx.MockTransport(refuse))
    for host in ["", "x/y", "x?type=AAAA", "user@example.com", "-invalid.com", "invalid-.com", "x..com"]:
        with pytest.raises(netguard.BadHost):
            netguard.cloudflare(host, 443, 1, client=client)
    with pytest.raises(TimeoutError):
        netguard.cloudflare("example.com", 443, 0, client=client)


def test_doh_queries_cloudflare_without_redirects() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"Status": 0, "Answer": [{"type": 1, "data": "93.184.215.14"}]})

    client = http.Http(1, transport=httpx.MockTransport(answer))
    assert netguard.cloudflare("Example.com.", 443, 1, client=client) == ["93.184.215.14"]
    assert str(seen[0].url) == "https://cloudflare-dns.com/dns-query?name=Example.com&type=A"
    assert seen[0].headers["accept"] == "application/dns-json"
    moved = http.Http(1, transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={"Location": "/x"})))
    with pytest.raises(netguard.LookupFailed, match="HTTP 302"):
        netguard.cloudflare("example.com", 443, 1, client=moved)


def test_resolver_follows_isbe_article_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    used: list[str] = []
    monkeypatch.setattr(netguard, "cloudflare", lambda h, p, t: used.append("cloudflare") or ["1.1.1.1"])
    monkeypatch.setattr(netguard, "system", lambda h, p, t: used.append("system") or ["8.8.8.8"])
    resolver = PublicResolver()
    assert resolver("example.com", 443, 30) == ["8.8.8.8"]
    monkeypatch.setenv("ISBE_ARTICLE_DNS", "cloudflare")
    assert resolver("example.com", 443, 30) == ["1.1.1.1"]
    assert resolver("[2606:4700:4700::1111]", 443, 30) == ["2606:4700:4700::1111"]
    assert used == ["system", "cloudflare"]
    with pytest.raises(Blocked):
        resolver("127.0.0.1", 80, 30)
    many = PublicResolver(lookup=lambda h, p, t: [f"8.8.{i}.1" for i in range(40)])
    assert len(many("example.com", 443, 30)) == 16


def test_fetch_connects_only_to_the_vetted_address_and_ignores_proxies(  # type: ignore[no-untyped-def]
    serve, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str | None]] = []

    def page(req: BaseHTTPRequestHandler) -> None:
        seen.append((req.path, req.headers.get("Host")))
        serve.reply(req, 200, "<html>ok</html>", {"Content-Type": "text/html"})

    port = int(serve(page).rsplit(":", 1)[1])
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    calls: list[str] = []
    fetcher = PublicFetcher(resolver=pinned(["127.0.0.1"], calls, is_public=loopback_ok))
    response = fetcher.fetch(f"http://pinned.invalid:{port}/a?x=1", max_bytes=1024)
    assert (response.status, response.content) == (200, b"<html>ok</html>")
    assert response.url == f"http://pinned.invalid:{port}/a?x=1"
    assert seen == [("/a?x=1", f"pinned.invalid:{port}")]
    assert calls == ["pinned.invalid"]
    with pytest.raises(http.BodyTooLarge):
        fetcher.fetch(f"http://pinned.invalid:{port}/a", max_bytes=4)


def test_rebinding_answers_are_refused_before_connecting(serve) -> None:  # type: ignore[no-untyped-def]
    seen: list[str] = []
    port = int(serve(lambda req: seen.append(req.path) or serve.reply(req, 200, "leak")).rsplit(":", 1)[1])
    for answers in (["127.0.0.1"], ["93.184.215.14", "127.0.0.1"], ["::1"], ["not an ip"]):
        fetcher = PublicFetcher(resolver=pinned(answers))
        with pytest.raises(Blocked):
            fetcher.fetch(f"http://rebind.invalid:{port}/", max_bytes=1024)
    with pytest.raises(Blocked):
        PublicFetcher().fetch(f"http://127.0.0.1:{port}/", max_bytes=1024)
    assert seen == []


def test_redirects_are_revalidated_and_limited(serve) -> None:  # type: ignore[no-untyped-def]
    paths: list[str] = []

    def site(req: BaseHTTPRequestHandler) -> None:
        paths.append(req.path)
        targets = {
            "/start": "/next#frag",
            "/loop": "/loop",
            "/metadata": "http://169.254.169.254/latest/",
            "/nowhere": None,
        }
        if req.path in targets:
            headers = {} if targets[req.path] is None else {"Location": targets[req.path]}
            serve.reply(req, 302, "", headers)
        else:
            serve.reply(req, 200, f"page {req.path}")

    port = int(serve(site).rsplit(":", 1)[1])
    base = f"http://site.invalid:{port}"
    fetcher = PublicFetcher(resolver=pinned(["127.0.0.1"], is_public=loopback_ok))
    final = fetcher.fetch(f"{base}/start", max_bytes=1024)
    assert (final.url, final.text) == (f"{base}/next", "page /next")
    paths.clear()
    with pytest.raises(netguard.RedirectError, match="limit"):
        fetcher.fetch(f"{base}/loop", max_bytes=1024)
    assert paths == ["/loop"] * 4, "three redirects are followed, the fourth is refused"
    with pytest.raises(Blocked):
        fetcher.fetch(f"{base}/metadata", max_bytes=1024)
    with pytest.raises(netguard.RedirectError, match="no location"):
        fetcher.fetch(f"{base}/nowhere", max_bytes=1024)
    with fetcher.stream(f"{base}/missing") as page:
        assert page.status == 200 and page.read(100) == b"page /missing"


def test_the_time_budget_covers_every_hop(serve) -> None:  # type: ignore[no-untyped-def]
    def slow(req: BaseHTTPRequestHandler) -> None:
        if req.path == "/hop":
            time.sleep(0.6)
            serve.reply(req, 302, "", {"Location": "/slow"})
        else:
            time.sleep(0.6)
            serve.reply(req, 200, "late")

    port = int(serve(slow).rsplit(":", 1)[1])
    fetcher = PublicFetcher(timeout=1.0, resolver=pinned(["127.0.0.1"], is_public=loopback_ok))
    started = time.monotonic()
    with pytest.raises((httpx.TimeoutException, TimeoutError)):
        fetcher.fetch(f"http://slow.invalid:{port}/hop", max_bytes=100)
    assert time.monotonic() - started < 1.6


def _sni(hello: bytes) -> str | None:
    """The server_name of a TLS ClientHello record."""
    if len(hello) < 5 or hello[0] != 0x16:
        return None
    body = hello[5:]
    pos = 4 + 2 + 32  # handshake header, client_version, random
    pos += 1 + body[pos]  # session id
    pos += 2 + struct.unpack(">H", body[pos : pos + 2])[0]  # cipher suites
    pos += 1 + body[pos]  # compression methods
    end = pos + 2 + struct.unpack(">H", body[pos : pos + 2])[0]
    pos += 2
    while pos + 4 <= end:
        kind, size = struct.unpack(">HH", body[pos : pos + 4])
        if kind == 0:
            length = struct.unpack(">H", body[pos + 7 : pos + 9])[0]
            return body[pos + 9 : pos + 9 + length].decode()
        pos += 4 + size
    return None


def test_tls_uses_the_host_name_while_connecting_to_the_pinned_address() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    captured: list[str | None] = []

    def accept() -> None:
        conn, _ = listener.accept()
        with conn:
            conn.settimeout(5)
            data = b""
            while len(data) < 5 or len(data) < 5 + struct.unpack(">H", data[3:5])[0]:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            captured.append(_sni(data))

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    fetcher = PublicFetcher(timeout=5, resolver=pinned(["127.0.0.1"], is_public=loopback_ok))
    with pytest.raises((httpx.HTTPError, OSError)):
        fetcher.fetch(f"https://news.example:{port}/", max_bytes=100)
    thread.join(5)
    listener.close()
    assert captured == ["news.example"]
