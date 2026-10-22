import sqlite3
import sys
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from isbe.core import settings


@pytest.fixture
def data(tmp_path: Path) -> Path:
    """An empty data directory, laid out like `isbe init` creates it."""
    for sub in ("topics", "memory", "reports"):
        (tmp_path / sub).mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Tests never see the developer's environment or another test's saved settings."""
    for name in settings.KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "_saved", {})
    yield


class _Quiet(ThreadingHTTPServer):
    def handle_error(self, request: object, client_address: object) -> None:
        # A client that gave up (a timeout test) is expected; anything else is reported.
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            super().handle_error(request, client_address)  # type: ignore[arg-type]


class LocalServer:
    """Threaded HTTP/1.1 servers on 127.0.0.1 for tests; no outside network."""

    def __init__(self) -> None:
        self.servers: list[ThreadingHTTPServer] = []

    def __call__(self, handle: Callable[[BaseHTTPRequestHandler], None]) -> str:
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                handle(self)

            do_POST = do_GET

            def log_message(self, *args: object) -> None:
                pass

        server = _Quiet(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    @staticmethod
    def reply(
        req: BaseHTTPRequestHandler,
        status: int = 200,
        body: bytes | str = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        data = body.encode() if isinstance(body, str) else body
        req.send_response(status)
        for name, value in (headers or {}).items():
            req.send_header(name, value)
        req.send_header("Content-Length", str(len(data)))
        req.end_headers()
        req.wfile.write(data)

    def close(self) -> None:
        for server in self.servers:
            server.shutdown()
            server.server_close()


@pytest.fixture
def serve() -> Iterator[LocalServer]:
    servers = LocalServer()
    yield servers
    servers.close()


@pytest.fixture(autouse=True)
def close_databases(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Close every SQLite connection a test opened, so none is left to the garbage collector."""
    opened: list[sqlite3.Connection] = []
    real = sqlite3.connect

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        conn = real(*args, **kwargs)  # type: ignore[arg-type]
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    yield
    for conn in opened:
        conn.close()
