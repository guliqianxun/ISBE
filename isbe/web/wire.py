"""Requests and replies as the routes see them, plus the JSON body and form helpers.

A route is synchronous: it runs in a worker thread with the request body
already read (or deliberately not read) by the ASGI layer, so it never
blocks the event loop and never waits on a slow client.
"""

import enum
import gzip
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any
from urllib.parse import parse_qsl, quote_plus

from isbe.core import jsonfmt

# One limit for every JSON body.
BODY_LIMIT = 64 * 1024
# A document nested this deep is refused.
_MAX_DEPTH = 128


class BodyState(enum.Enum):
    """Why a request has no usable body bytes."""

    NOT_READ = "not read"  # the guards refused the request before any body was read
    TOO_LARGE = "too large"
    BROKEN = "broken"  # the client went away mid-body


@dataclass
class Request:
    method: str
    # The request target before `?`, percent-encoding intact.
    path: str
    query: str
    # Lowercase names, in arrival order; values decoded as Latin-1.
    headers: list[tuple[str, str]]
    # The client address used for login throttling (see guards.client_address).
    client: str
    body: bytes | BodyState = BodyState.NOT_READ

    def header(self, name: str) -> str | None:
        """The first header called `name`."""
        name = name.lower()
        return next((v for k, v in self.headers if k == name), None)

    @cached_property
    def args(self) -> dict[str, str]:
        """The query parsed into a map: the last value wins."""
        return dict(parse_qsl(self.query, keep_blank_values=True))

    def arg(self, name: str) -> str:
        return self.args.get(name, "")

    @property
    def url(self) -> str:
        return f"{self.path}?{self.query}" if self.query else self.path


@dataclass
class Stream:
    """A reply body sent in parts, for server-sent events. The ASGI layer calls
    `poll()` in a worker thread every `interval` seconds while the client stays
    connected: it returns the next bytes (b"" when there is nothing new) or None
    to end the reply. A reply ends at the latest after `seconds`; event-stream
    clients then reconnect on their own."""

    poll: Callable[[], bytes | None]
    interval: float = 0.5
    seconds: float = 300.0
    # Sent after this long with nothing else, so proxies keep the connection.
    heartbeat: float = 15.0
    # Called once the reply has ended, however it ended (frees what `poll` holds).
    close: Callable[[], None] | None = None


@dataclass
class Reply:
    status: int
    body: bytes = b""
    headers: list[tuple[str, str]] = field(default_factory=list)
    # When set, `body` is sent first and the stream follows (no Content-Length).
    stream: Stream | None = None

    def with_header(self, name: str, value: str) -> Reply:
        self.headers.append((name, value))
        return self

    def get(self, name: str) -> str | None:
        name = name.lower()
        return next((v for k, v in self.headers if k.lower() == name), None)

    def set(self, name: str, value: str) -> Reply:
        lower = name.lower()
        self.headers = [(k, v) for k, v in self.headers if k.lower() != lower]
        self.headers.append((name, value))
        return self


class Respond(Exception):
    """Answer with `reply` now, from anywhere inside a route."""

    def __init__(self, reply: Reply) -> None:
        super().__init__(reply.status)
        self.reply = reply


def reply(status: int, content_type: str, body: bytes) -> Reply:
    return Reply(status, body, [("Content-Type", content_type)])


def json_reply(status: int, value: Any) -> Reply:
    body = jsonfmt.dumps(value).encode("utf-8")
    return reply(status, "application/json; charset=utf-8", body).with_header("Cache-Control", "no-store")


# Replies smaller than this are not worth compressing.
_GZIP_MIN = 1024


def gzipped(req: Request, response: Reply) -> Reply:
    """`response` gzip-compressed when the client accepts it and the body is worth
    it. Only for replies that hold no secret: compressing a secret next to text
    an attacker chose leaks it (BREACH)."""
    if response.stream is not None or response.get("Content-Encoding") or len(response.body) < _GZIP_MIN:
        return response
    response.set("Vary", "Accept-Encoding")
    if not _accepts_gzip(req.header("accept-encoding") or ""):
        return response
    # Level 4: half the time of the default 6 for about 6 % more bytes.
    response.body = gzip.compress(response.body, 4, mtime=0)
    return response.set("Content-Encoding", "gzip")


