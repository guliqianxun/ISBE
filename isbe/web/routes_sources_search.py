"""Search plans, their runs and date rechecks, and the media week built from a plan."""

from pathlib import Path
from sqlite3 import Connection
from typing import Any

from isbe import search
from isbe.core import db, locks, settings
from isbe.core.errors import HttpError
from isbe.reports import media
from isbe.sources import registry

from .routes_sources_body import (
    RECHECK,
    RUN_SEARCH,
    SAVE_SEARCH,
    WEEKLY,
    body,
    search_plan,
)
from .routes_sources_registry import start
from .routes_sources_reply import respond, segment, task_settings, valid_id
from .wire import Reply, Request, not_found

_BUSY = "Another collection or report operation is running. Wait and try again."
_PLAN = "/api/sources/search/"


def plans(conn: Connection, data: Path, token: str) -> Reply:
    """Every plan and the providers' readiness under the settings a run would use."""

    def listed() -> dict[str, Any]:
        with settings.task_scope(task_settings(data)):
            return {"plans": search.list(conn), "providers": search.availability(conn), "csrf_token": token}

    return respond(listed)


def save(conn: Connection, req: Request) -> Reply:
    input = body(req, SAVE_SEARCH)
    plan = search_plan(input["config"])
    revision = input["revision"]
    # With a revision the plan is updated in place, so its id must not change.
    expected = plan.id if revision is not None else None
    return respond(lambda: search.save(conn, plan, revision, expected))


def run(data: Path, req: Request) -> Reply:
    input = body(req, RUN_SEARCH)
    return start(data, registry.Search(search_plan(input["config"])), input["request_id"])


def recheck(data: Path, req: Request) -> Reply:
    """POST /api/sources/search/<plan>/recheck: re-verify the plan's saved dates."""
    id = segment(req.path, _PLAN, "/recheck")
    if not valid_id(id):
        return not_found()
    input = body(req, RECHECK)
    return start(data, registry.Recheck(id, input["after"]), input["request_id"])


def weekly(conn: Connection, data: Path, req: Request) -> Reply:
    """POST /api/sources/search/<plan>/weekly: freeze the plan's saved materials into a
    media week, under the execution lock every collection and report run shares."""
    id = segment(req.path, _PLAN, "/weekly")
    if not valid_id(id):
        return not_found()
    input = body(req, WEEKLY)
    topic, week_end = input["topic_id"], input["week_end"]

    def build() -> dict[str, Any]:
        try:
            guard = locks.execution_lock(data)
        except Exception:
            raise HttpError(409, _BUSY) from None
        with guard:

            def draft(run_id: str, stats: dict[str, Any]) -> None:
                value = media.run(conn, data, id, topic, week_end)
                if isinstance(value, dict):
                    stats.update(value)

            return db.job(conn, "media-weekly", topic, draft)

    return respond(build)
