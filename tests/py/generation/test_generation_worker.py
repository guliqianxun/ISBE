"""The worker: sequential execution under the
execution lock, frozen topics, late keys, owner isolation, durable results
and the safe failure messages. Tests taking `stages` run with the doubles of
`generation_helpers` (collection, triage; enrichment and the digest are
real) and again with every real stage."""

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from generation_helpers import (
    OWNER,
    REPORT,
    Request,
    Script,
    copy_topic,
    count,
    fake_stages,
    finished,
    make_root,
    papers,
    response,
    workspace,
)

from isbe.core import db, locks, settings
from isbe.generation import Manager, Stages, StartError, failure_message

type Open = Callable[..., Manager]
type Start = Callable[[Callable[[int, Request], tuple[int, Any]]], Script]


def test_generation_sequential_worker_freezes_topics_reads_keys_late_and_isolates_owners(
    tmp_path: Path, script: Start, managers: Open, stages: Stages
) -> None:
    arrived, release = threading.Event(), threading.Event()

    def answer(i: int, req: Request) -> tuple[int, Any]:
        if i == 0:
            arrived.set()
            assert release.wait(10)
        if i % 3 == 0:
            return 200, papers()
        return 200, response(REPORT if i % 3 == 1 else "[]")

    mock = script(answer)
    root = make_root(tmp_path / "data", mock.base)
    settings.save(root, {"OPENAI_API_KEY": "fake-original-key-never-persist", "ISBE_LLM_NO_AUTH": "false"})
    cli = locks.execution_lock(root)
    manager = managers(root, stages=stages)
    other = workspace(root)
    copy_topic(other, "papers")
    a = manager.submit("local", "papers", "one")
    b = manager.submit(OWNER, "papers", "two")
    (other / "topics/papers.yaml").write_text("invalid changed topic")
    settings.save(root, {"OPENAI_API_KEY": "fake-new-key-never-persist"})
    cli.release()
    manager.wake()
    assert arrived.wait(5)
    running, queued = manager.get(a.id), manager.get(b.id)
    assert running is not None and running.state == "running" and running.stage == "collect"
    assert queued is not None and queued.state == "queued" and queued.position == 1
    with pytest.raises(locks.LockBusy):
        locks.execution_lock(root)
    with pytest.raises(locks.LockBusy):
        locks.execution_lock(other)
    with pytest.raises(StartError) as caught:
        manager.cancel(a.id, None)
    assert caught.value.status == 409
    release.set()
    assert finished(manager, a.id).state == "succeeded"
    done = finished(manager, b.id)
    assert done.state == "succeeded" and done.stage == "digest"
    assert done.result is not None and done.result.topic == "papers"
    assert mock.count == 6
    for i, req in enumerate(mock.requests):
        if i % 3 == 0:
            assert req.path.startswith("/api/search?")
        else:
            assert req.path == "/chat/completions"
            assert req.headers["Authorization"] == "Bearer fake-new-key-never-persist"
            assert req.json()["model"] == "first-model"
    for data, job in ((root, a.id), (other, b.id)):
        assert count(data / "isbe.db", "SELECT count(*) FROM reports") == 1
        assert count(data / "isbe.db", "SELECT count(*) FROM runs WHERE task_id = ?", job) == 4
    conn = db.connect(root / "isbe.db")
    stored = conn.execute("SELECT topic_snapshot || settings_fingerprint FROM tasks WHERE id = ?", (a.id,)).fetchone()[0]
    assert "fake-new-key" not in stored and "fake-original-key" not in stored
    listed = json.dumps([job.to_json() for job in manager.list(None)])
    for secret in ("fake-new-key", "settings_fingerprint", "topic_snapshot", "127.0.0.1"):
        assert secret not in listed


def test_generation_terminal_result_waits_for_database_without_repeating_pipeline(
    tmp_path: Path, script: Start, managers: Open, stages: Stages
) -> None:
    arrived, release = threading.Event(), threading.Event()

    def answer(i: int, req: Request) -> tuple[int, Any]:
        if i == 1:
            arrived.set()
            assert release.wait(10)
        if i % 3 == 0:
            return 200, papers()
        return 200, response(REPORT if i % 3 == 1 else "[]")

    mock = script(answer)
    root = make_root(tmp_path / "data", mock.base)
    cli = locks.execution_lock(root)
    manager = managers(root, stages=stages)
    other = workspace(root)
    copy_topic(other, "papers")
    first = manager.submit(OWNER, "papers", "first")
    next = manager.submit("local", "papers", "next")
    cli.release()
    manager.wake()
    assert arrived.wait(5)
    read = db.connect(root / "isbe.db")
    owner_db = db.connect(other / "isbe.db")
    writer = db.connect(root / "isbe.db")
    writer.execute("BEGIN IMMEDIATE")
    release.set()
    deadline = time.monotonic() + 5
    query = "SELECT EXISTS(SELECT 1 FROM runs WHERE task_id = ? AND job = 'digest' AND status = 'ok')"
    while not owner_db.execute(query, (first.id,)).fetchone()[0]:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    # Exceed SQLite's five-second busy timeout while the pipeline has finished.
    time.sleep(5.5)
    assert mock.count == 3
    state = "SELECT state FROM tasks WHERE id = ?"
    assert read.execute(state, (first.id,)).fetchone()[0] == "running"
    assert read.execute(state, (next.id,)).fetchone()[0] == "queued"
    writer.execute("COMMIT")
    manager.wake()
    assert finished(manager, first.id).state == "succeeded"
    assert finished(manager, next.id).state == "succeeded"
    assert owner_db.execute("SELECT count(*) FROM runs WHERE task_id = ?", (first.id,)).fetchone()[0] == 4
    assert mock.count == 6


