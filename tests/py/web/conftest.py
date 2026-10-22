"""Fixtures for the console tests: a fake report queue, direct calls into the
dispatcher, and a real uvicorn server for the connection-level tests. No outside network."""

import hashlib
import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from isbe import auth, topics
from isbe.core import db
from isbe.web import Limits, TrustedProxies, create_app
from isbe.web.app import Endpoint
from isbe.web.console import Console
from isbe.web.server import server_config
from isbe.web.wire import BodyState, Request

TOKEN = "test-token"
PASSWORD = "temporary-test-password"


@pytest.fixture(autouse=True)
def fast_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """One PBKDF2 round instead of 600 000: the console tests are about routing."""
    monkeypatch.setattr(
        auth, "_derive", lambda password, salt: hashlib.pbkdf2_hmac("sha256", password, salt, 1, 32)
    )


@dataclass(frozen=True)
class FakeReport:
    topic: str
    period: str


@dataclass
class FakeJob:
    id: str
    owner: str
    topic: str
    state: str = "queued"
    stage: str = "queued"
    error: str | None = None
    result: FakeReport | None = None
    created_at: str = field(default_factory=db.now)
    started_at: str | None = None
    finished_at: str | None = None
    position: int | None = None


class FakeStartError(Exception):
    def __init__(self, status: int, code: str, error: str) -> None:
        super().__init__(error)
        self.status, self.code, self.error = status, code, error
        self.job: FakeJob | None = None


class FakeManager:
    """The queue's validation and bookkeeping as `isbe.generation` does it, without a worker."""

    def __init__(self) -> None:
        self.jobs: list[FakeJob] = []
        self.requests: dict[tuple[str, str], str] = {}
        self.closed = False

    def list(self, owner: str | None) -> list[FakeJob]:
        return [j for j in reversed(self.jobs) if owner is None or j.owner == owner]

    def get(self, id: str) -> FakeJob | None:
        return next((j for j in self.jobs if j.id == id), None)

    def submit(self, owner: str, topic: str, request_id: str) -> FakeJob:
        try:
            topics.check_id(topic)
        except topics.TopicError:
            raise FakeStartError(400, "invalid_topic", "Choose a valid topic.") from None
        if not request_id or len(request_id) > 128 or not all(c.isalnum() or c in "-_" for c in request_id):
            raise FakeStartError(400, "invalid_request", "A valid request identifier is required.")
        if (owner, request_id) in self.requests:
            return self.get(self.requests[(owner, request_id)])  # type: ignore[return-value]
        job = FakeJob(id=f"{len(self.jobs) + 1:032x}", owner=owner, topic=topic, position=1)
        self.jobs.append(job)
        self.requests[(owner, request_id)] = job.id
        return job

    def cancel(self, id: str, owner: str | None) -> FakeJob:
        job = self.get(id)
        if job is None or (owner is not None and job.owner != owner):
            raise FakeStartError(404, "not_found", "Task not found.")
        if job.state != "queued":
            raise FakeStartError(409, "not_queued", "Only queued tasks can be cancelled.")
        job.state, job.finished_at, job.position = "cancelled", db.now(), None
        return job

    def close(self) -> None:
        self.closed = True


def make_console(root: Path, *, shared: bool = False, secure_cookies: bool = False, **kwargs: Any) -> Console:
    kwargs.setdefault("generation", FakeManager())
    kwargs.setdefault("local_port", 8080)
    app = create_app(root, token=TOKEN, shared=shared, secure_cookies=secure_cookies, **kwargs)
    console: Console = app.state.console
    return console


@dataclass
class Result:
    status: int
    body: Any
    headers: list[tuple[str, str]]
    raw: bytes

    def header(self, name: str) -> str | None:
        return next((v for k, v in self.headers if k.lower() == name.lower()), None)

    @property
    def text(self) -> str:
        return self.raw.decode("utf-8", "replace")


def call(
    console: Console,
    method: str,
    target: str,
    body: Any = None,
    *,
    host: str = "127.0.0.1:8080",
    token: str = TOKEN,
    content_type: str = "application/json",
    cookie: str | None = None,
    raw: bytes | None = None,
    client: str = "127.0.0.1",
) -> Result:
    """One request through the dispatcher, as the ASGI layer hands it over (body read)."""
    path, _, query = target.partition("?")
    headers = [("host", host), ("x-isbe-csrf", token), ("content-type", content_type)]
    if cookie is not None:
        headers.append(("cookie", f"isbe_session={cookie}"))
    req = Request(method, path, query, headers, client)
    if method in ("POST", "PUT", "DELETE"):
        data = raw if raw is not None else (b"" if body is None else json.dumps(body).encode())
        req.body = BodyState.TOO_LARGE if len(data) > console.limits.body_bytes else data
    reply = Endpoint(console)._handle(req)
    try:
        parsed: Any = json.loads(reply.body)
    except ValueError:
        parsed = reply.body.decode("utf-8", "replace")
    return Result(reply.status, parsed, reply.headers, reply.body)


def add_user(root: Path, name: str, admin: bool = False) -> auth.User:
    with closing(db.connect(root / "isbe.db")) as conn:
        return auth.create_user(conn, name, PASSWORD, admin)


def login(console: Console, name: str) -> str:
    result = call(console, "POST", "/api/login", {"username": name, "password": PASSWORD})
    assert result.status == 200, result.body
    cookie = result.header("Set-Cookie") or ""
    return cookie.split("isbe_session=", 1)[1].split(";", 1)[0]


class LiveServer:
    """The console on uvicorn, with the protocol and limits `serve` uses."""

    def __init__(self, root: Path, limits: Limits, trusted: TrustedProxies) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        app = create_app(
            root, token=TOKEN, local_port=self.port, generation=FakeManager(), limits=limits, trusted_proxies=trusted
        )
        self.console: Console = app.state.console
        self.server = uvicorn.Server(server_config(app, limits, trusted))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [sock]}, daemon=True)
        self.sock = sock
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            assert time.monotonic() < deadline, "server did not start"
            time.sleep(0.01)

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self.port}"

    def connect(self, timeout: float = 5) -> socket.socket:
        return socket.create_connection(("127.0.0.1", self.port), timeout=timeout)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(10)
        self.sock.close()


@pytest.fixture
def live(data: Path) -> Iterator[Any]:
    """`live(limits, trusted)` starts a server on `data`; all stop after the test."""
    servers: list[LiveServer] = []

    def start(limits: Limits | None = None, trusted: TrustedProxies | None = None) -> LiveServer:
        server = LiveServer(data, limits or Limits(), trusted or TrustedProxies())
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.stop()
