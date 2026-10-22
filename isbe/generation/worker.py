"""The single queue worker of a root data directory.

One thread per root holds `.queue-worker.lock` for its whole life. Each task
runs under `.execution.lock`, the lock every CLI mutation takes, so a report
never overlaps a CLI run in any workspace; while the CLI holds it the queue
just waits. A task is claimed (queued → running) in one `BEGIN IMMEDIATE`,
the same transaction a cancel needs, so a task is either cancelled or run,
never both. Its result is written before the execution lock is released and
before any other task starts, retrying until the database accepts it; a
crash in between leaves it running, which the next `Manager.open` resolves.
"""

import logging
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from isbe import topics
from isbe.core import db, locks, paths, settings
from isbe.core.errors import describe
from isbe.core.llm import Llm
from isbe.topics import Flavor, Topic

from .fingerprint import unchanged
from .jobs import Report, trusted_owner
from .stages import Endpoints, Stages

log = logging.getLogger(__name__)

type ReadSettings = Callable[[Path], settings.Values]

SETTINGS_CHANGED = (
    "Service settings changed while this task was queued. Review them and submit again."
)
NO_PAPERS = (
    "No papers remain in this topic's current window. Review its sources, keywords and "
    "filters in Topics, then try again."
)
INTERRUPTED = "Generation was interrupted. Review the run records, then try again."


def failure_message(stage: str) -> str:
    match stage:
        case "collect":
            return "Collection failed. Review the topic's sources and service settings, then try again."
        case "triage":
            return "Screening failed. Review model settings and the run records, then try again."
        case "enrich":
            return "Paper enrichment failed. Review service settings and the run records, then try again."
        case _:
            return (
                "The report could not be completed. Review model settings and the run records, "
                "then try again."
            )


class SettingsChanged(Exception):
    def __init__(self) -> None:
        super().__init__("service settings changed")


class NoPapers(Exception):
    def __init__(self) -> None:
        super().__init__("no kept papers in the current window")


