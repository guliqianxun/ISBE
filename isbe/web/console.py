"""The console's process-wide state, shared by every request."""

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from sqlite3 import Connection
from typing import TYPE_CHECKING

from isbe import mail
from isbe.core import assets
from isbe.reports.library import Articles

from .generation_api import Manager
from .guards import Access, TrustedProxies

if TYPE_CHECKING:
    from .wire import Reply, Request

# The `/api/sources*` routes, plugged in by `create_app(sources=...)`:
# `route(console, conn, data, req)` for workspace `data` and its database `conn`,
# after the shared-mode guards (login, administrators only, `?owner=` reads).
type SourcesRoute = Callable[["Console", Connection, Path, "Request"], "Reply"]


@dataclass(frozen=True)
class Limits:
    """Bounds on what one client can hold."""

    # A JSON body.
    body_bytes: int = 64 * 1024
    # From the end of the request head to the end of its body.
    body_seconds: float = 10.0
    # From connecting, or from the previous response, to a complete request head.
    header_seconds: float = 10.0
    # Idle keep-alive connections are closed after this.
    keep_alive_seconds: float = 5.0
    # A response the client does not read is dropped after this.
    send_seconds: float = 60.0
    # Open connections in total (uvicorn answers 503 beyond it) and per client address.
    connections: int = 256
    connections_per_peer: int = 32
    # Route handlers running at once in worker threads.
    handlers: int = 16


class Static:
    """The UI's files, read once and kept in memory."""

    @cached_property
    def index(self) -> bytes:
        return assets.path("web", "index.html").read_bytes()

    @cached_property
    def app_js(self) -> bytes:
        return assets.path("web", "app.js").read_bytes()

    @cached_property
    def app_css(self) -> bytes:
        return assets.path("web", "app.css").read_bytes()


@dataclass
class Console:
    root: Path
    shared: bool
    secure_cookies: bool
    access: Access
    generation: Manager
    # One per app: report email counts per account.
    limiter: mail.Limiter = field(default_factory=mail.Limiter)
    # `isbe.reports.media.articles` once the media briefing is wired in.
    articles: Articles | None = None
    sources: SourcesRoute | None = None
    trusted: TrustedProxies = field(default_factory=TrustedProxies)
    limits: Limits = field(default_factory=Limits)
    static: Static = field(default_factory=Static)
