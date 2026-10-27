import queue
import re
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

from isbe.core import db, paperrefs


def raw(*migrations: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", autocommit=True)
    conn.execute("PRAGMA foreign_keys=ON")
    for sql in migrations:
        conn.executescript(sql)
    return conn


def run_threads(count: int, target, *args) -> None:  # type: ignore[no-untyped-def]
    errors: list[BaseException] = []

    def guarded() -> None:
        try:
            target(*args)
        except BaseException as error:  # surfaced below
            errors.append(error)

    threads = [threading.Thread(target=guarded) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]


def test_concurrent_first_open_migrates_once(tmp_path: Path) -> None:
    path = tmp_path / "isbe.db"
    barrier = threading.Barrier(8)

    def first_open() -> None:
        barrier.wait()
        conn = db.connect(path)
        db.job(conn, "collect", "test", lambda run, stats: None)
        conn.close()

    run_threads(8, first_open)
    conn = db.connect(path)
    assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 8
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db._migrations())
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_initialization_retries_only_sqlite_lock_contention() -> None:
    def failure(code: int) -> sqlite3.Error:
        error = sqlite3.OperationalError("fixture")
        error.sqlite_errorcode = code  # type: ignore[attr-defined]
        return error

    for code in [5, 6, 15, 517]:  # BUSY, LOCKED, PROTOCOL, BUSY_SNAPSHOT
        assert db._contention(failure(code))
    for code in [11, 10, 13, 19]:  # CORRUPT, IOERR, FULL, CONSTRAINT
        assert not db._contention(failure(code))
    assert not db._contention(RuntimeError("invalid schema"))


def test_established_wal_opens_under_write_load_without_reinitializing(tmp_path: Path) -> None:
    path = tmp_path / "isbe.db"
    writer = db.connect(path)
    writer.execute("BEGIN IMMEDIATE")
    opened: queue.Queue[object] = queue.Queue()

    def open_while_held() -> None:
        try:
            db.connect(path).close()
            opened.put("ok")
        except BaseException as error:
            opened.put(error)

    threading.Thread(target=open_while_held).start()
    assert opened.get(timeout=3) == "ok", "opening an established WAL database must not wait"
    writer.execute("COMMIT")
    barrier = threading.Barrier(9)

    def write() -> None:
        conn = db.connect(path)
        barrier.wait()
        for _ in range(200):
            db.job(conn, "collect", None, lambda run, stats: None)

    def read() -> None:
        barrier.wait()
        for _ in range(50):
            conn = db.connect(path)
            conn.execute("SELECT count(*) FROM runs").fetchone()
            conn.close()

    errors: list[BaseException] = []

    def guarded(target) -> None:  # type: ignore[no-untyped-def]
        try:
            target()
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=guarded, args=(write,))]
    threads += [threading.Thread(target=guarded, args=(read,)) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    assert writer.execute("SELECT count(*) FROM runs").fetchone()[0] == 200


def test_task_context_restores_after_nested_calls_and_errors() -> None:
    conn = db.connect(":memory:")
    with db.task("outer"):
        with pytest.raises(RuntimeError):
            with db.task("inner"):
                raise RuntimeError("test unwind")
        db.job(conn, "collect", None, lambda run, stats: None)
    db.job(conn, "digest", None, lambda run, stats: None)
    assert conn.execute("SELECT task_id FROM runs WHERE job = 'collect'").fetchone()[0] == "outer"
    assert conn.execute("SELECT task_id FROM runs WHERE job = 'digest'").fetchone()[0] is None


def test_migrates_once_and_enforces_constraints() -> None:
    conn = raw()
    db._migrate(conn)
    db._migrate(conn)  # second run is a no-op
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db._migrations()) == 19
    conn.execute(
        "INSERT INTO items (id, kind, source, title, url, lang, published_at) VALUES "
        "('arxiv:2609.34836', 'paper', 'essaies', 'MW-Nowcast', "
        "'https://arxiv.org/abs/2609.34836', 'en', '2026-09-30')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO items (id, kind, source, title, url, lang, published_at) "
            "VALUES ('x', 'tweet', 's', 't', 'u', 'en', '2026-01-01')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO topic_items (topic_id, item_id, first_seen) "
            "VALUES ('t', 'missing', '2026-01-01')"
        )