def test_generation_configuration_changes_fail_explicitly_without_executing(tmp_path: Path, managers: Open) -> None:
    root = make_root(tmp_path / "data")
    cli = locks.execution_lock(root)
    manager = managers(root)
    job = manager.submit("local", "papers", "one")
    settings.save(root, {"ISBE_LLM_MODEL_SMART": "new-model"})
    cli.release()
    manager.wake()
    done = finished(manager, job.id)
    assert done.state == "failed"
    assert done.error == "Service settings changed while this task was queued. Review them and submit again."
    assert count(root / "isbe.db", "SELECT count(*) FROM runs") == 0


def test_generation_active_admin_policy_is_loaded_from_database_at_submit_and_execution(
    tmp_path: Path, managers: Open
) -> None:
    root = make_root(tmp_path / "data")
    cli = locks.execution_lock(root)
    manager = managers(root)
    other = workspace(root)
    copy_topic(other, "papers")
    raw = (other / "topics/papers.yaml").read_text() + "window_days: 366\nrss: {feeds: []}\n"
    (other / "topics/papers.yaml").write_text(raw)
    with pytest.raises(StartError) as caught:
        manager.submit(OWNER, "papers", "one")
    assert caught.value.code == "unsupported_topic"
    conn = db.connect(root / "isbe.db")
    conn.execute(
        "INSERT INTO auth_users (id, username, role, active, password_salt, password_hash, password_iterations) "
        "VALUES (?, 'admin-two', 'admin', 1, zeroblob(16), zeroblob(32), 600000)",
        (OWNER,),
    )
    job = manager.submit(OWNER, "papers", "one")
    conn.execute("UPDATE auth_users SET active = 0 WHERE id = ?", (OWNER,))
    cli.release()
    manager.wake()
    done = finished(manager, job.id)
    assert (done.state, done.error) == ("failed", failure_message("collect"))
    assert not (other / "isbe.db").exists(), "rejected task must not execute its pipeline"


def test_generation_linked_report_directories_fail_the_task_before_it_runs(tmp_path: Path, managers: Open) -> None:
    # Linked workspace and nested report directories are rejected at execution
    # (the path checks themselves are tests/py/core/test_paths.py).
    root = make_root(tmp_path / "data")
    outside = tmp_path / "outside"
    outside.mkdir()
    cli = locks.execution_lock(root)
    manager = managers(root)
    other = workspace(root)
    copy_topic(other, "papers")
    job = manager.submit(OWNER, "papers", "one")
    (other / "reports").mkdir()
    (other / "reports" / "papers").symlink_to(outside)
    cli.release()
    manager.wake()
    done = finished(manager, job.id)
    assert (done.state, done.stage, done.error) == ("failed", "collect", failure_message("collect"))
    assert not (other / "isbe.db").exists()
    assert list(outside.iterdir()) == []


ATOM_ERROR = """<feed xmlns="http://www.w3.org/2005/Atom">
  <title>ArXiv Query</title><id>http://arxiv.org/api/query</id>
  <updated>2007-10-12T00:00:00Z</updated>
  <entry><id>http://arxiv.org/api/errors#invalid_query</id>
    <title>Error</title><summary>invalid search query</summary>
    <updated>2007-10-12T00:00:00Z</updated>
  </entry>
</feed>"""


def test_generation_arxiv_error_feed_stops_at_collection_instead_of_reporting_no_papers(
    tmp_path: Path, script: Start, managers: Open, stages: Stages
) -> None:
    mock = script(lambda i, req: (200, ATOM_ERROR))
    root = make_root(tmp_path / "data", mock.base)
    settings.save(root, {"ESSAIES_URL": None, "ARXIV_API_URL": mock.base})
    manager = managers(root, stages=stages)
    job = manager.submit("local", "papers", "arxiv-error")
    done = finished(manager, job.id)
    assert (done.state, done.stage) == ("failed", "collect")
    assert done.error == failure_message("collect")
    assert count(root / "isbe.db", "SELECT count(*) FROM runs") == 1, "no screening or generation should start"
    conn = db.connect(root / "isbe.db")
    error = conn.execute("SELECT error FROM runs WHERE task_id = ?", (job.id,)).fetchone()[0]
    assert "arXiv API error: invalid search query" in error
    assert mock.count == 1