def _accepts_gzip(header: str) -> bool:
    """Whether `Accept-Encoding` takes gzip: named (or `*`) with a q-value above 0."""
    weights: dict[str, float] = {}
    for part in header.split(","):
        name, *params = (p.strip().lower() for p in part.split(";"))
        q = 1.0
        for param in params:
            if param.startswith("q="):
                try:
                    q = float(param[2:])
                except ValueError:
                    q = 0.0
        if name:
            weights[name] = q
    return weights.get("gzip", weights.get("x-gzip", weights.get("*", 0.0))) > 0


def error(status: int, message: str) -> Reply:
    return json_reply(status, {"error": message})


def not_found() -> Reply:
    return error(404, "not found")


def method_not_allowed() -> Reply:
    return reply(405, "text/plain", b"method not allowed")


def asset(content_type: str, body: bytes) -> Reply:
    """The UI's own files. Browsers check back each time, so a new build shows
    its new UI without a forced reload."""
    return reply(200, content_type, body).with_header("Cache-Control", "no-cache")


INTERNAL = "The request could not be completed. Try again or review the service logs."


def _accepts_strict(value: Any, depth: int = 0) -> bool:
    """Whether strict JSON would accept `value`: no lone surrogate escapes, no deep
    nesting (Python's parser accepts both)."""
    if isinstance(value, str):
        return not any(0xD800 <= ord(c) <= 0xDFFF for c in value)
    if isinstance(value, (list, dict)):
        if depth + 1 >= _MAX_DEPTH:
            return False
        items = value.values() if isinstance(value, dict) else value
        return all(_accepts_strict(v, depth + 1) for v in items) and (
            not isinstance(value, dict) or all(_accepts_strict(k) for k in value)
        )
    return True


def request_json(req: Request) -> Any:
    """The request body as JSON, or `Respond` with
    the same 413/400 answers. Any JSON value is accepted; the caller checks its shape."""
    body = req.body
    if body is BodyState.TOO_LARGE:
        raise Respond(error(413, "Settings request is too large"))
    if not isinstance(body, bytes):
        raise Respond(error(400, "Could not read request body"))
    try:
        value = jsonfmt.loads(body)
    except (ValueError, RecursionError):
        raise Respond(error(400, "Expected a JSON object")) from None
    if not _accepts_strict(value):
        raise Respond(error(400, "Expected a JSON object"))
    return value


# Field kinds for `fields`: a JSON string, a JSON boolean, or any value.
STR, BOOL, ANY = "str", "bool", "any"


def _kind(value: Any, kind: str) -> bool:
    if kind == STR:
        return isinstance(value, str)
    if kind == BOOL:
        return isinstance(value, bool)
    return True


def fields(value: Any, spec: Sequence[tuple[str, str, bool]]) -> dict[str, Any] | None:
    """`value` as an object with exactly the named fields, or None.

    `spec` lists `(name, kind, optional)` in declaration order; an optional
    field may be missing or null (None). An array holding exactly one value
    per field is also accepted, positionally (the form serde accepts). Returns
    None when the value does not fit.
    """
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        names = {name for name, _, _ in spec}
        if any(key not in names for key in value):
            return None
        for name, kind, optional in spec:
            item = value.get(name)
            if item is None:
                if not optional:
                    return None
            elif not _kind(item, kind):
                return None
            out[name] = item
        return out
    if isinstance(value, list) and len(value) == len(spec):
        for (name, kind, optional), item in zip(spec, value, strict=True):
            if item is None and optional:
                out[name] = None
            elif item is None or not _kind(item, kind):
                return None
            else:
                out[name] = item
        return out
    return None


def form_value(value: str) -> str:
    """`application/x-www-form-urlencoded` byte serialization:
    alphanumerics and `*-._` stay, space is `+`, everything else is percent-encoded."""
    return quote_plus(value, safe="*").replace("~", "%7E")


def form_pairs(pairs: Sequence[tuple[str, str]]) -> str:
    return "&".join(f"{form_value(k)}={form_value(v)}" for k, v in pairs)
