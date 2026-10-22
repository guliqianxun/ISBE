"""The server around the routes: `serve` checks, connection and body limits,
security headers and the client address."""

import asyncio
import json
import re
import socket
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from conftest import PASSWORD, TOKEN, FakeManager, add_user

from isbe.core import assets
from isbe.web import Limits, TrustedProxies, create_app, serve, views
from isbe.web.guards import client_address


def head(server: Any, method: str, path: str, extra: str = "") -> bytes:
    return (
        f"{method} {path} HTTP/1.1\r\nHost: {server.host}\r\nX-ISBE-CSRF: {TOKEN}\r\n"
        f"Content-Type: application/json\r\n{extra}\r\n"
    ).encode()


def read_reply(sock: socket.socket, timeout: float = 10) -> tuple[int, dict[str, str], bytes]:
    sock.settimeout(timeout)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError(f"closed after {data!r}")
        data += chunk
    top, _, body = data.partition(b"\r\n\r\n")
    lines = top.decode("latin-1").split("\r\n")
    headers = {k.lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
    while len(body) < int(headers.get("content-length", "0")):
        chunk = sock.recv(65536)
        if not chunk:
            break
        body += chunk
    return int(lines[0].split()[1]), headers, body


def closed(sock: socket.socket, timeout: float) -> bool:
    """Whether the server closes (or resets) the connection within `timeout`."""
    sock.settimeout(timeout)
    try:
        while sock.recv(65536):
            pass
    except TimeoutError:
        return False
    except OSError:
        return True
    return True


def quick_get(server: Any, path: str = "/api/session") -> tuple[int, float]:
    started = time.monotonic()
    response = httpx.get(f"http://{server.host}{path}", timeout=5)
    return response.status_code, time.monotonic() - started


def test_serve_refuses_bad_binds_and_shared_mode_without_admin(data: Path) -> None:
    with pytest.raises(ValueError, match="^bind must be an IP address and port$"):
        serve(data, "localhost:8080", False, False)
    for bind in ["127.0.0.1", "127.0.0.1:65536", "[::1:80", "::1:80"]:
        with pytest.raises(ValueError):
            serve(data, bind, False, False)
    with pytest.raises(RuntimeError, match="^Use --shared for network access; local mode only listens on localhost.$"):
        serve(data, "0.0.0.0:8080", False, False)
    with pytest.raises(RuntimeError, match="^Create an administrator with account-add --admin"):
        serve(data, "127.0.0.1:0", True, False)
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    port = busy.getsockname()[1]
    try:
        with pytest.raises(RuntimeError, match=rf"^bind 127.0.0.1:{port}: Address already in use \(os error 98\)$"):
            serve(data, f"127.0.0.1:{port}", False, False)
    finally:
        busy.close()


def test_serve_opens_the_queue_after_binding_and_closes_it(data: Path, monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    opened: list[FakeManager] = []
    served: list[Any] = []

    def factory(root: Path) -> FakeManager:
        assert root == data
        opened.append(FakeManager())
        return opened[-1]

    def run(self: uvicorn.Server, sockets: list[socket.socket]) -> None:
        served.append((self.config, sockets[0].getsockname()))

    monkeypatch.setattr(uvicorn.Server, "run", run)
    serve(data, "127.0.0.1:0", False, False, generation=factory)
    assert opened[0].closed
    config, address = served[0]
    assert address[0] == "127.0.0.1" and config.proxy_headers is False and config.server_header is False
    assert capsys.readouterr().out == "ISBE console on http://127.0.0.1:0\n"

    def broken(root: Path) -> FakeManager:
        raise RuntimeError("Another queue worker is already running")

    with pytest.raises(RuntimeError, match="queue worker"):
        serve(data, "127.0.0.1:0", False, False, generation=broken)


def test_slow_request_bodies_do_not_block_other_requests(live: Any) -> None:
    server = live(Limits(body_seconds=1.5, handlers=2))
    slow = []
    for _ in range(8):
        sock = server.connect()
        sock.sendall(head(server, "POST", "/api/login", "Content-Length: 60000\r\n") + b"{")
        slow.append(sock)
    time.sleep(0.2)
    for _ in range(5):
        status, elapsed = quick_get(server)
        assert status == 200 and elapsed < 1.0
    for sock in slow:
        status, headers, body = read_reply(sock)
        assert status == 408
        assert json.loads(body) == {"error": "The request body was not received in time."}
        assert headers["connection"] == "close"
        assert closed(sock, 2)


def test_oversize_bodies_are_refused_while_streaming(live: Any) -> None:
    server = live()
    sock = server.connect()
    sock.sendall(head(server, "PUT", "/api/settings", "Content-Length: 10000000\r\n"))
    status, headers, body = read_reply(sock)
    assert (status, json.loads(body)) == (413, {"error": "Settings request is too large"})
    assert headers["connection"] == "close"
    chunked = server.connect()
    chunked.sendall(head(server, "PUT", "/api/settings", "Transfer-Encoding: chunked\r\n"))
    piece = b" " * 8192
    for _ in range(9):
        chunked.sendall(b"%x\r\n%s\r\n" % (len(piece), piece))
    status, headers, body = read_reply(chunked)
    assert (status, json.loads(body)) == (413, {"error": "Settings request is too large"})
    assert headers["connection"] == "close"
    assert closed(chunked, 2)
    assert quick_get(server)[0] == 200


def test_slow_headers_and_idle_connections_are_dropped(live: Any) -> None:
    server = live(Limits(header_seconds=1, connections_per_peer=3))
    partial = server.connect()
    partial.sendall(b"GET /api/session HTTP/1.1\r\nHost: ")
    assert closed(partial, 3)
    idle = [server.connect() for _ in range(3)]
    time.sleep(0.2)
    extra = server.connect()
    assert closed(extra, 1)
    for sock in idle:
        assert closed(sock, 3)
    assert quick_get(server)[0] == 200


def test_handlers_run_in_threads_and_failures_keep_serving(live: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    server = live(Limits(handlers=4))

    def failing(conn: Any) -> Any:
        raise RuntimeError("internal failure at /private/path")

    def slow(conn: Any, data: Path, topic: str) -> Any:
        time.sleep(2)
        return None

    monkeypatch.setattr(views, "runs", failing)
    monkeypatch.setattr(views, "triage", slow)
    for _ in range(6):
        response = httpx.get(f"http://{server.host}/api/runs", timeout=5)
        assert response.status_code == 500
        assert response.json() == {
            "error": "The request could not be completed. Try again or review the service logs."
        }
    started = threading.Thread(target=httpx.get, args=(f"http://{server.host}/api/triage",), kwargs={"timeout": 5})
    started.start()
    time.sleep(0.2)
    status, elapsed = quick_get(server)
    assert status == 200 and elapsed < 1.0
    started.join()


EXPECTED_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)


def test_security_headers_on_every_response(live: Any) -> None:
    server = live()
    base = f"http://{server.host}"
    with httpx.Client(base_url=base, timeout=5) as client:
        responses = [
            client.get("/"), client.get("/reports?topic=x"), client.get("/app.js"), client.get("/app.css"),
            client.get("/help"), client.get("/api/session"), client.get("/api/nope"), client.post("/"),
            client.get("/figures/missing.png"), client.get("/api/session", headers={"Host": "evil.example"}),
            client.put("/api/settings", content=b"{}", headers={"Content-Type": "application/json"}),
        ]
    for response in responses:
        h = response.headers
        assert h["content-security-policy"] == EXPECTED_CSP, response.url
        assert h["x-frame-options"] == "DENY"
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "no-referrer"
        assert h["cross-origin-resource-policy"] == "same-origin"
        assert h.get("cache-control") in ("no-store", "no-cache"), response.url
        assert "server" not in h
    assert [r.status_code for r in responses] == [200, 200, 200, 200, 200, 200, 404, 405, 404, 403, 403]


def test_the_ui_works_under_its_policy() -> None:
    """The policy allows what web/ uses: no inline script, external origin or framing."""
    index = assets.text("web", "index.html")
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", index, re.S)
    assert scripts == [""] and '<script src="/app.js">' in index
    assert not re.search(r"\son[a-z]+\s*=", index)
    assert re.search(r'<link rel="icon" href="data:', index) and "img-src 'self' data:" in EXPECTED_CSP
    assert '<link rel="stylesheet" href="/app.css">' in index
    app = assets.text("web", "app.js")
    assert not re.search(r"\beval\(|new Function\(|<script|\son(click|load|error)=", app)
    assert not re.search(r"""fetch\(\s*['"`]https?:""", app)
    # Inline style attributes are rendered by app.js, hence 'unsafe-inline' for styles only.
    assert 'style="' in app
    css = assets.text("web", "app.css")
    assert "@import" not in css and "url(" not in css


def test_login_throttle_uses_the_peer_unless_a_trusted_proxy_forwards(data: Path) -> None:
    add_user(data, "admin", admin=True)

    def client(peer: str, trusted: str | None = None) -> Any:
        app = create_app(data, token=TOKEN, local_port=8080, shared=True, generation=FakeManager(),
                         trusted_proxies=TrustedProxies.parse(trusted))

        def login(password: str, forwarded: str | None = None) -> int:
            headers = {"X-ISBE-CSRF": TOKEN, "Content-Type": "application/json"}
            if forwarded is not None:
                headers["X-Forwarded-For"] = forwarded
            body = json.dumps({"username": "admin", "password": password})

            async def post() -> int:
                transport = httpx.ASGITransport(app=app, client=(peer, 40000))
                async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8080") as c:
                    return (await c.post("/api/login", content=body, headers=headers)).status_code

            return asyncio.run(post())

        return login

    wrong = "wrong-password-0"
    attacker = client("203.0.113.5")
    # A spoofed X-Forwarded-For from an untrusted peer does not open a new bucket.
    assert [attacker(wrong, f"198.51.100.{i}") for i in range(6)] == [401] * 5 + [429]
    assert client("203.0.113.6")(wrong) == 401
    assert client("203.0.113.6")(PASSWORD) == 200
    proxy = client("10.0.0.2", "10.0.0.0/8")
    assert [proxy(wrong, "6.6.6.6, 198.51.100.7") for _ in range(6)] == [401] * 5 + [429]
    assert proxy(wrong, "198.51.100.7, 198.51.100.8") == 401


def test_client_address_trusts_only_configured_proxies() -> None:
    none = TrustedProxies()
    assert client_address("203.0.113.5", ["1.2.3.4"], none) == "203.0.113.5"
    proxies = TrustedProxies.parse("127.0.0.1, 10.0.0.0/8 ::1")
    assert client_address("203.0.113.5", ["1.2.3.4"], proxies) == "203.0.113.5"
    assert client_address("127.0.0.1", ["1.2.3.4, 10.1.1.1"], proxies) == "1.2.3.4"
    assert client_address("127.0.0.1", ["9.9.9.9", "1.2.3.4"], proxies) == "1.2.3.4"
    assert client_address("::1", ["[2001:db8::1]:443"], proxies) == "2001:db8::1"
    assert client_address("127.0.0.1", [], proxies) == "127.0.0.1"
    assert client_address("127.0.0.1", ["garbage, 1.2.3.4"], proxies) == "1.2.3.4"
    assert client_address("127.0.0.1", ["1.2.3.4, garbage"], proxies) == "127.0.0.1"
    assert client_address("127.0.0.1", ["10.0.0.9"], proxies) == "10.0.0.9"
    with pytest.raises(ValueError, match="ISBE_TRUSTED_PROXIES"):
        TrustedProxies.parse("not-an-ip")
