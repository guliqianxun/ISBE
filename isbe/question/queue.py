"""The queue of question runs. Sole writer of `question_tasks`.

The queue lives in the root database, so the single worker finds every owner's
runs; each run, its events and its results live in the owner's workspace
(`runs`). A run goes through steps: `plan`, then `search` once its plan review
ends (`ready_at`: NULL until a person starts it or runs an edited plan), `screen`
and `finish`. The worker claims the oldest ready step under the execution lock
(`flow.work_one`), so a step never overlaps a CLI command.

People act on a run here: start the search, hold the review, pause, resume, cancel.
What can happen at once is changed at once (a waiting run is paused or cancelled
on the spot, with its `state` event); a step in hand is asked to stop through
`control`, which the worker reads between queries and batches. Every change to
the root database is its own transaction and is committed before any event is
written to the workspace: in local mode both are the same file.

A step left `running` by a worker that died (a restart, a killed CLI) is found
the next time the worker holds the execution lock: nothing else can be running
then, so it is marked paused and resumes where it stopped.
"""

import secrets
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from isbe.core import db, jsonfmt
from isbe.core.errors import HttpError
from isbe.question import runs

# Runs an owner may have waiting, running or paused at once.
ACTIVE_LIMIT = 5
# Seconds after which a task's end in the root database stands even when the run's
# own record of it never followed (a worker killed between its two writes, or a
# workspace write that failed): the run can then be cancelled and deleted.
SETTLE = 30.0
_REQUEST = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


@dataclass(frozen=True)
class Task:
    run_id: str
    owner: str
    state: str
    step: str
    ready_at: str | None
    started: bool
    control: str | None
    options: dict[str, Any]
    updated_at: str = ""

    @property
    def reviewing(self) -> bool:
        """The plan is shown and the search is not due yet (waiting for a person, or a test's countdown)."""
        return (self.state == "waiting" and self.step == "search" and not self.started
                and (self.ready_at is None or self.ready_at > db.now()))


    @property
    def settled(self) -> bool:
        """Its last change is older than `SETTLE`: no worker is still writing it."""
        return self.updated_at <= at(-SETTLE)


_COLUMNS = "run_id, owner, state, step, ready_at, started, control, options, updated_at"


def _task(row: tuple[Any, ...]) -> Task:
    run_id, owner, state, step, ready_at, started, control, options, updated_at = row
    return Task(run_id, owner, state, step, ready_at, bool(started), control, jsonfmt.loads(options), updated_at)


@contextmanager
def _root(root: Path) -> Iterator[sqlite3.Connection]:
    with closing(db.connect(root / "isbe.db")) as conn:
        yield conn


def at(seconds: float = 0.0) -> str:
    """A `db.now()` timestamp `seconds` from now."""
    moment = datetime.now(UTC) + timedelta(seconds=seconds)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def check_request(request_id: str | None) -> str:
    if request_id is None:
        return secrets.token_hex(16)
    if not request_id or len(request_id) > 128 or not set(request_id) <= _REQUEST:
        raise HttpError(400, "A valid request identifier is required")
    return request_id


def requested(root: Path, owner: str, request_id: str) -> str | None:
    """The run an earlier request with this idempotency key created."""
    with _root(root) as conn:
        row = _requested(conn, owner, request_id)
    return row[0] if row else None


def add(
    root: Path, owner: str, run_id: str, options: dict[str, Any], *, step: str = "plan",
    request_id: str | None = None, state: str = "waiting",
) -> Task:
    """Queue run `run_id` (already created in `owner`'s workspace) at `step`, ready now.
    When `request_id` was seen before for `owner`, nothing is queued and the earlier
    run's task is returned: callers compare its `run_id` with theirs. The check, the
    limit and the insert are one transaction, and the database's unique key on
    (owner, request_id) settles any race left."""
    now = db.now()
    with _root(root) as conn, db.immediate(conn):
        if (earlier := _requested(conn, owner, request_id)) is not None:
            return _task(earlier)
        active = conn.execute(
            "SELECT count(*) FROM question_tasks WHERE owner = ? AND state != 'finished'", (owner,)
        ).fetchone()[0]
        if active >= ACTIVE_LIMIT and state == "waiting":
            raise HttpError(429, "This account already has five question runs in progress. "
                                 "Wait for one to finish or cancel one.")
        added = conn.execute(
            "INSERT INTO question_tasks (run_id, owner, request_id, state, step, ready_at, options, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (owner, request_id) DO NOTHING",
            (run_id, owner, request_id, state, step, now, jsonfmt.dumps(options, sort_keys=False), now, now),
        ).rowcount
        if not added:
            return _task(_requested(conn, owner, request_id))
    return get(root, run_id)


