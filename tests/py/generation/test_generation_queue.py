"""The queue itself: FIFO, idempotency, cancellation, limits, restart recovery,
validation and the submit/cancel races."""

import json
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from generation_helpers import OWNER, copy_topic, finished, make_root, workspace

from isbe.core import db, jsonfmt, locks
from isbe.generation import Job, Manager, StartError

type Open = Callable[..., Manager]


def submit(manager: Manager, owner: str, topic: str, request: str) -> Job:
    return manager.submit(owner, topic, request)


def refused(call: Callable[[], object]) -> StartError:
    with pytest.raises(StartError) as caught:
        call()
    return caught.value


def test_generation_durable_fifo_idempotency_cancellation_limits_and_restart(tmp_path: Path, managers: Open) -> None:
    root = make_root(tmp_path / "data")
    cli = locks.execution_lock(root)
    manager = managers(root)
    with pytest.raises(locks.LockBusy, match="Another queue worker is already running"):
        Manager.open(root)
    a = submit(manager, "local", "papers", "first")
    assert a.position == 1
    assert submit(manager, "local", "papers", "first").id == a.id
    assert submit(manager, "local", "papers", "alias").id == a.id
    for n in range(2, 6):
        copy_topic(root, f"p{n}")
        assert submit(manager, "local", f"p{n}", f"p{n}").position == n
    copy_topic(root, "p6")
    assert refused(lambda: manager.submit("local", "p6", "six")).status == 429
    assert refused(lambda: manager.submit("local", "p6", "first")).status == 409
    assert refused(lambda: manager.cancel(a.id, OWNER)).status == 404
    assert manager.cancel(a.id, "local").state == "cancelled"
    assert refused(lambda: manager.cancel(a.id, None)).status == 409
    assert submit(manager, "local", "papers", "alias").id == a.id
    six = submit(manager, "local", "p6", "six")
    assert six.position == 5
    other = workspace(root)
    copy_topic(other, "papers")
    b = submit(manager, OWNER, "papers", "first")
    assert a.id != b.id
    assert len(manager.list(OWNER)) == 1
    assert len(manager.list("local")) == 6
    manager.close()

    # A worker that died mid-task left it running with an open run.
    conn = db.connect(root / "isbe.db")
    conn.execute("UPDATE tasks SET state = 'running', started_at = ? WHERE id = ?", (db.now(), six.id))
    conn.execute(
        "INSERT INTO runs (id, job, topic_id, status, started_at, task_id) "
        "VALUES ('interrupted-run', 'collect', 'p6', 'running', ?, ?)",
        (db.now(), six.id),
    )
    manager = managers(root)
    run = conn.execute("SELECT status, error FROM runs WHERE id = 'interrupted-run'").fetchone()
    assert tuple(run) == ("failed", "Task interrupted by service restart")
    interrupted = manager.get(six.id)
    assert interrupted is not None and interrupted.state == "interrupted"
    assert interrupted.error == "Generation was interrupted by a service restart. Submit again to retry."
    assert interrupted.finished_at is not None and interrupted.position is None
    queued = manager.get(b.id)
    assert queued is not None and queued.state == "queued"
    assert submit(manager, "local", "papers", "alias").id == a.id
    assert len(manager.list(None)) == 7
    manager.close()
    cli.release()