@pytest.mark.parametrize("mode", ["empty", "partial", "collect-error"])
def test_generation_empty_collection_and_partial_reports_are_failures_with_safe_feedback(
    tmp_path: Path, script: Start, managers: Open, stages: Stages, mode: str
) -> None:
    def answer(i: int, req: Request) -> tuple[int, Any]:
        if mode == "empty":
            return 200, {"items": []}
        if mode == "collect-error":
            return 200, {"provider": "private-path-and-secret"}
        if i == 0:
            return 200, papers()
        return 200, response("## TL;DR\nAn incomplete report." if i == 1 else "[]")

    mock = script(answer)
    root = make_root(tmp_path / "data", mock.base)
    manager = managers(root, stages=stages)
    job = manager.submit("local", "papers", "one")
    done = finished(manager, job.id)
    assert done.state == "failed", done
    assert done.result is None
    assert "private-path-and-secret" not in json.dumps(done.to_json())
    if mode == "empty":
        assert done.error is not None and done.error.startswith("No papers remain")
    elif mode == "partial":
        assert (done.stage, done.error) == ("digest", failure_message("digest"))
    else:
        assert (done.stage, done.error) == ("collect", failure_message("collect"))
    db_path = root / "isbe.db"
    assert count(db_path, "SELECT count(*) FROM reports") == (1 if mode == "partial" else 0)
    assert count(db_path, "SELECT count(*) FROM runs WHERE status = 'failed'") == 1
    assert mock.count == (3 if mode == "partial" else 1)


def test_generation_triage_and_enrichment_failures_keep_the_cli_abstract_fallback(
    tmp_path: Path, script: Start, managers: Open, stages: Stages
) -> None:
    def answer(i: int, req: Request) -> tuple[int, Any]:
        if i == 0:
            return 200, papers()
        if i == 1:
            return 400, response("not valid judge JSON")
        return 200, response(REPORT if i == 2 else "[]")

    mock = script(answer)
    root = make_root(tmp_path / "data", mock.base)
    topic = (root / "topics/papers.yaml").read_text() + "retrieval:\n  intent: Test learning\n"
    (root / "topics/papers.yaml").write_text(topic)
    settings.save(root, {"ISBE_FULLTEXT_EXTRACTOR": "metrail"})
    manager = managers(root, stages=stages)
    job = manager.submit("local", "papers", "one")
    done = finished(manager, job.id)
    assert done.state == "succeeded", done
    conn = db.connect(root / "isbe.db")
    for stage in ("triage", "enrich"):
        assert conn.execute("SELECT status FROM runs WHERE job = ?", (stage,)).fetchone()[0] == "failed"
    assert mock.count == 4


def test_generation_stage_failures_name_the_stage(tmp_path: Path, managers: Open) -> None:
    """A failure outside the tolerated stages is reported by the stage it happened in."""
    root = make_root(tmp_path / "data")

    def broken(*args: Any) -> None:
        raise RuntimeError("provider said private-secret")

    def no_report(conn: Any, llm: Any, run: str, topic: Any, data: Path, stats: dict[str, Any]) -> None:
        stats["papers"] = 1

    manager = managers(root, stages=fake_stages(collect=lambda conn, topic, ep, stats: None, digest=no_report))
    conn = db.connect(root / "isbe.db")
    conn.execute(
        "INSERT INTO items (id, kind, source, title, url, lang, published_at) "
        "VALUES ('arxiv:1', 'paper', 'essaies', 't', 'u', 'en', ?)",
        (db.now()[:10] + "T00:00:00Z",),
    )
    conn.execute("INSERT INTO topic_items (topic_id, item_id, first_seen) VALUES ('papers', 'arxiv:1', ?)", (db.now(),))
    job = manager.submit("local", "papers", "one")
    done = finished(manager, job.id)
    assert (done.state, done.stage, done.error) == ("failed", "digest", failure_message("digest"))
    manager.close()
    manager = managers(root, stages=fake_stages(collect=broken))
    job = manager.submit("local", "papers", "two")
    done = finished(manager, job.id)
    assert (done.stage, done.error) == ("collect", failure_message("collect"))
    assert "private-secret" not in json.dumps(done.to_json())
    error = conn.execute("SELECT error FROM runs WHERE task_id = ?", (job.id,)).fetchone()[0]
    assert error == "provider said private-secret"
