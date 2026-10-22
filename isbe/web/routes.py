"""Read routes, the UI and its files.

Reached from `shared.route` with a workspace's data directory and database
(the service root's for `/api/settings` and `/help`). `/api/topics`, `/api/mail`,
`/api/generation` and `/api/overview` never reach this far: `shared` answers
them.
"""

from pathlib import Path
from sqlite3 import Connection

from isbe.reports import library
from isbe.reports.research import evidence, original

from . import help_pages, routes_sources, views
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
        case "/help":
            page = help_pages.page(arg("doc") or "readme")
            return not_found() if page is None else asset(_HTML, page)
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
    """Paths outside `/api/`, `/figures/` and `/help`: the UI's files, with no
    Host check (they hold no data), and its page for every other path."""
    if req.method != "GET":
        return method_not_allowed()
    if req.path == "/app.js":
        return asset("text/javascript; charset=utf-8", console.static.app_js)
    if req.path == "/app.css":
        return asset("text/css; charset=utf-8", console.static.app_css)
    return asset(_HTML, console.static.index)
