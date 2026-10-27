"""SQLite store. Migrations are the numbered files in `migrations/`, applied in
order; the applied count lives in `PRAGMA user_version`. Databases created by
the earlier Rust build use the same files and open unchanged.
"""

import contextvars
import hashlib
import itertools
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from isbe.core import assets, jsonfmt, paperrefs
from isbe.core.errors import describe

log = logging.getLogger(__name__)

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


def connect(path: Path | str, say: Callable[[str], None] | None = None) -> sqlite3.Connection:
    """Open (creating if needed) and bring the schema up to date.

    Uses a WAL journal (switched only when not already WAL), `foreign_keys=ON`,
    a 5 s busy timeout, migrations applied in one `BEGIN IMMEDIATE` transaction
    with `user_version` bumped inside it, and a bounded retry on initialization
    contention / `SQLITE_PROTOCOL`.
    Connections use `autocommit=True` and `sqlite3.Row` rows; callers open
    explicit transactions with `immediate()`. A connection may move between
    threads (a web request may hop worker threads) but must not be shared by
    two at once. `say` hears about a migration that takes time (`upgrade`).
    """
    deadline = time.monotonic() + _OPEN_SECONDS
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
            _migrate(conn, say)
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
                deadline = time.monotonic() + _OPEN_SECONDS
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


# How long an ordinary open waits for the database.
_OPEN_SECONDS = 5.0
# A long migration says it is still going this often.
_PROGRESS_SECONDS = 30.0
# SQLite instructions between two looks at the clock.
_PROGRESS_STEPS = 10_000
# The migration after which the file is compacted (016: each paper stored once).
_COMPACTS_AFTER = 16
# How long an opener waits for another connection's migration: a large question
# database takes 016 tens of seconds (about 30 s per GB).
_MIGRATION_WAIT_MS = 300_000
# Compact when this share of the file, and at least this many pages, is free.
_FREE_SHARE = 0.25
_FREE_PAGES = 1024


@cache
def _migrations() -> tuple[str, ...]:
    files = sorted(assets.path("migrations").glob("*.sql"))
    for i, file in enumerate(files, 1):
        if not file.name.startswith(f"{i:03}_"):
            raise RuntimeError(f"migrations must be numbered consecutively: {file.name}")
    return tuple(file.read_text(encoding="utf-8") for file in files)


def _version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _migrate(conn: sqlite3.Connection, say: Callable[[str], None] | None = None) -> None:
    migrations = _migrations()
    # Ordinary readers do not need a write lock; stale versions are rechecked below.
    if _version(conn) == len(migrations):
        return
    # Functions a migration may call (016 moves paper records into one table).
    paperrefs.register(conn)
    # Another connection may be migrating; wait for it rather than fail, saying so.
    if say is not None and _held(conn):
        say(f"waiting for {_name(conn)}: another process is writing to it…")
    conn.execute(f"PRAGMA busy_timeout={_MIGRATION_WAIT_MS}")
    with immediate(conn):
        applied = _version(conn)
        if applied > len(migrations):
            raise SchemaTooNew(
                f"database schema v{applied} is newer than this binary (v{len(migrations)})"
            )
        if say is not None and 0 < applied < len(migrations):
            say(f"migrating {_name(conn)} from schema v{applied} to v{len(migrations)}…")
            _progress(conn, say)
        try:
            for i in range(applied, len(migrations)):
                try:
                    # With autocommit=True executescript runs inside our transaction.
                    conn.executescript(migrations[i])
                except sqlite3.Error as error:
                    error.add_note(f"migration {i + 1:03}")
                    raise
                conn.execute(f"PRAGMA user_version = {i + 1}")
        finally:
            conn.set_progress_handler(None, 0)
    if 0 < applied < _COMPACTS_AFTER <= len(migrations):
        compact(conn)


def _progress(conn: sqlite3.Connection, say: Callable[[str], None]) -> None:
    """Say every `_PROGRESS_SECONDS` that the migration is still running."""
    started = last = time.monotonic()
    # Named now: the handler must not use the connection it interrupts.
    name = _name(conn)

    def tick() -> int:
        nonlocal last
        now = time.monotonic()
        if now - last >= _PROGRESS_SECONDS:
            last = now
            try:
                say(f"still migrating {name} ({now - started:.0f} s)")
            except Exception:  # a broken output must not abort the migration
                pass
        return 0

    conn.set_progress_handler(tick, _PROGRESS_STEPS)


def _held(conn: sqlite3.Connection) -> bool:
    """Whether another connection holds the write lock right now."""
    conn.execute("PRAGMA busy_timeout=0")
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as error:
        if _contention(error):
            return True
        raise
    conn.execute("ROLLBACK")
    return False


def _name(conn: sqlite3.Connection) -> str:
    return conn.execute("PRAGMA database_list").fetchone()[2] or "the database"


def compact(conn: sqlite3.Connection) -> bool:
    """Give the file's free pages back (`VACUUM`) when a large share of it is free,
    as after 016; whether it is compact now. Another writer or a full disk makes it
    fail: that is logged and the next `upgrade` (each `serve` start) tries again."""
    free, pages = (conn.execute(f"PRAGMA {name}").fetchone()[0] for name in ("freelist_count", "page_count"))
    if free < _FREE_PAGES or free < pages * _FREE_SHARE:
        return True
    try:
        conn.execute("VACUUM")
    except sqlite3.OperationalError as error:
        log.warning("could not compact the database (%d of %d pages free): %s", free, pages, error)
        return False
    return True


def upgrade(path: Path, say: Callable[[str], None] = print) -> None:
    """Bring an existing database up to date and compact it, saying what takes
    time: `serve` does this for every data directory before it listens, so no
    request waits for a migration. A missing file is `sqlite3.OperationalError`."""
    probe = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    try:
        applied = _version(probe)
    finally:
        probe.close()
    started = time.monotonic()
    said: list[str] = []

    def heard(line: str) -> None:
        said.append(line)
        say(line)

    conn = connect(path, heard)
    try:
        if applied == _version(conn) and not compact(conn):
            say(f"{path} could not be compacted; see the warning above")
    finally:
        conn.close()
    if said:  # this open migrated it, not another process it waited for
        say(f"migrated {path} in {time.monotonic() - started:.1f} s")


@contextmanager
def immediate(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """`BEGIN IMMEDIATE` … `COMMIT`, rolling back on any exception. Inside a
    transaction already open it joins that one: the outer block commits or rolls
    back everything, so writes made through several helpers land together."""
    if conn.in_transaction:
        yield conn
        return
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


@contextmanager
def snapshot(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A read transaction: every read inside sees one state of the database, so
    rows read first and the rows they refer to, read next, belong together even
    while another connection commits. Joins a transaction already open. Read
    only: a write inside fails (it would be rolled back, or meet a newer commit)
    instead of being lost. While it lasts, checkpoints stop at its state; keep
    it to one request."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("PRAGMA query_only=ON")
    try:
        conn.execute("BEGIN")
        try:
            yield conn
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
    finally:
        conn.execute("PRAGMA query_only=OFF")


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
