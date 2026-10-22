"""Opening the queue: recovering a crashed worker's task, broken workspaces
and the locks other processes hold."""

import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from generation_helpers import OWNER, finished, make_root, workspace

from isbe.core import db, locks
from isbe.generation import Manager
from isbe.generation.queue import INTERRUPTED, WORKSPACE_BROKEN

type Open = Callable[..., Manager]

REPO = Path(__file__).resolve().parents[3]
HOLD = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
print("locked", flush=True)
sys.stdin.read()
"""
# A server process whose worker is in the middle of collecting when it dies.
CRASH = """
import sys, time
from pathlib import Path
from isbe.core import settings
from isbe.generation import Manager, Stages

def collect(conn, topic, endpoints, stats):
    print("running", flush=True)
    time.sleep(600)

manager = Manager.open(Path(sys.argv[1]), stages=Stages(collect=collect), read_settings=settings.read)
manager.submit("local", "papers", "crash")
manager.wake()
time.sleep(600)
"""


def spawn(code: str, *args: str) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        [sys.executable, "-c", code, *args],
        cwd=REPO,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    ready = process.stdout.readline().strip()
    if ready not in ("locked", "running"):
        process.kill()
        raise AssertionError(f"helper process did not start: {ready!r}")
    return process


def queue_running(root: Path, id: str, owner: str) -> None:
    conn = db.connect(root / "isbe.db")
    conn.execute(
        "INSERT INTO tasks (id, owner, topic, request_id, state, stage, created_at, started_at, "
        "topic_snapshot, settings_fingerprint) VALUES (?, ?, 'papers', ?, 'running', 'collect', ?, ?, '{}', '')",
        (id, owner, id, db.now(), db.now()),
    )


def test_generation_crashed_worker_task_is_recovered_at_open(tmp_path: Path, managers: Open) -> None:
    root = make_root(tmp_path / "data")
    db.connect(root / "isbe.db").close()
    server = spawn(CRASH, str(root))
    try:
        with pytest.raises(locks.LockBusy, match="Another queue worker is already running"):
            Manager.open(root)
    finally:
        server.send_signal(signal.SIGKILL)
        server.wait()
    conn = db.connect(root / "isbe.db")
    task = conn.execute("SELECT id, state, stage FROM tasks").fetchone()
    assert tuple(task)[1:] == ("running", "collect")
    run = conn.execute("SELECT status FROM runs WHERE task_id = ?", (task[0],)).fetchone()
    assert run[0] == "running"
    # The kernel dropped both locks with the process; a new service starts and recovers.
    manager = managers(root)
    job = manager.get(task[0])
    assert job is not None
    assert (job.state, job.error) == ("interrupted", INTERRUPTED)
    run = conn.execute("SELECT status, error, finished_at FROM runs WHERE task_id = ?", (task[0],)).fetchone()
    assert tuple(run)[:2] == ("failed", "Task interrupted by service restart") and run[2] is not None
    again = manager.submit("local", "papers", "after-crash")
    assert finished(manager, again.id).state == "failed"  # it ran: collection is unreachable here


def test_generation_worker_waits_while_another_process_holds_the_execution_lock(
    tmp_path: Path, managers: Open
) -> None:
    root = make_root(tmp_path / "data")
    manager = managers(root)
    cli = spawn(HOLD, str(root / locks.EXECUTION))
    try:
        job = manager.submit("local", "papers", "one")
        manager.wake()
        time.sleep(1.5)
        waiting = manager.get(job.id)
        assert waiting is not None and waiting.state == "queued" and waiting.position == 1
        assert db.connect(root / "isbe.db").execute("SELECT count(*) FROM runs").fetchone()[0] == 0
    finally:
        assert cli.stdin is not None
        cli.stdin.close()
        cli.wait()
    assert finished(manager, job.id).state == "failed"  # it ran once the CLI finished


def test_generation_open_fails_while_another_process_runs_the_worker(tmp_path: Path) -> None:
    root = make_root(tmp_path / "data")
    root.mkdir(exist_ok=True)
    other = spawn(HOLD, str(root / locks.WORKER))
    try:
        with pytest.raises(locks.LockBusy, match="Another queue worker is already running"):
            Manager.open(root)
    finally:
        assert other.stdin is not None
        other.stdin.close()
        other.wait()
    Manager.open(root).close()


def test_broken_workspace_of_a_running_task_does_not_stop_the_service(
    tmp_path: Path, managers: Open, caplog: pytest.LogCaptureFixture
) -> None:
    root = make_root(tmp_path / "data")
    linked, corrupt = OWNER, "fedcba9876543210fedcba9876543210"
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"")
    (workspace(root, linked) / "isbe.db").symlink_to(outside)
    (workspace(root, corrupt) / "isbe.db").write_bytes(b"this is not a database" * 100)
    queue_running(root, "a" * 32, "local")
    queue_running(root, "b" * 32, linked)
    queue_running(root, "c" * 32, corrupt)
    conn = db.connect(root / "isbe.db")
    conn.execute(
        "INSERT INTO runs (id, job, topic_id, status, started_at, task_id) VALUES ('r', 'collect', 'papers', 'running', ?, ?)",
        (db.now(), "a" * 32),
    )
    with caplog.at_level(logging.ERROR, logger="isbe.generation.queue"):
        manager = managers(root)  # opening must not fail, or the service would not start
    states = {job.id: (job.state, job.error) for job in manager.list(None)}
    assert states == {
        "a" * 32: ("interrupted", INTERRUPTED),
        "b" * 32: ("failed", WORKSPACE_BROKEN),
        "c" * 32: ("failed", WORKSPACE_BROKEN),
    }
    assert conn.execute("SELECT status FROM runs WHERE id = 'r'").fetchone()[0] == "failed"
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "b" * 32 in logged and "c" * 32 in logged
    assert outside.read_bytes() == b"", "the linked database was never opened"
    assert os.path.islink(workspace(root, "local") / "workspaces" / linked / "isbe.db")
    # The service keeps serving: new tasks queue and run.
    job = manager.submit("local", "papers", "next")
    assert finished(manager, job.id).state == "failed"  # it ran: collection is unreachable here