@pytest.mark.parametrize("applied", [5, 9])
def test_later_migrations_preserve_existing_reports(applied: int) -> None:
    conn = raw(*db._migrations()[:applied])
    conn.execute(f"PRAGMA user_version = {applied}")
    conn.execute(
        "INSERT INTO reports VALUES ('old','t','2026-W41','weekly','reports/t/old.md','{}','2026-10-16')"
    )
    db._migrate(conn)
    assert conn.execute("SELECT path FROM reports WHERE id='old'").fetchone()[0] == "reports/t/old.md"
    assert conn.execute("SELECT count(*) FROM article_text").fetchone()[0] == 0
    conn.execute(
        "INSERT INTO reports VALUES ('new','t','2026-10-16-v1','research','reports/t/new.md','{}','2026-10-16')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO reports VALUES ('bad','t','bad','unknown','reports/t/bad.md','{}','2026-10-16')"
        )
    db._migrate(conn)


def test_job_records_stats_even_on_failure() -> None:
    conn = db.connect(":memory:")
    assert db.sha256("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

    def fail(run: str, stats: dict[str, object]) -> None:
        stats["found"] = 3
        raise RuntimeError("feed down")

    with pytest.raises(RuntimeError, match="feed down"):
        db.job(conn, "collect", "t", fail)
    row = conn.execute("SELECT status, stats, error FROM runs").fetchone()
    assert tuple(row) == ("failed", '{"found":3}', "feed down")


def test_job_rows_match_the_ledger_format() -> None:
    conn = db.connect(":memory:")

    def fill(run: str, stats: dict[str, object]) -> None:
        stats.update({"zeta": 1, "alpha": [1.0, "主题"], "kept": True})

    assert db.job(conn, "triage", None, fill) == {"zeta": 1, "alpha": [1.0, "主题"], "kept": True}

    def fail(run: str, stats: dict[str, object]) -> None:
        try:
            raise ConnectionError("connection refused")
        except ConnectionError as cause:
            raise RuntimeError("collect rss feed") from cause

    with pytest.raises(RuntimeError):
        db.job(conn, "collect", "", fail)
    ok, failed = conn.execute("SELECT * FROM runs ORDER BY rowid").fetchall()
    stamp = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z"
    assert re.fullmatch(rf"{stamp}/triage/-/\d+", ok["id"])
    assert re.fullmatch(rf"{stamp}/collect//\d+", failed["id"])
    assert int(failed["id"].rsplit("/", 1)[1]) == int(ok["id"].rsplit("/", 1)[1]) + 1
    assert ok["stats"] == '{"alpha":[1.0,"主题"],"kept":true,"zeta":1}'
    assert (ok["status"], ok["error"], ok["topic_id"]) == ("ok", None, None)
    assert re.fullmatch(stamp, ok["started_at"]) and re.fullmatch(stamp, ok["finished_at"])
    assert (failed["status"], failed["error"]) == ("failed", "collect rss feed: connection refused")


def test_refuses_newer_schema() -> None:
    conn = raw()
    conn.execute(f"PRAGMA user_version = {len(db._migrations()) + 1}")
    with pytest.raises(db.SchemaTooNew):
        db._migrate(conn)


def test_immediate_commits_or_rolls_back() -> None:
    conn = db.connect(":memory:")
    with db.immediate(conn):
        conn.execute("INSERT INTO search_daily_usage VALUES ('d1', 1)")
    with pytest.raises(ValueError):
        with db.immediate(conn):
            conn.execute("INSERT INTO search_daily_usage VALUES ('d2', 1)")
            raise ValueError("abort")
    assert not conn.in_transaction
    assert [r[0] for r in conn.execute("SELECT day FROM search_daily_usage")] == ["d1"]
    row = conn.execute("SELECT day, requests FROM search_daily_usage").fetchone()
    assert row["day"] == "d1"


def test_a_refused_open_closes_its_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "newer.db"
    conn = db.connect(path)
    conn.execute(f"PRAGMA user_version = {len(db._migrations()) + 1}")
    conn.close()
    opened: list[sqlite3.Connection] = []
    real = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: opened.append(real(*a, **k)) or opened[-1])
    with pytest.raises(db.SchemaTooNew, match="newer than this binary"):
        db.connect(path)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


