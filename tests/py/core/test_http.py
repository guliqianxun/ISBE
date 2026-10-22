import ipaddress
import time
from http.server import BaseHTTPRequestHandler

import httpx
import pytest

from isbe import __version__
from isbe.core import http


def ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    return ipaddress.ip_address(text)


def test_no_proxy_cidrs() -> None:
    nets = http.cidrs("localhost, 127.0.0.1,192.168.0.0/16,10.0.0.0/8,fd00::/8,0.0.0.0/0x")
    assert len(nets) == 3

    def hit(address: str) -> bool:
        return any(http.contains(net, bits, ip(address)) for net, bits in nets)

    assert hit("192.168.0.10") and hit("10.1.2.3") and hit("fd12::1")
    assert not hit("192.169.0.1") and not hit("8.8.8.8") and not hit("::1")
    assert http.contains(ip("0.0.0.0"), 0, ip("8.8.8.8"))
    assert http.contains(ip("192.168.1.1"), 16, ip("192.168.200.3")), "host bits are ignored"
    assert not http.contains(ip("10.0.0.0"), 33, ip("10.0.0.1"))
    assert not http.contains(ip("::"), 0, ip("8.8.8.8")), "families never match"


def test_no_proxy_hosts_addresses_and_wildcard() -> None:
    rules = http.NoProxy.parse("localhost, .corp.example,*.lan, internal.example, 127.0.0.1,::1,10.0.0.0/8")
    for host in ["localhost", "a.corp.example", "corp.example", "x.lan", "internal.example",
                 "deep.internal.example", "127.0.0.1", "[::1]", "10.9.8.7", "LOCALHOST."]:
        assert rules.bypass(host), host
    for host in ["example.com", "notinternal.example", "127.0.0.2", "11.0.0.1", "xlan"]:
        assert not rules.bypass(host), host
    assert http.NoProxy.parse("example.com, *").bypass("anything.test")


def test_proxy_comes_from_the_first_valid_variable_for_every_scheme() -> None:
    assert http.proxy_from({}) is None
    assert http.proxy_from({"HTTP_PROXY": "proxy:3128"}) == "http://proxy:3128"
    assert http.proxy_from({"ALL_PROXY": " ", "https_proxy": "http://s:1", "HTTP_PROXY": "http://h:1"}) == "http://s:1"
    client = http.Http(5, environ={"HTTPS_PROXY": "http://proxy:3128", "NO_PROXY": "192.168.0.0/16,.lan"})
    assert not client.direct("http://example.com/")
    assert not client.direct("https://example.com/")
    assert client.direct("http://192.168.0.10:8000/api")
    assert client.direct("http://essaies.lan/")
    assert http.Http(5, environ={"no_proxy": "*", "HTTP_PROXY": "http://p:1"}).direct("http://x/")
    assert not http.Http(5, environ={"NO_PROXY": "", "no_proxy": "*", "HTTP_PROXY": "http://p:1"}).direct("http://x/")
    assert http.Http(5, environ={}).direct("http://example.com/")


def test_requests_carry_the_isbe_user_agent_and_compact_json() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = http.Http(5, transport=httpx.MockTransport(handler))
    response = client.post("http://service.test/api", json={"b": 1, "a": "主题"})
    assert response.status == 200 and response.json() == {"ok": True}
    assert seen[0].headers["user-agent"] == f"isbe/{__version__}"
    assert seen[0].headers["content-type"] == "application/json; charset=utf-8"
    assert seen[0].content == '{"a":"主题","b":1}'.encode()


def test_redirects_follow_by_default_and_can_be_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(302, headers={"Location": "/new"})
        return httpx.Response(200, text="moved")

    transport = httpx.MockTransport(handler)
    client = http.Http(5, transport=transport)
    followed = client.get("http://service.test/old")
    assert (followed.status, followed.text, followed.url) == (200, "moved", "http://service.test/new")
    assert client.get("http://service.test/old", follow_redirects=False).status == 302
    assert http.Http(5, max_redirects=0, transport=transport).get("http://service.test/old").status == 302

    def loop(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "/again"})

    with pytest.raises(httpx.TooManyRedirects):
        http.Http(5, max_redirects=2, transport=httpx.MockTransport(loop)).get("http://service.test/")


def test_bodies_are_capped() -> None:
    client = http.Http(5, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 11)))
    assert client.get("http://service.test/", max_bytes=11).content == b"x" * 11
    with pytest.raises(http.BodyTooLarge):
        client.get("http://service.test/", max_bytes=10)


def test_streamed_bodies_without_length_stop_at_the_cap(serve) -> None:  # type: ignore[no-untyped-def]
    def chunked(req: BaseHTTPRequestHandler) -> None:
        req.send_response(200)
        req.send_header("Transfer-Encoding", "chunked")
        req.end_headers()
        for _ in range(64):
            req.wfile.write(b"400\r\n" + b"y" * 1024 + b"\r\n")
        req.wfile.write(b"0\r\n\r\n")

    base = serve(chunked)
    with http.Http(5, environ={}) as client:
        with pytest.raises(http.BodyTooLarge):
            client.get(base, max_bytes=10_000)
        assert len(client.get(base, max_bytes=1 << 20).content) == 64 * 1024


def test_timeout_bounds_the_whole_request_including_a_trickling_body(serve) -> None:  # type: ignore[no-untyped-def]
    def trickle(req: BaseHTTPRequestHandler) -> None:
        req.send_response(200)
        req.send_header("Content-Length", "100")
        req.end_headers()
        try:
            for _ in range(100):
                req.wfile.write(b"z")
                req.wfile.flush()
                time.sleep(0.2)
        except OSError:
            pass

    base = serve(trickle)
    started = time.monotonic()
    with pytest.raises(httpx.TimeoutException), http.Http(1.0, environ={}) as client:
        client.get(base)
    assert time.monotonic() - started < 1.8, "every read is limited to the time left"


def test_requests_go_through_the_proxy_unless_no_proxy_cidr_matches(serve) -> None:  # type: ignore[no-untyped-def]
    proxied: list[str] = []
    direct: list[str] = []

    def proxy(req: BaseHTTPRequestHandler) -> None:
        proxied.append(req.path)
        serve.reply(req, 200, "via proxy")

    def upstream(req: BaseHTTPRequestHandler) -> None:
        direct.append(req.path)
        serve.reply(req, 200, "direct")

    proxy_url, upstream_url = serve(proxy), serve(upstream)
    env = {"HTTP_PROXY": proxy_url, "NO_PROXY": "localhost"}
    with http.Http(5, environ=env) as client:
        assert client.get("http://upstream.invalid/x?q=1").text == "via proxy"
        assert proxied == ["http://upstream.invalid/x?q=1"]
        assert client.get(f"{upstream_url}/y").text == "via proxy"
    env["NO_PROXY"] = "localhost,127.0.0.0/8"
    with http.Http(5, environ=env) as client:
        assert client.get(f"{upstream_url}/z").text == "direct"
        assert direct == ["/z"]
