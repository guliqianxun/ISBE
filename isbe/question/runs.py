"""Question runs and their event stream. Sole writer of `question_runs` and
`question_events`.

A run is one research question being worked on; it starts `queued`. Every
change a client shows is an event (`question_event@1`) with `seq` 1, 2, … per
run, so a client that reconnects asks for the events after the last seq it saw.
Stages add their own events through `emit`; a status change is `set_status`,
which also emits the `state` event. An event's `paper` record is stored once in
`question.papers` and put back when the event is read.
"""

import secrets
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from isbe.core import db, jsonfmt
from isbe.core.errors import HttpError
from isbe.question import papers

EVENT = "question_event@1"
STATUSES = ("queued", "running", "paused", "cancelled", "failed", "done")
ENDED = ("cancelled", "failed", "done")
# Every workspace table that holds a run's rows, children before parents.
_TABLES = (
    "question_graphs", "question_citations", "screen_overrides", "screen_verdicts", "set_ops", "paper_sets", "question_hits",
    "question_queries", "question_plans", "question_events", "question_runs",
)


def create(conn: sqlite3.Connection, question: str, lang: str, *, run_id: str | None = None) -> str:
    """A new queued run for `question`; its id (`run-<yyyymmdd>-<hex>` unless given)."""
    if lang not in ("zh", "en"):
        raise HttpError(400, "The question language must be zh or en")
    run_id = run_id or f"run-{datetime.now(UTC):%Y%m%d}-{secrets.token_hex(4)}"
    at = db.now()
    with db.immediate(conn):
        conn.execute(
            "INSERT INTO question_runs (run_id, question, lang, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'queued', ?, ?)",
            (run_id, question, lang, at, at),
        )
        _append(conn, run_id, [("state", {"state": "queued"})])
    return run_id


def get(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    """The run's row, or `HttpError(404)`."""
    row = conn.execute(
        "SELECT run_id, question, lang, status, created_at, updated_at FROM question_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise HttpError(404, "No such question run")
    return dict(zip(("run_id", "question", "lang", "status", "created_at", "updated_at"), row, strict=True))


def delete(conn: sqlite3.Connection, run_id: str) -> None:
    """Remove the run and every row of it, in one transaction. The one place rows of
    the stage tables are deleted: their own writers only append."""
    with db.immediate(conn):
        get(conn, run_id)
        # Paper records are shared by runs; only those no other run uses go.
        used = papers.forget(conn, run_id)
        for table in _TABLES:
            conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
        papers.prune(conn, used)


def set_status(conn: sqlite3.Connection, run_id: str, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown run status {status!r}")
    with db.immediate(conn):
        changed = conn.execute(
            "UPDATE question_runs SET status = ?, updated_at = ? WHERE run_id = ?", (status, db.now(), run_id)
        ).rowcount
        if not changed:
            raise HttpError(404, "No such question run")
        _append(conn, run_id, [("state", {"state": status})])


def emit(conn: sqlite3.Connection, run_id: str, events: Iterable[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Append `(type, data)` events to the run's stream in one transaction; the
    `question_event@1` documents written."""
    with db.immediate(conn):
        return _append(conn, run_id, events)


def events(conn: sqlite3.Connection, run_id: str, after: int = 0) -> list[dict[str, Any]]:
    """The run's events with seq after `after`, in order."""
    with db.snapshot(conn):
        rows = conn.execute(
            "SELECT seq, at, type, data FROM question_events WHERE run_id = ? AND seq > ? ORDER BY seq",
            (run_id, after),
        ).fetchall()
        data = papers.full(conn, [jsonfmt.loads(d) for *_, d in rows])
    return [_event(run_id, seq, at, kind, d) for (seq, at, kind, _), d in zip(rows, data, strict=True)]


def ended(conn: sqlite3.Connection, run_id: str) -> int | None:
    """The seq of the run's `done` event, None while it has none."""
    return conn.execute(
        "SELECT min(seq) FROM question_events WHERE run_id = ? AND type = 'done'", (run_id,)
    ).fetchone()[0]


def _append(
    conn: sqlite3.Connection, run_id: str, events: Iterable[tuple[str, dict[str, Any]]]
) -> list[dict[str, Any]]:
    seq = conn.execute("SELECT coalesce(max(seq), 0) FROM question_events WHERE run_id = ?", (run_id,)).fetchone()[0]
    events = list(events)
    stored = papers.slim(conn, run_id, [data for _, data in events])
    out = []
    for (kind, data), slim in zip(events, stored, strict=True):
        seq += 1
        event = _event(run_id, seq, db.now(), kind, data)
        conn.execute(
            "INSERT INTO question_events (run_id, seq, at, type, data) VALUES (?, ?, ?, ?, ?)",
            (run_id, seq, event["at"], kind, jsonfmt.dumps(slim, sort_keys=False)),
        )
        out.append(event)
    return out


def _event(run_id: str, seq: int, at: str, kind: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"schema": EVENT, "run_id": run_id, "seq": seq, "at": at, "type": kind, "data": data}
