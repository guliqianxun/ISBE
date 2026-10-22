"""Durable source operations: `Operation`, `job`, `jobs`, `recover`, `start`.

`start` is idempotent per request id: the same id with the same operation
returns the existing job, a different operation is refused. Each operation
runs on its own thread holding the execution lock, with the settings
snapshot taken when it started. A job left running by a stopped process is
marked interrupted by `recover`, which succeeds only when no process holds
the lock (the OS releases it after a crash).

Search and recheck runs live in `isbe.search`, which depends on this module;
it is imported only where an operation needs it.
"""

import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from isbe.core import db, jsonfmt, locks, settings
from isbe.core.errors import HttpError, describe
from isbe.core.validators import is_control, trim

from . import reader
from .registry_config import get, loads, message

if TYPE_CHECKING:
    from isbe.search.plans import SearchPlan

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Collect:
    source_id: str
    name: ClassVar[str] = "collect"

    def to_json(self) -> dict[str, Any]:
        return {"operation": self.name, "source_id": self.source_id}


@dataclass(frozen=True)
class Discover:
    url: str
    topic: str
    auto_enable: bool
    name: ClassVar[str] = "discover"

    def to_json(self) -> dict[str, Any]:
        return {"operation": self.name, "url": self.url, "topic": self.topic, "auto_enable": self.auto_enable}


@dataclass(frozen=True)
class Search:
    plan: SearchPlan
    name: ClassVar[str] = "search"

    def to_json(self) -> dict[str, Any]:
        return {"operation": self.name, "plan": self.plan.to_json()}


@dataclass(frozen=True)
class Recheck:
    plan_id: str
    after: str | None = None
    name: ClassVar[str] = "recheck"

    def to_json(self) -> dict[str, Any]:
        return {"operation": self.name, "plan_id": self.plan_id, "after": self.after}


type Operation = Collect | Discover | Search | Recheck


def fingerprint(operation: Operation) -> str:
    """SHA-256 of the operation's JSON form (tagged by kind, fields in declaration order)."""
    return db.sha256(jsonfmt.dumps(operation.to_json(), sort_keys=False))


def validate(conn: sqlite3.Connection, operation: Operation) -> Operation:
    """The operation with its inputs normalized, or `HttpError` naming what is wrong."""
    from isbe import search

    match operation:
        case Collect(source_id):
            get(conn, source_id)
            return operation
        case Discover(url, topic, auto_enable):
            url = reader.validate_url(url)
            topic = trim(topic)
            if not topic or len(topic) > 120 or any(is_control(c) for c in topic):
                raise HttpError(400, "Enter a topic of 1–120 visible characters")
            return Discover(url, topic, auto_enable)
        case Search(plan):
            return Search(plan.validated())
        case Recheck(plan_id, after):
            search.validate_recheck(conn, plan_id, after)
            return operation
    raise TypeError(f"not a source operation: {operation!r}")


def job(conn: sqlite3.Connection, id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id,operation,state,created_at,finished_at,stats,error FROM source_jobs WHERE id=?1", (id,)
    ).fetchone()
    if row is None:
        raise HttpError(404, "Source job not found")
    return {
        "id": row[0],
        "operation": row[1],
        "state": row[2],
        "created_at": row[3],
        "finished_at": row[4],
        "stats": loads(row[5]),
        "error": row[6],
    }


