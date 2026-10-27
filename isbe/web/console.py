"""The console's process-wide state, shared by every request."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from sqlite3 import Connection
from typing import TYPE_CHECKING

from isbe import mail
from isbe.core import assets
from isbe.core.markdown import render_markdown
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


# The React build's file types; anything else in it is not served.
_APP_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".json": "application/json; charset=utf-8",
}


@dataclass(frozen=True)
class AppBuild:
    """The React console as `npm run build` leaves it in `isbe/_assets/app`:
    its page and, by path below `/app/`, its content-hashed files."""

    index: bytes
    files: Mapping[str, tuple[str, bytes]]

    @classmethod
    def read(cls, root: Path) -> AppBuild | None:
        try:
            index = (root / "index.html").read_bytes()
        except OSError:
            return None
        files: dict[str, tuple[str, bytes]] = {}
        assets_dir = root / "assets"
        if assets_dir.is_dir():
            for path in sorted(assets_dir.rglob("*")):
                kind = _APP_TYPES.get(path.suffix)
                if kind is None or path.is_symlink() or not path.is_file():
                    continue
                files[path.relative_to(root).as_posix()] = (kind, path.read_bytes())
        return cls(index, files)


@dataclass
class Static:
    """The UI's files, read once and kept in memory. `app_dir` is the React
    build (default: the packaged or checked-out `isbe/_assets/app`)."""

    app_dir: Path | None = None

    @cached_property
    def index(self) -> bytes:
        return assets.path("web", "index.html").read_bytes()

    @cached_property
    def app_js(self) -> bytes:
        return assets.path("web", "app.js").read_bytes()

    @cached_property
    def app_css(self) -> bytes:
        return assets.path("web", "app.css").read_bytes()

    @cached_property
    def app(self) -> AppBuild | None:
        """None when the React console was not built (a checkout without `npm run build`)."""
        root = self.app_dir
        if root is None:
            try:
                root = assets.path("app")
            except FileNotFoundError:
                return None
        return AppBuild.read(root)

    @cached_property
    def help(self) -> bytes:
        # The service guide: configuration, then Docker, upgrades and backups.
        pages = (assets.text("docs", "configuration.md"), assets.text("docs", "deployment.md"))
        return render_markdown("\n\n".join(pages)).encode("utf-8")


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
