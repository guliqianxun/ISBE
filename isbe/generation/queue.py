"""The durable, owner-scoped FIFO of report tasks.

The queue lives in the root database (`tasks`, `task_requests`); each task's
pipeline rows live in its owner's workspace and link back by `runs.task_id`.
Every mutation is one `BEGIN IMMEDIATE` transaction on its own connection,
so submits from several web threads or processes dedupe exactly: one active
task per owner and topic, an idempotent request id per owner, at most five
pending tasks per owner.
"""

import builtins
import logging
import secrets
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from types import TracebackType

from isbe import topics
from isbe.core import db, jsonfmt, locks, paths, settings
from isbe.core.errors import describe
from isbe.core.llm import Llm
from isbe.question import flow
from isbe.topics import Flavor

from . import worker
from .fingerprint import fingerprint
from .jobs import (
    SELECT,
    Job,
    StartError,
    from_row,
    lookup,
    require,
    trusted_owner,
    unavailable,
)
from .stages import Stages

log = logging.getLogger(__name__)

INTERRUPTED = "Generation was interrupted by a service restart. Submit again to retry."
# A broken workspace must not keep the service from starting.
WORKSPACE_BROKEN = (
    "Generation was interrupted by a service restart, and the account workspace could not be "
    "opened. Ask an administrator to review it, then submit again."
)
PENDING_LIMIT = 5
_REQUEST = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


class Manager:
    """The queue of one root data directory and the handle of its worker thread.

    `Manager.open(root)` recovers tasks a dead worker left running and starts
    the worker; `close()` stops it. The queue methods open their own database
    connection per call and are safe to call from any thread.
    """

    def __init__(self, root: Path, signal: worker.Signal, thread: threading.Thread, read: worker.ReadSettings) -> None:
        self.root = root
        self._signal = signal
        self._thread = thread
        self._read = read

    @classmethod
    def open(
        cls,
        root: Path,
        *,
        stages: Stages | None = None,
        read_settings: worker.ReadSettings = settings.for_task,
        questions: flow.Services | None = None,
    ) -> Manager:
        """Take the worker lock of `root`, recover interrupted tasks and start the worker.

        Raises `locks.LockBusy` ("Another queue worker is already running") when
        another process (or another open Manager) serves `root`, and the
        `paths`/`sqlite3` errors of an unusable root. A broken *owner*
        workspace does not fail it. `stages` replaces the pipeline and
        `read_settings` the settings snapshot (tests); both apply to the
        worker and, for the settings, to `submit`; `questions` what question
        runs use (tests).
        """
        root = paths.workspace(root, "local")
        lock = locks.worker_lock(root)
        try:
            with closing(db.connect(root / "isbe.db")) as conn:
                _recover(conn, root)
            signal = worker.Signal()
            thread = threading.Thread(
                target=worker.loop,
                args=(root, lock, read_settings, stages or Stages(), signal, questions),
                name="report-queue",
                # A task can take minutes; a process exiting without close() leaves
                # it running in the database, and the next open marks it interrupted.
                daemon=True,
            )
            thread.start()
        except BaseException:
            lock.release()
            raise
        return cls(root, signal, thread, read_settings)

    def close(self, timeout: float | None = None) -> None:
        """Stop the worker and wait for it.

        A running task is not interrupted: without `timeout` this waits for
        it; with one, the thread may still be finishing (holding the worker
        lock) when this returns, see `worker_alive`. If the process exits
        first, the task stays running and the next `open` marks it
        interrupted. Queue methods keep working without a worker.
        """
        self._signal.stop()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout)

    @property
    def worker_alive(self) -> bool:
        return self._thread.is_alive()

    def __enter__(self) -> Manager:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    def wake(self) -> None:
        """Make the worker look for work now instead of within a second."""
        self._signal.wake()

    def list(self, owner: str | None) -> builtins.list[Job]:
        """Tasks of `owner` (every owner for None), newest first."""
        with closing(db.connect(self.root / "isbe.db")) as conn:
            rows = conn.execute(f"{SELECT} WHERE (?1 IS NULL OR owner = ?1) ORDER BY seq DESC", (owner,))
            return [from_row(row) for row in rows]

    def get(self, id: str) -> Job | None:
        with closing(db.connect(self.root / "isbe.db")) as conn:
            return lookup(conn, id)

    def submit(self, owner: str, topic: str, request_id: str) -> Job:
        """Queue a report of `owner`'s `topic`, or return the task this request
        (or an active task of the same topic) already has. Raises `StartError`.

        `owner` comes from authentication (`local` or an account id); the
        topic is frozen now, as JSON, and settings are fingerprinted so a
        changed model or source fails the task instead of surprising it.
        """
        if not paths.owner_valid(owner):
            raise StartError(400, "invalid_owner", "Choose a valid account.")
        try:
            topics.check_id(topic)
        except topics.TopicError:
            raise StartError(400, "invalid_topic", "Choose a valid topic.") from None
        if not request_id or len(request_id) > 128 or not set(request_id) <= _REQUEST:
            raise StartError(400, "invalid_request", "A valid request identifier is required.")
        try:
            with closing(db.connect(self.root / "isbe.db")) as conn:
                with db.immediate(conn):
                    job, created = self._submit(conn, owner, topic, request_id)
        except StartError:
            raise
        except Exception as error:
            raise _unavailable(error) from None
        if created:
            self._signal.wake()
        return job

    def _submit(self, conn: sqlite3.Connection, owner: str, topic: str, request_id: str) -> tuple[Job, bool]:
        row = conn.execute(
            "SELECT task_id FROM task_requests WHERE owner = ? AND request_id = ?", (owner, request_id)
        ).fetchone()
        if row is not None:
            job = require(conn, row[0])
            if job.topic != topic:
                raise StartError(
                    409, "request_conflict", "This request identifier already belongs to another topic."
                )
            return job, False
        row = conn.execute(
            "SELECT id FROM tasks WHERE owner = ? AND topic = ? AND state IN ('queued','running')",
            (owner, topic),
        ).fetchone()
        if row is not None:
            conn.execute(
                "INSERT INTO task_requests (owner, request_id, task_id) VALUES (?, ?, ?)",
                (owner, request_id, row[0]),
            )
            return require(conn, row[0]), False
        pending = conn.execute(
            "SELECT count(*) FROM tasks WHERE owner = ? AND state IN ('queued','running')", (owner,)
        ).fetchone()[0]
        if pending >= PENDING_LIMIT:
            raise StartError(
                429,
                "queue_limit",
                "This account already has five pending tasks. Wait for one to finish or cancel a queued task.",
            )
        try:
            data = paths.workspace(self.root, owner)
        except Exception:
            raise StartError(400, "workspace", "The account workspace is unavailable.") from None
        try:
            values = self._read(self.root)
        except Exception:
            raise StartError(
                400, "configuration", "Service settings could not be loaded. Review and save Settings."
            ) from None
        trusted = trusted_owner(conn, owner)
        with settings.task_scope(values):
            snapshot = _snapshot(data, topic, trusted)
        id = secrets.token_hex(16)
        conn.execute(
            "INSERT INTO tasks (id, owner, topic, request_id, state, stage, created_at, topic_snapshot, "
            "settings_fingerprint) VALUES (?, ?, ?, ?, 'queued', 'queued', ?, ?, ?)",
            (id, owner, topic, request_id, db.now(), snapshot, fingerprint(values)),
        )
        conn.execute(
            "INSERT INTO task_requests (owner, request_id, task_id) VALUES (?, ?, ?)",
            (owner, request_id, id),
        )
        return require(conn, id), True

    def cancel(self, id: str, owner: str | None) -> Job:
        """Cancel a queued task of `owner` (any owner for None). Raises `StartError`:
        404 not_found when it does not exist or belongs to someone else, 409
        not_queued once the worker has claimed it."""
        try:
            with closing(db.connect(self.root / "isbe.db")) as conn:
                with db.immediate(conn):
                    job = lookup(conn, id)
                    if job is None or (owner is not None and job.owner != owner):
                        raise StartError(404, "not_found", "Task not found.")
                    if job.state != "queued":
                        raise StartError(409, "not_queued", "Only queued tasks can be cancelled.")
                    conn.execute(
                        "UPDATE tasks SET state = 'cancelled', finished_at = ? WHERE id = ? AND state = 'queued'",
                        (db.now(), id),
                    )
                    return require(conn, id)
        except StartError:
            raise
        except Exception as error:
            raise _unavailable(error) from None


