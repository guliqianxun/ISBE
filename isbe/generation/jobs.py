"""The `tasks` rows of the root database and the records the console sees.

`Job.to_json()` is the task object of `/api/generation` and `/api/tasks`;
`StartError.to_json()` the body of a refused submit or cancel, sent with
`StartError.status`. Both serialize with the same keys in the same order,
absent values as `null`, and never the stored topic snapshot or settings
fingerprint.
"""

import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Report:
    """The report a succeeded task produced: its topic and period."""

    topic: str
    period: str

    def to_json(self) -> dict[str, Any]:
        return {"topic": self.topic, "period": self.period}


@dataclass(frozen=True)
class Job:
    """One queued task. `state` is queued, running, succeeded, failed,
    interrupted or cancelled; `stage` is queued, collect, triage, enrich or
    digest; `position` is the 1-based place among queued tasks (None once the
    task left the queue); `error` is a safe, user-facing message."""

    id: str
    owner: str
    topic: str
    state: str
    stage: str
    error: str | None
    result: Report | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    position: int | None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner": self.owner,
            "topic": self.topic,
            "state": self.state,
            "stage": self.stage,
            "error": self.error,
            "result": None if self.result is None else self.result.to_json(),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "position": self.position,
        }


class StartError(Exception):
    """A refused submit or cancel. The web layer answers `status` with `to_json()`.

    | status | code | error |
    | --- | --- | --- |
    | 400 | invalid_owner | Choose a valid account. |
    | 400 | invalid_topic | Choose a valid topic. / The topic configuration is invalid. |
    | 400 or 404 | invalid_topic | The topic is missing or invalid. Review and save it in Topics. |
    | 400 | invalid_request | A valid request identifier is required. |
    | 409 | request_conflict | This request identifier already belongs to another topic. |
    | 429 | queue_limit | This account already has five pending tasks. ... |
    | 400 | workspace | The account workspace is unavailable. |
    | 400 | configuration | Service settings could not be loaded. ... / Model configuration is ... |
    | 400 | unsupported_topic | Reports currently require an arXiv paper topic. / Shared topics must ... |
    | 404 | not_found | Task not found. (cancel) |
    | 409 | not_queued | Only queued tasks can be cancelled. (cancel) |
    | 500 | queue | The task queue is unavailable. Try again later. |
    """

    def __init__(self, status: int, code: str, error: str, job: Job | None = None) -> None:
        super().__init__(error)
        self.status = status
        self.code = code
        self.error = error
        self.job = job

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "error": self.error}
        if self.job is not None:
            body["job"] = self.job.to_json()
        return body


def unavailable() -> StartError:
    return StartError(500, "queue", "The task queue is unavailable. Try again later.")


SELECT = (
    "SELECT id, owner, topic, state, stage, error, result_period, created_at, started_at, "
    "finished_at, CASE WHEN state = 'queued' THEN (SELECT count(*) FROM tasks q "
    "WHERE q.state = 'queued' AND q.seq <= tasks.seq) END FROM tasks"
)


def from_row(row: sqlite3.Row | tuple[Any, ...]) -> Job:
    (id, owner, topic, state, stage, error, period, created, started, finished, position) = tuple(row)
    return Job(
        id=id,
        owner=owner,
        topic=topic,
        state=state,
        stage=stage,
        error=error,
        result=None if period is None else Report(topic, period),
        created_at=created,
        started_at=started,
        finished_at=finished,
        position=position,
    )


def lookup(conn: sqlite3.Connection, id: str) -> Job | None:
    row = conn.execute(f"{SELECT} WHERE id = ?", (id,)).fetchone()
    return None if row is None else from_row(row)


def require(conn: sqlite3.Connection, id: str) -> Job:
    """A task this transaction just read or wrote; its absence is a broken queue."""
    job = lookup(conn, id)
    if job is None:
        raise LookupError("missing task")
    return job


def trusted_owner(conn: sqlite3.Connection, owner: str) -> bool:
    """The local owner or an active admin, read from the database each time:
    an admin can lose the role between submit and execution."""
    if owner == "local":
        return True
    row = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM auth_users WHERE id = ? AND role = 'admin' AND active = 1)",
        (owner,),
    ).fetchone()
    return bool(row[0])
