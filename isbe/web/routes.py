"""Read routes, the UI and its files.

Reached from `shared.route` with a workspace's data directory and database
(the service root's for `/api/settings` and `/help/service`). `/api/topics`, `/api/mail`,
`/api/generation`, `/api/overview` and `/api/home` never reach this far: `shared` answers
them.
"""

from pathlib import Path
from sqlite3 import Connection

from isbe.reports import library
from isbe.reports.research import evidence, original

from . import routes_sources, views
from .console import Console
from .pages import figure, research_evidence_page
from .routes_config import settings_route
from .wire import (
    Reply,
    Request,
    asset,
    json_reply,
    method_not_allowed,
    not_found,
    reply,
)

_HTML = "text/html; charset=utf-8"
# The evidence page's own policy, plus the framing and base/form
# restrictions every other response gets.
EVIDENCE_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)
# A frozen original is someone else's document: never rendered as part of the console.
ORIGINAL_CSP = "default-src 'none'; sandbox; frame-ancestors 'none'"

# Pages the React console (frontend/) answers; every other page is still the
# classic console in web/. A path moves here when its page moves over; keep
# `NAV` in frontend/src/shell/nav.ts in step.
REACT_PATHS = ("/question", "/galaxies", "/settings", "/system", "/help")
# Home is the React console too, but "/" only matches itself.
REACT_HOME = "/"
# The classic console's pages, exactly (`PAGES` in web/app.js; a test keeps
# them equal). A path that is neither these nor a React page gets the React
# console's not-found page with a 404 status, not the classic overview.
CLASSIC_PATHS = (
    "/overview", "/reports", "/triage", "/runs", "/topics", "/tasks", "/users", "/sources",
)
# The React build's files carry a content hash in their names.
_IMMUTABLE = "public, max-age=31536000, immutable"
_UNBUILT = (
    '<!doctype html><html lang="en"><meta charset="utf-8"><title>ISBE</title>'
    "<p>This page needs the React console, which is not built. Run "
    "<code>npm --prefix frontend ci &amp;&amp; npm --prefix frontend run build</code>, "
    "then restart <code>isbe serve</code>.</p><p>此页面需要 React 前端，但尚未构建。"
    '<a href="/">ISBE</a></p></html>'
).encode()


# Pages under these prefixes are one more path segment (a run's id).
REACT_PREFIXES = ("/question/runs/",)


def react_page(path: str) -> bool:
    """Whether the React console routes `path` (`Pages` in frontend/src/App.tsx)."""
    if path == REACT_HOME or path.rstrip("/") in REACT_PATHS:
        return True
    return any(path.startswith(p) and "/" not in path.removeprefix(p).rstrip("/") and path != p for p in REACT_PREFIXES)


def _found(value: object) -> Reply:
    return not_found() if value is None else json_reply(200, value)


def route(console: Console, conn: Connection, data: Path, req: Request) -> Reply:
    path = req.path
    if path == "/api/settings":
        return settings_route(console, data, req)
    if path == "/api/sources" or path.startswith("/api/sources/"):
        return routes_sources.route(console, conn, data, req)
    if req.method != "GET":
        return method_not_allowed()
    arg = req.arg
    match path:
        case "/help/service":
            # The service guide as a page: how the service is set up and run.
            return asset(_HTML, console.static.help)
        case "/api/reports":
            return json_reply(200, library.library(conn, data))
        case "/api/report":
            return _found(
                library.report(conn, data, arg("topic"), arg("period"), articles=console.articles)
            )
        case "/api/research/evidence":
            found = evidence(conn, data, arg("topic"), arg("period"), arg("id"))
            if found is None:
                return not_found()
            page = research_evidence_page(found, req.args).encode("utf-8")
            return asset(_HTML, page).with_header("Content-Security-Policy", EVIDENCE_CSP)
        case "/api/research/original":
            frozen = original(conn, data, arg("topic"), arg("period"), arg("id"))
            if frozen is None:
                return not_found()
            mime, body = frozen
            return (
                reply(200, mime, body)
                .with_header("Content-Disposition", "attachment")
                .with_header("X-Content-Type-Options", "nosniff")
                .with_header("Content-Security-Policy", ORIGINAL_CSP)
            )
        case "/api/report.md":
            report = library.report_file(conn, data, arg("topic"), arg("period"))
            if report is None:
                return not_found()
            meta, md = report
            return reply(200, "text/markdown; charset=utf-8", md.encode("utf-8")).with_header(
                "Content-Disposition", f'attachment; filename="{library.download_name(meta)}"'
            )
        case "/api/triage":
            return _found(views.triage(conn, data, arg("topic")))
        case "/api/runs":
            return json_reply(200, views.runs(conn))
        case "/api/call":
            return _found(views.call(conn, arg("id")))
    if path.startswith("/figures/"):
        return figure(data, path.removeprefix("/figures/"))
    return not_found()


def ui(console: Console, req: Request) -> Reply:
    """Paths outside `/api/`, `/figures/` and `/help/service`: the UI's files, with no
    Host check (they hold no data), and a console's page for every other path:
    the classic one for `CLASSIC_PATHS`, the React one otherwise (with 404
    for a path it does not route)."""
    if req.method != "GET":
        return method_not_allowed()
    if req.path == "/app.js":
        return asset("text/javascript; charset=utf-8", console.static.app_js)
    if req.path == "/app.css":
        return asset("text/css; charset=utf-8", console.static.app_css)
    if req.path.startswith("/app/"):
        build = console.static.app
        found = build.files.get(req.path.removeprefix("/app/")) if build else None
        if found is None:
            return not_found()
        kind, body = found
        return reply(200, kind, body).with_header("Cache-Control", _IMMUTABLE)
    if req.path in CLASSIC_PATHS:
        return asset(_HTML, console.static.index)
    build = console.static.app
    if build is None:
        status = 503 if react_page(req.path) else 404
        return reply(status, _HTML, _UNBUILT).with_header("Cache-Control", "no-store")
    if react_page(req.path):
        return asset(_HTML, build.index)
    # The React console draws its not-found page for any path it does not route.
    return reply(404, _HTML, build.index).with_header("Cache-Control", "no-cache")