def test_generation_rows_and_job_json_keep_the_stored_format(tmp_path: Path, managers: Open) -> None:
    root = make_root(tmp_path / "data")
    cli = locks.execution_lock(root)
    manager = managers(root)
    job = submit(manager, "local", "papers", "first")
    conn = db.connect(root / "isbe.db")
    row = conn.execute("SELECT * FROM tasks").fetchone()
    assert (row["seq"], row["owner"], row["topic"], row["request_id"]) == (1, "local", "papers", "first")
    assert (row["state"], row["stage"], row["error"], row["result_period"]) == ("queued", "queued", None, None)
    assert len(row["id"]) == 32 and set(row["id"]) <= set("0123456789abcdef")
    assert row["started_at"] is None and row["finished_at"] is None
    assert row["created_at"].endswith("Z") and len(row["created_at"]) == 24
    assert row["topic_snapshot"] == jsonfmt.dumps(
        {
            "arxiv": {"categories": ["cs.LG"], "include_keywords": ["learning"]},
            "cadence": "weekly",
            "id": "papers",
            "label": "Papers",
        }
    )
    requests = [tuple(r) for r in conn.execute("SELECT owner, request_id, task_id FROM task_requests")]
    assert requests == [("local", "first", job.id)]
    # The `Job` fields, in order, `null` for absent ones.
    assert list(job.to_json()) == [
        "id", "owner", "topic", "state", "stage", "error", "result",
        "created_at", "started_at", "finished_at", "position",
    ]
    assert job.to_json() == {
        "id": job.id, "owner": "local", "topic": "papers", "state": "queued", "stage": "queued",
        "error": None, "result": None, "created_at": row["created_at"], "started_at": None,
        "finished_at": None, "position": 1,
    }
    conn.execute("UPDATE tasks SET state = 'succeeded', result_period = '2026-W42' WHERE id = ?", (job.id,))
    done = manager.get(job.id)
    assert done is not None
    assert done.to_json()["result"] == {"topic": "papers", "period": "2026-W42"}
    assert done.position is None
    error = refused(lambda: manager.cancel(job.id, None))
    assert (error.status, error.to_json()) == (
        409, {"code": "not_queued", "error": "Only queued tasks can be cancelled."}
    )
    cli.release()


def test_generation_invalid_requests_and_shared_sources_return_safe_errors(tmp_path: Path, managers: Open) -> None:
    root = make_root(tmp_path / "data")
    manager = managers(root)
    for owner, topic, request, status in [
        ("../private", "papers", "one", 400),
        ("local", "../private", "one", 400),
        ("local", "missing", "one", 404),
        ("local", "papers", "", 400),
        ("local", "papers", "x" * 129, 400),
        ("local", "papers", "bad id", 400),
        ("local", "papers", "é", 400),
    ]:
        assert refused(lambda: manager.submit(owner, topic, request)).status == status
    other = workspace(root)
    copy_topic(other, "papers")
    text = (other / "topics/papers.yaml").read_text()
    text += "rss:\n  feeds:\n    - name: Private\n      url: http://127.0.0.1/private\n"
    (other / "topics/papers.yaml").write_text(text)
    assert refused(lambda: manager.submit(OWNER, "papers", "one")).code == "unsupported_topic"
    for extra in [
        "window_days: 366\n",
        "arxiv:\n  categories: [cs.LG]\n  include_keywords: [learning]\n  max_results: 201\n",
        "digest: {max_papers: 51}\n",
        "metrail: {poll_timeout_s: 301}\n",
        "metrail: {base_url: 'http://127.0.0.1/private'}\n",
    ]:
        copy_topic(other, "papers")
        original = (other / "topics/papers.yaml").read_text()
        if extra.startswith("arxiv:"):
            original = original.split("arxiv:")[0]
        (other / "topics/papers.yaml").write_text(original + extra)
        error = refused(lambda: manager.submit(OWNER, "papers", "limit"))
        assert (error.status, error.code) == (400, "unsupported_topic")
    (root / "settings.json").write_text('{"ISBE_LLM_PROVIDER":"private-secret"}')
    error = refused(lambda: manager.submit("local", "papers", "one"))
    assert (error.status, error.code) == (400, "configuration")
    assert "private-secret" not in json.dumps(error.to_json())
    assert manager.list(None) == []


def test_generation_submit_error_messages(tmp_path: Path, managers: Open) -> None:
    root = make_root(tmp_path / "data")
    manager = managers(root)
    (root / "topics/articles.yaml").write_text(
        "id: articles\nlabel: A\ncadence: weekly\nrss:\n  feeds:\n    - name: F\n      url: http://127.0.0.1/f\n"
    )
    (root / "topics/broken.yaml").write_text("id: broken\nlabel: B\ncadence: weekly\n")
    cases = {
        ("../x", "papers"): (400, "invalid_owner", "Choose a valid account."),
        ("local", "Bad"): (400, "invalid_topic", "Choose a valid topic."),
        ("local", "missing"): (404, "invalid_topic", "The topic is missing or invalid. Review and save it in Topics."),
        ("local", "broken"): (400, "invalid_topic", "The topic configuration is invalid."),
        ("local", "articles"): (400, "unsupported_topic", "Reports currently require an arXiv paper topic."),
    }
    for (owner, topic), expected in cases.items():
        error = refused(lambda: manager.submit(owner, topic, "r"))
        assert (error.status, error.code, error.error) == expected
        assert error.to_json() == {"code": expected[1], "error": expected[2]}
    error = refused(lambda: manager.cancel("nope", None))
    assert (error.status, error.code, error.error) == (404, "not_found", "Task not found.")
    (root / "settings.json").write_text(json.dumps({"ISBE_LLM_PROVIDER": "openai"}))
    error = refused(lambda: manager.submit("local", "papers", "r"))
    assert (error.status, error.code, error.error) == (
        400, "configuration", "Model configuration is incomplete or invalid. Review and save Settings."
    )
    (root / "settings.json").unlink()
    (root / "settings.json").mkdir()  # an unreadable settings file
    error = refused(lambda: manager.submit("local", "papers", "r"))
    assert error.error == "Service settings could not be loaded. Review and save Settings."


