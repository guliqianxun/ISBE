"""SQLite store. Migrations are the numbered files in `migrations/`, applied in
order; the applied count lives in `PRAGMA user_version`. Databases created by
the earlier Rust build use the same files and open unchanged.
"""

import contextvars
import hashlib
import itertools
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from isbe.core import assets, jsonfmt
from isbe.core.errors import describe

_TASK: contextvars.ContextVar[str | None] = contextvars.ContextVar("isbe_task", default=None)


def now() -> str:
    """UTC now, RFC 3339 with milliseconds: the timestamp format of every table."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256(text: str) -> str:
    """Lowercase hex SHA-256: the content key of prompts and retrieval contracts."""
    return hashlib.sha256(text.encode()).hexdigest()


@contextmanager
def task(task_id: str) -> Iterator[None]:
    """Associate pipeline ledger rows written inside the block with a queue task."""
    token = _TASK.set(task_id)
    try:
        yield
    finally:
        _TASK.reset(token)


def current_task() -> str | None:
    return _TASK.get()


def connect(path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) and bring the schema up to date.

    Uses a WAL journal (switched only when not already WAL), `foreign_keys=ON`,
    a 5 s busy timeout, migrations applied in one `BEGIN IMMEDIATE` transaction
    with `user_version` bumped inside it, and a bounded retry on initialization
    contention / `SQLITE_PROTOCOL`.
    Connections use `autocommit=True` and `sqlite3.Row` rows; callers open
    explicit transactions with `immediate()`. A connection may move between
    threads (a web request may hop worker threads) but must not be shared by
    two at once.
    """
    deadline = time.monotonic() + 5
    retried_protocol = False
    while True:
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(
                path,
                timeout=max(0.0, deadline - time.monotonic()),
                autocommit=True,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            # Changing journal mode takes locks; established WAL readers need no transition.
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if str(mode).lower() != "wal":
                conn.execute("PRAGMA journal_mode=WAL").fetchone()
            conn.execute("PRAGMA foreign_keys=ON")
            _migrate(conn)
            conn.execute("PRAGMA busy_timeout=5000")
            return conn
        except BaseException as error:
            if conn is not None:
                conn.close()
            if not isinstance(error, sqlite3.Error):
                raise
            code = _code(error)
            if code == _PROTOCOL:
                if retried_protocol:
                    raise
                # SQLite's own protocol wait can outlast the busy budget; reopen once.
                retried_protocol = True
                deadline = time.monotonic() + 5
            elif not _contention(error) or time.monotonic() >= deadline:
                raise
            # The failed attempt dropped its connection and transaction before this bounded wait.
            time.sleep(min(0.025, max(0.0, deadline - time.monotonic())))


_BUSY, _LOCKED, _PROTOCOL = 5, 6, 15


def _code(error: BaseException) -> int | None:
    """The primary SQLite result code (extended codes keep it in the low byte)."""
    code = getattr(error, "sqlite_errorcode", None)
    return None if code is None else code & 0xFF


def _contention(error: BaseException) -> bool:
    return _code(error) in (_BUSY, _LOCKED, _PROTOCOL)


class SchemaTooNew(RuntimeError):
    pass


@cache
def _migrations() -> tuple[str, ...]:
    files = sorted(assets.path("migrations").glob("*.sql"))
    for i, file in enumerate(files, 1):
        if not file.name.startswith(f"{i:03}_"):
            raise RuntimeError(f"migrations must be numbered consecutively: {file.name}")
    return tuple(file.read_text(encoding="utf-8") for file in files)


def _version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _migrate(conn: sqlite3.Connection) -> None:
    migrations = _migrations()
    # Ordinary readers do not need a write lock; stale versions are rechecked below.
    if _version(conn) == len(migrations):
        return
    with immediate(conn):
        applied = _version(conn)
        if applied > len(migrations):
            raise SchemaTooNew(
                f"database schema v{applied} is newer than this binary (v{len(migrations)})"
            )
        for i in range(applied, len(migrations)):
            try:
                # With autocommit=True executescript runs inside our transaction.
                conn.executescript(migrations[i])
            except sqlite3.Error as error:
                error.add_note(f"migration {i + 1:03}")
                raise
            conn.execute(f"PRAGMA user_version = {i + 1}")


@contextmanager
def immediate(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """`BEGIN IMMEDIATE` … `COMMIT`, rolling back on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        raise


# Keeps two runs started in the same millisecond apart.
_SEQ = itertools.count()
_SEQ_LOCK = threading.Lock()


def job(
    conn: sqlite3.Connection,
    name: str,
    topic: str | None,
    run: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    """Run `run(run_id, stats)` as one row of the `runs` ledger.

    The row is written as running with `current_task()`, then finished as
    ok/failed with the stats recorded either way; the exception is re-raised
    after the row is updated, and on success the stats are returned. The run id
    is `<started>/<job>/<topic or ->/<seq>`; stats are stored as
    compact JSON with sorted keys (`jsonfmt`) and the error as the full cause chain.
    """
    started = now()
    with _SEQ_LOCK:
        seq = next(_SEQ)
    run_id = f"{started}/{name}/{'-' if topic is None else topic}/{seq}"
    conn.execute(
        "INSERT INTO runs (id, job, topic_id, status, started_at, task_id) "
        "VALUES (?, ?, ?, 'running', ?, ?)",
        (run_id, name, topic, started, current_task()),
    )
    stats: dict[str, Any] = {}
    finish = "UPDATE runs SET status = ?, finished_at = ?, stats = ?, error = ? WHERE id = ?"
    try:
        run(run_id, stats)
    except Exception as error:
        conn.execute(finish, ("failed", now(), jsonfmt.dumps(stats), describe(error), run_id))
        raise
    conn.execute(finish, ("ok", now(), jsonfmt.dumps(stats), None, run_id))
    return stats