class Signal:
    """Stop and wake-up flags shared by the manager and its worker.

    `wait` returns after a second at most, so the worker also notices tasks
    another process queued and the execution lock being released.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._stopped = False
        self._woken = False

    def stopped(self) -> bool:
        with self._cond:
            return self._stopped

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify_all()

    def wake(self) -> None:
        with self._cond:
            self._woken = True
            self._cond.notify_all()

    def wait(self, timeout: float = 1.0) -> None:
        with self._cond:
            if not self._stopped and not self._woken:
                self._cond.wait(timeout)
            self._woken = False


def loop(root: Path, lock: locks.Lock, read: ReadSettings, stages: Stages, signal: Signal) -> None:
    """The worker thread: run queued tasks until stopped, then drop the worker lock."""
    with lock:
        while not signal.stopped():
            try:
                worked = work_one(root, read, stages, signal)
            except Exception as error:
                log.error(
                    "report queue could not update its persistent state; retrying: %s",
                    describe(error),
                )
                worked = False
            if not worked:
                signal.wait()


@dataclass(frozen=True)
class _Claimed:
    id: str
    owner: str
    topic: str
    snapshot: str
    fingerprint: str


def work_one(root: Path, read: ReadSettings, stages: Stages, signal: Signal) -> bool:
    """Run the oldest queued task; false when there was nothing to do or the
    execution lock is busy."""
    with closing(db.connect(root / "isbe.db")) as conn:
        pending = conn.execute("SELECT EXISTS(SELECT 1 FROM tasks WHERE state = 'queued')").fetchone()
        if not pending[0]:
            return False
        try:
            execution = locks.execution_lock(root)
        except locks.LockBusy:
            return False
        with execution:
            if signal.stopped():
                return False
            with db.immediate(conn):
                row = conn.execute(
                    "SELECT id, owner, topic, topic_snapshot, settings_fingerprint FROM tasks "
                    "WHERE state = 'queued' ORDER BY seq LIMIT 1"
                ).fetchone()
                if row is None:
                    return False
                task = _Claimed(*row)
                conn.execute(
                    "UPDATE tasks SET state = 'running', stage = 'collect', started_at = ? "
                    "WHERE id = ? AND state = 'queued'",
                    (db.now(), task.id),
                )
            state, error, period = _outcome(conn, root, read, stages, task)
            return _finish(conn, signal, task.id, state, error, period)


def _outcome(
    conn: sqlite3.Connection, root: Path, read: ReadSettings, stages: Stages, task: _Claimed
) -> tuple[str, str | None, str | None]:
    stage = "collect"

    def advance(next: str) -> None:
        nonlocal stage
        stage = next
        conn.execute(
            "UPDATE tasks SET stage = ? WHERE id = ? AND state = 'running'", (next, task.id)
        )

    try:
        report = _execute(conn, root, read, stages, task, advance)
    except SettingsChanged:
        return "failed", SETTINGS_CHANGED, None
    except NoPapers:
        return "failed", NO_PAPERS, None
    except Exception as error:
        # The stage's own error is in its `runs` row; the task shows a safe summary.
        log.warning("task %s failed at %s: %s", task.id, stage, describe(error))
        return "failed", failure_message(stage), None
    except BaseException:
        # Record the task once, then let the thread end.
        try:
            conn.execute(_FINISH, ("failed", INTERRUPTED, None, db.now(), task.id))
        except sqlite3.Error:
            pass  # left running; the next Manager.open marks it interrupted
        raise
    return "succeeded", None, report.period


def _execute(
    conn: sqlite3.Connection,
    root: Path,
    read: ReadSettings,
    stages: Stages,
    task: _Claimed,
    advance: Callable[[str], None],
) -> Report:
    data = paths.workspace(root, task.owner)
    for name in ("reports", "figures", "memory"):
        paths.check_tree(data / name)
    values = read(root)
    if not unchanged(task.fingerprint, values):
        raise SettingsChanged()
    with settings.task_scope(values):
        topic = topics.parse(task.snapshot, task.topic)
        allowed = topic.flavor() is Flavor.PAPERS and topics.shared_topic_allowed(
            trusted_owner(conn, task.owner), topic, task.snapshot
        )
        if not allowed:
            raise ValueError("invalid queued topic")
        llm = Llm.from_env()
        with db.task(task.id):
            return run(data, topic, llm, stages, advance)


def run(data: Path, topic: Topic, llm: Llm, stages: Stages, advance: Callable[[str], None]) -> Report:
    """Collect, triage, enrich and digest `topic` in its workspace `data`."""
    with closing(db.connect(data / "isbe.db")) as conn:
        endpoints = Endpoints.from_settings()
        db.job(conn, "collect", topic.id, lambda _, stats: stages.collect(conn, topic, endpoints, stats))
        advance("triage")
        # CLI digest keeps unjudged items and falls back to abstracts on these failures.
        try:
            db.job(conn, "triage", topic.id, lambda run, stats: stages.triage(conn, llm, run, topic, stats))
        except Exception:
            pass
        advance("enrich")
        try:
            db.job(
                conn,
                "enrich",
                topic.id,
                lambda run, stats: stages.enrich(conn, llm, run, topic, data, endpoints.essaies, stats),
            )
        except Exception:
            pass
        advance("digest")

        def digest(run: str, stats: dict[str, Any]) -> None:
            since = topic.since(datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
            has_papers = conn.execute(
                "SELECT EXISTS(SELECT 1 FROM triage t JOIN items i ON i.id = t.item_id "
                "WHERE t.topic_id = ? AND t.verdict = 'kept' AND i.kind = 'paper' "
                "AND i.published_at >= ?)",
                (topic.id, since),
            ).fetchone()[0]
            if not has_papers:
                raise NoPapers()
            stages.digest(conn, llm, run, topic, data, stats)

        stats = db.job(conn, "digest", topic.id, digest)
        path = stats.get("report")
        if not isinstance(path, str):
            raise ValueError("missing report result")
        return generated_report(conn, data, topic.id, path)


def generated_report(conn: sqlite3.Connection, data: Path, topic: str, path: str) -> Report:
    """The digest's report, only if its row exists and its file is inside `data` and not empty."""
    row = conn.execute(
        "SELECT period FROM reports WHERE topic_id = ? AND path = ?", (topic, path)
    ).fetchone()
    if row is None:
        raise LookupError("Query returned no rows")
    # Normal path components only: no root, no `..`, no leading `.`.
    parts = path.split("/")
    if path.startswith("/") or parts[0] == "." or ".." in parts:
        raise ValueError("invalid report path")
    if not (data / PurePosixPath(path)).read_text(encoding="utf-8").strip():
        raise ValueError("empty report")
    return Report(topic, row[0])


_FINISH = (
    "UPDATE tasks SET state = ?, error = ?, result_period = ?, finished_at = ? "
    "WHERE id = ? AND state = 'running'"
)


def _finish(
    conn: sqlite3.Connection,
    signal: Signal,
    id: str,
    state: str,
    error: str | None,
    period: str | None,
) -> bool:
    """Record the outcome; keep the execution lock (and the queue) until it is durable."""
    finished = db.now()
    while True:
        try:
            conn.execute(_FINISH, (state, error, period, finished, id))
            return True
        except sqlite3.Error:
            if signal.stopped():
                return False
            signal.wait()