def _requested(conn: sqlite3.Connection, owner: str, request_id: str | None) -> tuple[Any, ...] | None:
    if request_id is None:
        return None
    return conn.execute(
        f"SELECT {_COLUMNS} FROM question_tasks WHERE owner = ? AND request_id = ?", (owner, request_id)
    ).fetchone()


def remove(root: Path, run_id: str, stopping: Callable[[Task], bool] = lambda task: False) -> None:
    """Take run `run_id` off the queue (it is being deleted). A step in hand is refused:
    cancel it first. `stopping(task)` says whether the run's workspace has yet to
    catch up with its task; it is asked holding the queue's write lock, so the
    worker cannot end a step between the answer and the delete."""
    with _root(root) as conn, db.immediate(conn):
        row = conn.execute(f"SELECT {_COLUMNS} FROM question_tasks WHERE run_id = ?", (run_id,)).fetchone()
        if row is not None:
            task = _task(row)
            if task.state == "running":
                raise HttpError(409, "This question run is working on a step; cancel it, then delete it")
            if stopping(task):
                raise HttpError(409, "This question run is still stopping; try again in a moment")
        conn.execute("DELETE FROM question_tasks WHERE run_id = ?", (run_id,))


def get(root: Path, run_id: str) -> Task:
    with _root(root) as conn:
        row = conn.execute(f"SELECT {_COLUMNS} FROM question_tasks WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise HttpError(404, "No such question run")
    return _task(row)


def find(root: Path, run_id: str) -> Task | None:
    try:
        return get(root, run_id)
    except HttpError:
        return None


def pending(root: Path) -> bool:
    """Whether the worker has something to do: a ready step, or a step a dead worker left."""
    with _root(root) as conn:
        return bool(conn.execute(
            "SELECT EXISTS(SELECT 1 FROM question_tasks WHERE state = 'running' OR "
            "(state = 'waiting' AND ready_at IS NOT NULL AND ready_at <= ?))",
            (db.now(),),
        ).fetchone()[0])


def stale(root: Path) -> list[Task]:
    """Mark steps left running as paused and return them. Only call this holding the
    execution lock, when no step can really be running."""
    with _root(root) as conn, db.immediate(conn):
        rows = conn.execute(f"SELECT {_COLUMNS} FROM question_tasks WHERE state = 'running'").fetchall()
        conn.execute(
            "UPDATE question_tasks SET state = 'paused', started = 1, control = NULL, updated_at = ? "
            "WHERE state = 'running'",
            (db.now(),),
        )
    return [_task(r) for r in rows]


def claim(root: Path, run_id: str | None = None) -> Task | None:
    """The oldest ready step (of run `run_id` when given), now running."""
    with _root(root) as conn, db.immediate(conn):
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM question_tasks WHERE state = 'waiting' AND ready_at IS NOT NULL "
            "AND ready_at <= ? AND (?2 IS NULL OR run_id = ?2) ORDER BY seq LIMIT 1",
            (db.now(), run_id),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE question_tasks SET state = 'running', updated_at = ? WHERE run_id = ?", (db.now(), row[0])
        )
    return _task(row)


def control(root: Path, run_id: str) -> str | None:
    """What a person asked of the step in hand: pause, cancel or nothing."""
    with _root(root) as conn:
        row = conn.execute("SELECT control FROM question_tasks WHERE run_id = ?", (run_id,)).fetchone()
    return row[0] if row else None


def advance(root: Path, run_id: str, step: str, *, ready_at: str | None = None, release: bool = False) -> str | None:
    """The step in hand moved on to `step`. With `release`, the worker lets the run go
    until `ready_at` (the plan review); otherwise it carries on with it. Returns a
    pending control the step should act on now."""
    with _root(root) as conn, db.immediate(conn):
        pending_control = conn.execute(
            "SELECT control FROM question_tasks WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE question_tasks SET step = ?, started = 0, ready_at = ?, state = ?, updated_at = ? "
            "WHERE run_id = ?",
            (step, ready_at if release else db.now(), "waiting" if release else "running", db.now(), run_id),
        )
    return pending_control


def stopped(root: Path, run_id: str, state: str) -> None:
    """The step in hand ended the run's work: `paused` (resumable) or `finished`."""
    with _root(root) as conn, db.immediate(conn):
        conn.execute(
            "UPDATE question_tasks SET state = ?, control = NULL, started = CASE WHEN ? = 'paused' THEN 1 "
            "ELSE started END, updated_at = ? WHERE run_id = ?",
            (state, state, db.now(), run_id),
        )


# --- what people do ------------------------------------------------------------------------