def test_an_opener_waits_for_another_connections_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A database one migration behind, its migration held open by another
    # connection for longer than the busy budget of an ordinary open.
    monkeypatch.setattr(db, "_OPEN_SECONDS", 0.3)
    path = tmp_path / "isbe.db"
    first = sqlite3.connect(path, autocommit=True, check_same_thread=False)
    first.execute("PRAGMA journal_mode=WAL")
    paperrefs.register(first)
    for sql in db._migrations()[:-1]:
        first.executescript(sql)
    first.execute(f"PRAGMA user_version = {len(db._migrations()) - 1}")
    first.execute("BEGIN IMMEDIATE")
    release = threading.Timer(1.0, lambda: first.execute("COMMIT"))
    release.start()
    started = time.monotonic()
    lines: list[str] = []
    try:
        conn = db.connect(path, lines.append)
    finally:
        release.join()
        first.close()
    assert time.monotonic() - started >= 1.0
    # It says why it waits, then (the other connection only held the lock) migrates.
    assert lines[0] == f"waiting for {path}: another process is writing to it…"
    assert lines[1].startswith(f"migrating {path} from schema v{len(db._migrations()) - 1}")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db._migrations())
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    conn.close()


def filled(path: Path) -> sqlite3.Connection:
    """A current database whose file is mostly free pages."""
    conn = db.connect(path)
    conn.execute("CREATE TABLE filler (body TEXT)")
    conn.executemany("INSERT INTO filler VALUES (?)", [("x" * 4000,) for _ in range(3000)])
    conn.execute("DELETE FROM filler")
    assert conn.execute("PRAGMA freelist_count").fetchone()[0] > 2000
    return conn


def test_compact_gives_free_pages_back_and_says_when_it_cannot(tmp_path: Path, caplog) -> None:  # type: ignore[no-untyped-def]
    conn = filled(tmp_path / "isbe.db")
    conn.execute("PRAGMA busy_timeout = 0")
    writer = sqlite3.connect(tmp_path / "isbe.db", autocommit=True)
    writer.execute("BEGIN IMMEDIATE")
    with caplog.at_level("WARNING", logger="isbe.core.db"):
        assert db.compact(conn) is False
    assert "could not compact the database" in caplog.text
    assert conn.execute("PRAGMA freelist_count").fetchone()[0] > 2000
    writer.execute("COMMIT")
    writer.close()
    assert db.compact(conn) is True
    assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0
    # A file with little free space is left alone.
    assert db.compact(conn) is True


def test_upgrade_says_what_takes_time_and_retries_compaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "isbe.db"
    raw = sqlite3.connect(path, autocommit=True)
    for sql in db._migrations()[:5]:
        raw.executescript(sql)
    raw.execute("PRAGMA user_version = 5")
    raw.close()
    lines: list[str] = []
    monkeypatch.setattr(db, "_PROGRESS_SECONDS", 0.0)
    monkeypatch.setattr(db, "_PROGRESS_STEPS", 1)
    db.upgrade(path, lines.append)
    assert lines[0].startswith(f"migrating {path} from schema v5 to v")
    assert lines[1].startswith(f"still migrating {path} (") and len(lines) > 3
    assert re.fullmatch(rf"migrated {re.escape(str(path))} in \d+\.\d s", lines[-1])
    monkeypatch.setattr(db, "_PROGRESS_SECONDS", 30.0)
    # Up to date: silent, but a file left mostly free (a compaction that failed
    # earlier) is compacted now.
    filled(path).close()
    lines.clear()
    db.upgrade(path, lines.append)
    assert lines == []
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0
    # It only upgrades: a missing file is an error, not a new database.
    with pytest.raises(sqlite3.OperationalError):
        db.upgrade(tmp_path / "missing.db", lines.append)
    assert not (tmp_path / "missing.db").exists()
