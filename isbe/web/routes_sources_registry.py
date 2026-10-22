"""Configured sources, their materials and source jobs."""

import re
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from isbe.sources import registry

from .routes_sources_body import (
    BATCH,
    COLLECT,
    DISCOVER,
    IMPORT,
    SAVE,
    body,
    source,
    source_revision,
)
from .routes_sources_reply import offset, respond, segment, valid_id
from .wire import Reply, Request, not_found

# Material ids are SHA-256 hex digests; tests and older rows use short slugs.
_MATERIAL_ID = re.compile(r"[0-9A-Za-z_-]{1,128}")


def catalog(conn: Connection, req: Request, token: str) -> Reply:
    start = offset(req, "Invalid source offset")
    query = registry.CatalogQuery(
        q=req.arg("q"), site=req.arg("site"), topic=req.arg("topic"), status=req.arg("status"), offset=start
    )

    def page() -> dict[str, Any]:
        value = registry.catalog(conn, query)
        value["csrf_token"] = token
        return value

    return respond(page)


def export(conn: Connection) -> Reply:
    return respond(lambda: registry.export(conn))


def jobs(conn: Connection) -> Reply:
    return respond(lambda: {"jobs": registry.jobs(conn)})


def materials(conn: Connection, req: Request) -> Reply:
    start = offset(req, "Invalid material offset")
    return respond(lambda: registry.materials_filtered(conn, req.arg("source"), start, req.arg("date_status")))


def timeline(conn: Connection, req: Request) -> Reply:
    """GET /api/sources/materials/<id>/timeline."""
    id = segment(req.path, "/api/sources/materials/", "/timeline")
    if not _MATERIAL_ID.fullmatch(id):
        return not_found()
    return respond(lambda: registry.timeline(conn, id))


def create(conn: Connection, req: Request) -> Reply:
    input = body(req, SAVE)
    config = source(input["config"])
    return respond(lambda: registry.save(conn, config, input["revision"], None))


def import_sources(conn: Connection, req: Request) -> Reply:
    sources = [source(item) for item in body(req, IMPORT)["sources"]]
    return respond(lambda: registry.import_(conn, sources))


def batch(conn: Connection, req: Request) -> Reply:
    input = body(req, BATCH)
    sources = [source_revision(item) for item in input["sources"]]
    return respond(lambda: registry.batch(conn, sources, input["enabled"]))


def start(data: Path, operation: registry.Operation, request_id: str) -> Reply:
    """Start `operation` and answer `{"job": ...}`."""
    return respond(lambda: {"job": registry.start(data, operation, request_id)})


def discover(data: Path, req: Request) -> Reply:
    input = body(req, DISCOVER)
    operation = registry.Discover(input["url"], input["topic"], input["auto_enable"])
    return start(data, operation, input["request_id"])


def update(conn: Connection, req: Request) -> Reply:
    """PUT /api/sources/<id>: save `<id>` at the revision the user saw."""
    id = segment(req.path, "/api/sources/")
    if not valid_id(id):
        return not_found()
    input = body(req, SAVE)
    config = source(input["config"])
    return respond(lambda: registry.save(conn, config, input["revision"], id))


def collect(data: Path, req: Request) -> Reply:
    """POST /api/sources/<id>/collect."""
    id = segment(req.path, "/api/sources/", "/collect")
    if not valid_id(id):
        return not_found()
    return start(data, registry.Collect(id), body(req, COLLECT)["request_id"])


def detail(conn: Connection, req: Request) -> Reply:
    """GET /api/sources/config/<id>: ids such as `jobs` or `export` cannot shadow a route here."""
    id = segment(req.path, "/api/sources/config/")
    if not valid_id(id):
        return not_found()
    return respond(lambda: registry.get(conn, id))