def start(root: Path, run_id: str) -> Task:
    """Start the search: end the plan review."""
    return _review(root, run_id, db.now())


def hold(root: Path, run_id: str) -> Task:
    """Keep the plan under review while it is edited; `start` or an edited plan ends it."""
    return _review(root, run_id, None)


def _review(root: Path, run_id: str, ready_at: str | None) -> Task:
    with _root(root) as conn, db.immediate(conn):
        changed = conn.execute(
            "UPDATE question_tasks SET ready_at = ?, updated_at = ? WHERE run_id = ? AND state = 'waiting' "
            "AND step = 'search' AND started = 0",
            (ready_at, db.now(), run_id),
        ).rowcount
    if not changed:
        get(root, run_id)
        raise HttpError(409, "The plan is not under review")
    return get(root, run_id)


def pause(root: Path, conn: sqlite3.Connection, run_id: str) -> Task:
    """Pause a running run: at once when no step is in hand, else at the step's next
    query or batch."""
    with _root(root) as rc, db.immediate(rc):
        task = _locked(rc, run_id)
        if task.state == "paused":
            return task
        if task.state == "running":
            rc.execute("UPDATE question_tasks SET control = 'pause', updated_at = ? WHERE run_id = ? "
                       "AND control IS NULL", (db.now(), run_id))
            now = False
        elif task.state == "waiting" and task.step != "plan":
            rc.execute("UPDATE question_tasks SET state = 'paused', updated_at = ? WHERE run_id = ?",
                       (db.now(), run_id))
            now = True
        else:
            raise HttpError(409, "Only a running question run can be paused")
    if now:
        runs.set_status(conn, run_id, "paused")
    return get(root, run_id)


def resume(root: Path, conn: sqlite3.Connection, run_id: str) -> Task:
    with _root(root) as rc, db.immediate(rc):
        task = _locked(rc, run_id)
        if task.state == "running" and task.control == "pause":
            # Not stopped yet: take the request back.
            rc.execute("UPDATE question_tasks SET control = NULL WHERE run_id = ?", (run_id,))
            return _task(rc.execute(f"SELECT {_COLUMNS} FROM question_tasks WHERE run_id = ?", (run_id,)).fetchone())
        if task.state != "paused":
            raise HttpError(409, "Only a paused question run can be resumed")
        rc.execute("UPDATE question_tasks SET state = 'waiting', ready_at = ?, updated_at = ? WHERE run_id = ?",
                   (db.now(), db.now(), run_id))
    runs.set_status(conn, run_id, "queued" if task.step == "plan" else "running")
    return get(root, run_id)


def cancel(root: Path, conn: sqlite3.Connection, run_id: str) -> Task | None:
    """Cancel: at once when no step is in hand, else at the step's next query or batch.
    A run the queue never took (one left behind before ask was atomic) is cancelled
    in its workspace alone; None then."""
    with _root(root) as rc, db.immediate(rc):
        row = rc.execute(f"SELECT {_COLUMNS} FROM question_tasks WHERE run_id = ?", (run_id,)).fetchone()
        task = _task(row) if row is not None else None
        # A task that ended without its run's `done` (a cancel or a step whose
        # workspace write failed) is cancelled again once it has settled.
        if task is not None and task.state == "finished" and not (
            task.settled and runs.ended(conn, run_id) is None
        ):
            raise HttpError(409, "This question run has already ended")
        if task is not None and task.state == "running":
            rc.execute("UPDATE question_tasks SET control = 'cancel', updated_at = ? WHERE run_id = ?",
                       (db.now(), run_id))
        elif task is not None and task.state != "finished":
            rc.execute("UPDATE question_tasks SET state = 'finished', control = NULL, updated_at = ? "
                       "WHERE run_id = ?", (db.now(), run_id))
    if task is None:
        if runs.get(conn, run_id)["status"] in runs.ENDED:
            raise HttpError(409, "This question run has already ended")
        cancelled(conn, run_id)
        return None
    if task.state != "running":
        cancelled(conn, run_id)
    return get(root, run_id)


def cancelled(conn: sqlite3.Connection, run_id: str) -> None:
    """The run's events for a cancel: its status, then `done`."""
    runs.set_status(conn, run_id, "cancelled")
    runs.emit(conn, run_id, [("done", {"status": "cancelled"})])


def _locked(conn: sqlite3.Connection, run_id: str) -> Task:
    """The run's task; a run the routes found but the queue never took (one left
    behind before ask was atomic) can only be cancelled: 409."""
    row = conn.execute(f"SELECT {_COLUMNS} FROM question_tasks WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise HttpError(409, "This question run is not in the queue; it can only be cancelled or deleted")
    return _task(row)
