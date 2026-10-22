"""The source registry's routes.

`route` is where the console hands them over: after `shared.route` has
checked the Host, CSRF token and content type, the login, the administrator
role (members get 403) and `?owner=` (reads only), it checks the guards
again and calls the `SourcesRoute` passed
as `create_app(..., sources=...)`, which is `api` here (`cli._serve` passes
it); without one every request answers 404.

`api` answers:

- reads: `GET /api/sources` (catalog page; `q`, `site`, `topic`, `status`,
  `offset`), `/export`, `/jobs`, `/materials` (`source`, `date_status`,
  `offset`), `/materials/<id>/timeline` (every material sharing the
  article identity, newest first), `/config/<id>`, `/search` (plans and
  provider readiness);
- writes: `POST /api/sources` (create), `PUT /api/sources/<id>`,
  `POST /import`, `/batch`, `POST /search` (save a plan);
- jobs, started with `registry.start` on a daemon thread and replayed by
  request id: `POST /discover`, `/<id>/collect`, `/search/run`,
  `/search/<plan>/recheck`;
- `POST /search/<plan>/weekly` (a media week, synchronously) and
  `POST /article` (one article's original text).

Every request first marks jobs a stopped process left running as
interrupted (`registry.recover`). Bodies are decoded by
routes_sources_body.py; errors are `{"error": message}` with the status
the registry gives, 500 for anything else.
"""

from pathlib import Path
from sqlite3 import Connection

from isbe.sources import registry

from . import routes_sources_articles as articles
from . import routes_sources_registry as sources
from . import routes_sources_search as search
from .console import Console
from .routes_sources_reply import failure
from .wire import Reply, Request, error, not_found


def route(console: Console, conn: Connection, data: Path, req: Request) -> Reply:
    if console.sources is None:
        return not_found()
    if (refused := console.access.rejects(req)) is not None:
        return refused
    return console.sources(console, conn, data, req)


def api(console: Console, conn: Connection, data: Path, req: Request) -> Reply:
    """The `/api/sources*` routes for workspace `data` and its database `conn`."""
    try:
        registry.recover(data)
    except Exception as e:
        return failure(e)
    method, path, token = req.method, req.path, console.access.token
    match method, path:
        case "POST", "/api/sources/article":
            return articles.expand(conn, data, req)
        case "GET", "/api/sources/search":
            return search.plans(conn, data, token)
        case "POST", "/api/sources/search":
            return search.save(conn, req)
        case "POST", "/api/sources/search/run":
            return search.run(data, req)
        case "GET", "/api/sources":
            return sources.catalog(conn, req, token)
        case "GET", "/api/sources/export":
            return sources.export(conn)
        case "GET", "/api/sources/jobs":
            return sources.jobs(conn)
        case "GET", "/api/sources/materials":
            return sources.materials(conn, req)
        case "POST", "/api/sources":
            return sources.create(conn, req)
        case "POST", "/api/sources/import":
            return sources.import_sources(conn, req)
        case "POST", "/api/sources/batch":
            return sources.batch(conn, req)
        case "POST", "/api/sources/discover":
            return sources.discover(data, req)
    if method == "GET" and path.startswith("/api/sources/materials/"):
        return sources.timeline(conn, req)
    plan = path.startswith("/api/sources/search/")
    if method == "POST" and plan and path.endswith("/weekly"):
        return search.weekly(conn, data, req)
    if method == "POST" and plan and path.endswith("/recheck"):
        return search.recheck(data, req)
    if method == "PUT":
        return sources.update(conn, req)
    if method == "POST" and path.endswith("/collect"):
        return sources.collect(data, req)
    if method == "GET":
        return sources.detail(conn, req)
    return error(405, "Method not allowed")