def jobs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The twenty most recent jobs, newest first."""
    ids = [r[0] for r in conn.execute("SELECT id FROM source_jobs ORDER BY created_at DESC,id DESC LIMIT 20")]
    return [job(conn, id) for id in ids]


def interrupted(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE search_plans SET last_checked=?1,last_status='interrupted' WHERE id IN "
        "(SELECT r.plan_id FROM search_runs r JOIN source_jobs j ON j.id=r.id "
        "WHERE j.state='running' AND j.operation='search')",
        (db.now(),),
    )
    conn.execute(
        "UPDATE search_requests SET state='interrupted',error='The service stopped before this search finished' "
        "WHERE state='running' AND run_id IN (SELECT id FROM source_jobs WHERE state='running')"
    )
    conn.execute(
        "UPDATE source_jobs SET state='interrupted',finished_at=?1,error='The service stopped before this "
        "operation finished; retry with a new request ID.' WHERE state='running'",
        (db.now(),),
    )


def recover(data: Path) -> bool:
    """Mark jobs of a stopped process interrupted; false while an operation holds the lock."""
    try:
        guard = locks.execution_lock(data)
    except (OSError, ValueError):
        return False
    with guard:
        conn = db.connect(data / "isbe.db")
        try:
            interrupted(conn)
        finally:
            conn.close()
    return True


def _valid_request_id(id: str) -> bool:
    return 0 < len(id) <= 128 and all(c.isascii() and (c.isalnum() or c in "-_") for c in id)


def start(data: Path, operation: Operation, request_id: str) -> dict[str, Any]:
    """Start `operation` as job `request_id` and return the job (state `running`), or
    the existing job when this request id already ran the same operation."""
    from isbe import search

    if not _valid_request_id(request_id):
        raise HttpError(400, "Use an alphanumeric request ID of at most 128 characters")
    conn = db.connect(data / "isbe.db")
    try:
        operation = validate(conn, operation)
        key = fingerprint(operation)
        previous = conn.execute(
            "SELECT fingerprint FROM source_jobs WHERE id=?1", (request_id,)
        ).fetchone()
        if previous is not None:
            if previous[0] != key:
                raise HttpError(409, "This request ID belongs to a different operation")
            return job(conn, request_id)
        try:
            guard = locks.execution_lock(data)
        except (OSError, ValueError):
            raise HttpError(
                409, "Another collection or report operation is running. Wait and try again."
            ) from None
        try:
            saved = settings.for_task(data)
            if isinstance(operation, Search):
                operation = Search(search.validate_saved(conn, operation.plan))
                with settings.task_scope(saved):
                    search.preflight(conn, operation.plan)
            interrupted(conn)
            with db.immediate(conn):
                if isinstance(operation, Search):
                    operation = Search(search.validate_saved(conn, operation.plan))
                conn.execute(
                    "INSERT INTO source_jobs(id,fingerprint,operation,state,created_at) "
                    "VALUES (?1,?2,?3,'running',?4)",
                    (request_id, key, operation.name, db.now()),
                )
                if isinstance(operation, Search):
                    search.snapshot(conn, request_id, operation.plan)
                if isinstance(operation, Recheck):
                    search.snapshot_recheck(conn, request_id, operation.plan_id, operation.after)
            result = job(conn, request_id)
            worker = threading.Thread(
                target=_work,
                args=(data, operation, request_id, saved, guard),
                name="source-collection",
                daemon=True,
            )
            worker.start()
        except BaseException:
            guard.release()
            raise
        return result
    finally:
        conn.close()


def execute(conn: sqlite3.Connection, operation: Operation, id: str) -> Any:
    """Run a started operation; returns its stats."""
    from isbe import search

    from .registry_discover import discover
    from .registry_materials import collect

    match operation:
        case Collect(source_id):
            return collect(conn, source_id)
        case Discover(url, topic, auto_enable):
            return discover(conn, url, topic, auto_enable)
        case Search(plan):
            return search.run(conn, id, plan)
        case Recheck():
            return search.recheck(conn, id)
    raise TypeError(f"not a source operation: {operation!r}")


def outcome(stats: Any) -> str:
    """The job state for an operation's stats."""
    fields = stats if isinstance(stats, dict) else {}
    failures = fields.get("failed")
    if fields.get("state") == "failed":
        return "failed"
    if fields.get("state") == "partial" or (type(failures) is int and failures > 0):
        return "partial"
    return "succeeded"


def _work(data: Path, operation: Operation, id: str, saved: settings.Values, guard: locks.Lock) -> None:
    try:
        with settings.task_scope(saved):
            conn = db.connect(data / "isbe.db")
            try:
                error: str | None = None
                try:
                    stats = execute(conn, operation, id)
                    state = outcome(stats)
                except Exception as failure:
                    state, error = "failed", message(failure)[:1000]
                    stats = job(conn, id)["stats"]
                conn.execute(
                    "UPDATE source_jobs SET state=?2,stats=?3,error=?4,finished_at=?5 WHERE id=?1",
                    (id, state, jsonfmt.dumps(stats), error, db.now()),
                )
            finally:
                conn.close()
    except Exception as failure:
        log.error("source operation did not complete: %s", describe(failure))
    finally:
        guard.release()