def _snapshot(data: Path, topic: str, trusted: bool) -> str:
    """The topic as the task will run it: the raw document as JSON, checked
    like the runtime topic would be (inside the task's settings scope)."""
    try:
        doc = topics.get(data, topic)
    except Exception as error:
        raise StartError(
            404 if topics.status(error) == 404 else 400,
            "invalid_topic",
            "The topic is missing or invalid. Review and save it in Topics.",
        ) from None
    config = doc.get("config")
    if not isinstance(config, dict):
        raise StartError(400, "invalid_topic", "The topic configuration is invalid.")
    raw = jsonfmt.dumps(config)
    try:
        parsed = topics.parse(raw, topic)
    except Exception:
        raise StartError(400, "invalid_topic", "The topic configuration is invalid.") from None
    if parsed.flavor() is not Flavor.PAPERS:
        raise StartError(400, "unsupported_topic", "Reports currently require an arXiv paper topic.")
    if not topics.shared_topic_allowed(trusted, parsed, raw):
        raise StartError(
            400, "unsupported_topic", "Shared topics must use arXiv sources and stay within service limits."
        )
    try:
        Llm.from_env()
    except Exception:
        raise StartError(
            400, "configuration", "Model configuration is incomplete or invalid. Review and save Settings."
        ) from None
    return raw


def _unavailable(error: BaseException) -> StartError:
    log.error("task queue: %s", describe(error))
    return unavailable()


def _recover(conn: sqlite3.Connection, root: Path) -> None:
    """Resolve tasks a dead worker left running: close their open runs and mark
    them interrupted. A task whose workspace cannot be opened (a link in it, a
    corrupt database) is marked failed instead, and the queue still starts."""
    broken: builtins.list[str] = []
    for id, owner in conn.execute("SELECT id, owner FROM tasks WHERE state = 'running'").fetchall():
        try:
            data = paths.workspace(root, owner)
            with closing(db.connect(data / "isbe.db")) as ledger:
                ledger.execute(
                    "UPDATE runs SET status = 'failed', finished_at = ?, "
                    "error = 'Task interrupted by service restart' WHERE task_id = ? AND status = 'running'",
                    (db.now(), id),
                )
        except Exception as error:
            log.error(
                "task %s: the workspace of %s could not be opened to close its runs; "
                "marking the task failed: %s",
                id,
                owner,
                describe(error),
            )
            broken.append(id)
    now = db.now()
    with db.immediate(conn):
        for id in broken:
            conn.execute(
                "UPDATE tasks SET state = 'failed', error = ?, finished_at = ? WHERE id = ? AND state = 'running'",
                (WORKSPACE_BROKEN, now, id),
            )
        conn.execute(
            "UPDATE tasks SET state = 'interrupted', error = ?, finished_at = ? WHERE state = 'running'",
            (INTERRUPTED, now),
        )