def test_generation_queue_database_failures_are_unavailable(tmp_path: Path, managers: Open) -> None:
    root = make_root(tmp_path / "data")
    manager = managers(root)
    manager.close()  # the worker would recreate the database; the queue methods still work
    (root / "isbe.db").unlink()
    for suffix in ("-wal", "-shm"):
        (root / f"isbe.db{suffix}").unlink(missing_ok=True)
    (root / "isbe.db").mkdir()
    error = refused(lambda: manager.submit("local", "papers", "r"))
    assert (error.status, error.to_json()) == (
        500, {"code": "queue", "error": "The task queue is unavailable. Try again later."}
    )
    assert refused(lambda: manager.cancel("x", None)).code == "queue"


def test_generation_concurrent_submissions_and_cancel_claim_race_keep_one_task(tmp_path: Path, managers: Open) -> None:
    root = make_root(tmp_path / "data")
    cli = locks.execution_lock(root)
    manager = managers(root)
    jobs: dict[str, Job] = {}
    start = threading.Barrier(2)

    def race(request: str) -> None:
        start.wait()
        jobs[request] = submit(manager, "local", "papers", request)

    threads = [threading.Thread(target=race, args=(r,)) for r in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert jobs["a"].id == jobs["b"].id
    cli.release()
    manager.wake()
    try:
        cancelled = manager.cancel(jobs["a"].id, "local")
    except StartError as error:
        assert error.status == 409
        assert finished(manager, jobs["a"].id).state != "cancelled"
    else:
        assert cancelled.state == "cancelled"
        assert finished(manager, jobs["a"].id).state == "cancelled"
    assert len(manager.list(None)) == 1


def test_generation_many_concurrent_duplicates_dedupe_to_one_task(tmp_path: Path, managers: Open) -> None:
    root = make_root(tmp_path / "data")
    cli = locks.execution_lock(root)
    manager = managers(root)
    results: list[str] = []
    start = threading.Barrier(8)

    def race(n: int) -> None:
        start.wait()
        results.append(submit(manager, "local", "papers", f"r{n % 4}").id)

    threads = [threading.Thread(target=race, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 1
    assert len(manager.list(None)) == 1
    conn = db.connect(root / "isbe.db")
    assert conn.execute("SELECT count(*) FROM task_requests").fetchone()[0] == 4
    cli.release()


def test_generation_cancel_and_claim_race_many_times(tmp_path: Path, managers: Open) -> None:
    """Whatever wins, a task is either cancelled and never run, or run and not cancelled."""
    root = make_root(tmp_path / "data")
    manager = managers(root)
    for n in range(20):
        job = submit(manager, "local", "papers", f"r{n}")
        manager.wake()
        try:
            manager.cancel(job.id, "local")
        except StartError as error:
            assert error.code == "not_queued"
            done = finished(manager, job.id)
            assert done.state == "failed" and done.stage == "collect"
            runs = db.connect(root / "isbe.db").execute(
                "SELECT count(*) FROM runs WHERE task_id = ?", (job.id,)
            ).fetchone()[0]
            assert runs == 1
        else:
            assert finished(manager, job.id).state == "cancelled"
            runs = db.connect(root / "isbe.db").execute(
                "SELECT count(*) FROM runs WHERE task_id = ?", (job.id,)
            ).fetchone()[0]
            assert runs == 0
