"""A question run's ways in and out (#89): an ask or rerun the queue refuses leaves
no run behind, concurrent asks with one request id make one run, every run can be
cancelled while active and deleted with all its rows, and the event stream ends
once it has sent `done`."""

import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import pytest
import question_flow as qf
from conftest import TOKEN, add_user, call, login, make_console
from test_web_question import (
    PREFIX,
    QUESTION,
    finished_run,
    parse,
    read_until,
    stream_reply,
)

from isbe.core import db, paths
from isbe.core.errors import HttpError
from isbe.question import papers, queue, runs, service


def rows(path: Path, run_id: str) -> dict[str, int]:
    """How many rows each workspace table and the queue hold for `run_id`."""
    with closing(db.connect(path / "isbe.db")) as conn:
        out = {t: conn.execute(f"SELECT count(*) FROM {t} WHERE run_id = ?", (run_id,)).fetchone()[0]
               for t in runs._TABLES}
        out["question_tasks"] = conn.execute(
            "SELECT count(*) FROM question_tasks WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
    return out


def listed(console, **kw) -> list[str]:
    return [r["run_id"] for r in call(console, "GET", PREFIX, **kw).body["runs"]]


def test_an_ask_over_the_limit_leaves_no_run(data: Path) -> None:
    console = make_console(data)
    made = [call(console, "POST", PREFIX, QUESTION) for _ in range(queue.ACTIVE_LIMIT)]
    assert [m.status for m in made] == [202] * queue.ACTIVE_LIMIT
    for _ in range(3):  # a client that retries on 429
        refused = call(console, "POST", PREFIX, {**QUESTION, "request_id": "over"})
        assert refused.status == 429 and "five question runs" in refused.body["error"]
    assert sorted(listed(console)) == sorted(m.body["run"]["run_id"] for m in made)
    with closing(db.connect(data / "isbe.db")) as conn:
        assert conn.execute("SELECT count(*) FROM question_runs").fetchone()[0] == queue.ACTIVE_LIMIT
        assert conn.execute("SELECT count(*) FROM question_events").fetchone()[0] == queue.ACTIVE_LIMIT
    # A replay of an accepted request id is still answered at the limit.
    first = call(console, "POST", PREFIX, {**QUESTION, "request_id": "late"})
    assert first.status == 429
    cancelled = call(console, "POST", f"{PREFIX}/{made[0].body['run']['run_id']}/cancel", {})
    assert cancelled.status == 200
    accepted = call(console, "POST", PREFIX, {**QUESTION, "request_id": "late"})
    again = call(console, "POST", PREFIX, {**QUESTION, "request_id": "late"})
    assert (accepted.status, again.status) == (202, 200)
    assert again.body["run"]["run_id"] == accepted.body["run"]["run_id"]


def test_concurrent_asks_with_one_request_id_make_one_run(data: Path) -> None:
    make_console(data)  # initializes the data directory
    n = 8
    barrier = threading.Barrier(n)
    answers: list[tuple[str, bool]] = []
    failures: list[BaseException] = []

    def ask() -> None:
        with closing(db.connect(data / "isbe.db")) as conn:
            barrier.wait()
            try:
                run, created = service.ask(data, conn, "local", {**QUESTION, "request_id": "race-1"})
                answers.append((run["run_id"], created))
            except BaseException as e:
                failures.append(e)

    threads = [threading.Thread(target=ask) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert failures == []
    assert len({run_id for run_id, _ in answers}) == 1
    assert sorted(created for _, created in answers) == [False] * (n - 1) + [True]
    with closing(db.connect(data / "isbe.db")) as conn:
        assert conn.execute("SELECT count(*) FROM question_runs").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM question_tasks").fetchone()[0] == 1


def test_a_rerun_over_the_limit_leaves_no_run(data: Path) -> None:
    console = make_console(data)
    run_id = finished_run(console, data)
    for _ in range(queue.ACTIVE_LIMIT):
        assert call(console, "POST", PREFIX, QUESTION).status == 202
    refused = call(console, "POST", f"{PREFIX}/{run_id}/rerun", {"changes": {"rationale": "again"}})
    assert refused.status == 429
    assert len(listed(console)) == queue.ACTIVE_LIMIT + 1
    with closing(db.connect(data / "isbe.db")) as conn:
        assert conn.execute("SELECT count(DISTINCT run_id) FROM question_plans").fetchone()[0] == 1


def test_a_run_the_queue_never_took_can_be_cancelled_and_deleted(data: Path) -> None:
    """Runs left behind by the old ask (created, then refused by the queue)."""
    console = make_console(data)
    with closing(db.connect(data / "isbe.db")) as conn:
        zombie = runs.create(conn, "left behind", "en")
    one = f"{PREFIX}/{zombie}"
    cancelled = call(console, "POST", f"{one}/cancel", {})
    assert (cancelled.status, cancelled.body["run"]["status"]) == (200, "cancelled")
    assert call(console, "POST", f"{one}/cancel", {}).status == 409
    assert [e["type"] for e in call(console, "GET", f"{one}/events").body["events"]] == ["state", "state", "done"]
    assert call(console, "DELETE", one).status == 204
    assert call(console, "GET", one).status == 404 and listed(console) == []


@pytest.mark.parametrize("state", ["queued", "paused", "review", "done", "cancelled"])
def test_a_run_at_rest_is_deleted_with_all_its_rows(data: Path, state: str) -> None:
    console = make_console(data)
    if state in ("done", "cancelled"):
        run_id = finished_run(console, data)
        if state == "cancelled":
            run_id = call(console, "POST", PREFIX, qf_like()).body["run"]["run_id"]
            assert call(console, "POST", f"{PREFIX}/{run_id}/cancel", {}).status == 200
    else:
        run_id = call(console, "POST", PREFIX, qf_like()).body["run"]["run_id"]
        if state != "queued":
            qf.drain(data, qf.services(countdown=60))  # the plan, then the review
        if state == "paused":
            assert call(console, "POST", f"{PREFIX}/{run_id}/pause", {}).body["run"]["status"] == "paused"
    before = rows(data, run_id)
    assert before["question_runs"] == 1 and before["question_tasks"] == 1
    gone = call(console, "DELETE", f"{PREFIX}/{run_id}")
    assert gone.status == 204, gone.body
    assert set(rows(data, run_id).values()) == {0}
    assert call(console, "GET", f"{PREFIX}/{run_id}").status == 404
    assert run_id not in listed(console)
    assert call(console, "DELETE", f"{PREFIX}/{run_id}").status == 404


def qf_like() -> dict:
    return {"question": qf.replay.sample(qf.SAMPLE).question["question"], "lang": "en", "engine": "openalex"}


def test_a_step_in_hand_must_be_cancelled_before_the_run_is_deleted(data: Path) -> None:
    console = make_console(data)
    run_id = call(console, "POST", PREFIX, QUESTION).body["run"]["run_id"]
    assert queue.claim(data, run_id) is not None  # the worker has it
    busy = call(console, "DELETE", f"{PREFIX}/{run_id}")
    assert busy.status == 409 and "cancel it" in busy.body["error"]
    assert rows(data, run_id)["question_runs"] == 1
    with pytest.raises(HttpError):
        queue.remove(data, run_id)


def test_only_the_owner_deletes_a_run(data: Path) -> None:
    add_user(data, "admin", admin=True)
    hank_user = add_user(data, "hank")
    add_user(data, "ivy")
    console = make_console(data, shared=True)
    hank, ivy, admin = login(console, "hank"), login(console, "ivy"), login(console, "admin")
    run_id = call(console, "POST", PREFIX, QUESTION, cookie=hank).body["run"]["run_id"]
    one = f"{PREFIX}/{run_id}"
    assert call(console, "POST", f"{one}/cancel", {}, cookie=hank).status == 200
    assert call(console, "DELETE", one, cookie=ivy).status == 404
    assert call(console, "DELETE", f"{one}?owner={hank_user.id}", cookie=admin).status == 403
    assert call(console, "DELETE", one, cookie=hank, token="wrong").status == 403
    assert call(console, "DELETE", one, cookie=hank).status == 204
    assert set(rows(paths.workspace(data, hank_user.id), run_id).values()) == {0}


def test_the_event_stream_ends_after_done(data: Path) -> None:
    console = make_console(data)
    run_id = finished_run(console, data)
    path = f"{PREFIX}/{run_id}/events"
    reply = stream_reply(console, path)
    sent: list[tuple[int, str, dict]] = []
    while (chunk := reply.stream.poll()) is not None:
        assert chunk, "a finished run's stream never waits"
        sent += parse(chunk)
    reply.stream.close()
    done = next(seq for seq, kind, _ in sent if kind == "done")
    assert sent[-1][0] == done and [seq for seq, _, _ in sent] == list(range(1, done + 1))
    # The browser comes back with Last-Event-ID: 204 tells it to stop.
    again = stream_reply(console, path, last=str(done))
    assert (again.status, again.stream) == (204, None)
    # Events after `done` (an override) are sent to whoever asks after it, then it ends.
    key = call(console, "GET", f"{PREFIX}/{run_id}/papers").body["papers"][0]["paper"]["key"]
    assert call(console, "POST", f"{PREFIX}/{run_id}/overrides", {"key": key, "decision": "exclude"}).status == 201
    later = stream_reply(console, path, last=str(done))
    assert [kind for _, kind, _ in parse(later.stream.poll())] == ["override"]
    assert later.stream.poll() is None
    # A run still going keeps its stream open.
    live = call(console, "POST", PREFIX, QUESTION).body["run"]["run_id"]
    open_one = stream_reply(console, f"{PREFIX}/{live}/events")
    assert parse(open_one.stream.poll())[0][1] == "state" and open_one.stream.poll() == b""
    open_one.stream.close()


def test_the_event_stream_closes_the_connection_after_done(live) -> None:
    server = live()
    console = server.console
    run_id = call(console, "POST", PREFIX, QUESTION, host=server.host).body["run"]["run_id"]
    assert call(console, "POST", f"{PREFIX}/{run_id}/cancel", {}, host=server.host).status == 200
    with closing(server.connect()) as sock:
        sock.sendall(
            f"GET {PREFIX}/{run_id}/events HTTP/1.1\r\nHost: {server.host}\r\n"
            f"Accept: text/event-stream\r\nX-ISBE-CSRF: {TOKEN}\r\n\r\n".encode()
        )
        # The chunked body ends (a zero-length chunk) right after `done`, long before
        # the five-minute cap.
        received = read_until(sock, b"\r\n0\r\n\r\n", timeout=5)
    assert b"event: done\n" in received


def test_a_failed_run_is_deleted_with_all_its_rows(data: Path) -> None:
    console = make_console(data)
    run_id = call(console, "POST", PREFIX, {**qf_like(), "review": False}).body["run"]["run_id"]
    essaies = qf.Essaies()
    essaies.fail = True
    qf.drain(data, qf.services(essaies=essaies))
    assert call(console, "GET", f"{PREFIX}/{run_id}").body["run"]["status"] == "failed"
    assert call(console, "DELETE", f"{PREFIX}/{run_id}").status == 204
    assert set(rows(data, run_id).values()) == {0}


def test_a_run_paused_by_a_worker_restart_can_be_deleted(data: Path) -> None:
    console = make_console(data)
    run_id = call(console, "POST", PREFIX, QUESTION).body["run"]["run_id"]
    assert queue.claim(data, run_id) is not None  # the worker died holding it
    assert [t.run_id for t in queue.stale(data)] == [run_id]
    # Until the restarted worker records the pause in the workspace, it is still stopping.
    assert call(console, "DELETE", f"{PREFIX}/{run_id}").status == 409
    with closing(db.connect(data / "isbe.db")) as conn:
        runs.set_status(conn, run_id, "paused")
    assert call(console, "DELETE", f"{PREFIX}/{run_id}").status == 204
    assert set(rows(data, run_id).values()) == {0}


def test_a_run_whose_worker_is_still_writing_its_end_waits_for_the_delete(data: Path) -> None:
    """flow.Step writes the queue (finished) before the workspace (state, done)."""
    console = make_console(data)
    run_id = call(console, "POST", PREFIX, QUESTION).body["run"]["run_id"]
    assert queue.claim(data, run_id) is not None
    queue.stopped(data, run_id, "finished")
    busy = call(console, "DELETE", f"{PREFIX}/{run_id}")
    assert busy.status == 409 and "still stopping" in busy.body["error"]
    with closing(db.connect(data / "isbe.db")) as conn:
        runs.set_status(conn, run_id, "failed")
        runs.emit(conn, run_id, [("done", {"status": "failed"})])
    assert call(console, "DELETE", f"{PREFIX}/{run_id}").status == 204


def settle(data: Path, run_id: str) -> None:
    """Age the run's task past `queue.SETTLE`, as if its last change were long ago."""
    with closing(db.connect(data / "isbe.db")) as conn, db.immediate(conn):
        conn.execute("UPDATE question_tasks SET updated_at = ? WHERE run_id = ?",
                     (queue.at(-queue.SETTLE - 1), run_id))


def test_a_run_whose_worker_died_between_its_writes_is_deleted_once_settled(data: Path) -> None:
    """A worker killed after the queue's `finished` and before the workspace's `done`:
    the delete waits `queue.SETTLE`, then goes (#89 review, round 2, N1)."""
    console = make_console(data)
    run_id = call(console, "POST", PREFIX, QUESTION).body["run"]["run_id"]
    assert queue.claim(data, run_id) is not None
    queue.stopped(data, run_id, "finished")
    assert call(console, "DELETE", f"{PREFIX}/{run_id}").status == 409
    settle(data, run_id)
    assert call(console, "DELETE", f"{PREFIX}/{run_id}").status == 204
    assert set(rows(data, run_id).values()) == {0}


def test_a_cancel_whose_workspace_write_failed_can_be_done_again(data: Path, monkeypatch) -> None:
    """The queue says finished, the run never got its `done`: once settled, a cancel
    finishes the run and a delete removes it."""
    console = make_console(data)
    run_id = call(console, "POST", PREFIX, QUESTION).body["run"]["run_id"]
    real = queue.cancelled

    def locked(conn, rid):
        raise HttpError(500, "database is locked")

    monkeypatch.setattr(queue, "cancelled", locked)
    assert call(console, "POST", f"{PREFIX}/{run_id}/cancel", {}).status == 500
    monkeypatch.setattr(queue, "cancelled", real)
    assert queue.get(data, run_id).state == "finished"
    # Until it settles the end may still be on its way: no second cancel.
    assert call(console, "POST", f"{PREFIX}/{run_id}/cancel", {}).status == 409
    settle(data, run_id)
    again = call(console, "POST", f"{PREFIX}/{run_id}/cancel", {})
    assert again.status == 200 and again.body["run"]["status"] == "cancelled"
    assert call(console, "POST", f"{PREFIX}/{run_id}/cancel", {}).status == 409
    assert call(console, "DELETE", f"{PREFIX}/{run_id}").status == 204


def test_the_worker_cannot_end_a_step_between_the_delete_check_and_the_delete(data: Path, monkeypatch) -> None:
    """The stopping check runs under the queue's write lock (#89 review, round 2, N2):
    a worker ending its step waits for the delete instead of slipping in between."""
    console = make_console(data)
    run_id = call(console, "POST", PREFIX, QUESTION).body["run"]["run_id"]
    assert queue.claim(data, run_id) is not None
    real = runs.ended
    seen: list[str] = []

    def ended(conn, rid):
        # The worker tries to end the step while the delete is deciding.
        other = sqlite3.connect(data / "isbe.db", timeout=0.2, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.execute("ROLLBACK")
            seen.append("free")
        except sqlite3.OperationalError as error:  # the queue's write lock is held
            seen.append(str(error))
        finally:
            other.close()
        return real(conn, rid)

    monkeypatch.setattr(runs, "ended", ended)
    queue.stopped(data, run_id, "finished")
    assert call(console, "DELETE", f"{PREFIX}/{run_id}").status == 409
    assert seen == ["database is locked"]


def test_a_cancelling_run_is_deleted_once_it_has_stopped(data: Path) -> None:
    console = make_console(data)
    run_id = call(console, "POST", PREFIX, QUESTION).body["run"]["run_id"]
    assert queue.claim(data, run_id) is not None
    cancelling = call(console, "POST", f"{PREFIX}/{run_id}/cancel", {})
    assert cancelling.body["run"]["cancelling"]
    assert call(console, "DELETE", f"{PREFIX}/{run_id}").status == 409
    queue.stopped(data, run_id, "finished")  # the worker reaches its next check
    with closing(db.connect(data / "isbe.db")) as conn:
        queue.cancelled(conn, run_id)
    assert call(console, "DELETE", f"{PREFIX}/{run_id}").status == 204


def test_a_run_without_a_queue_row_cannot_be_paused_or_resumed(data: Path) -> None:
    console = make_console(data)
    with closing(db.connect(data / "isbe.db")) as conn:
        zombie = runs.create(conn, "left behind", "en")
    for action in ("pause", "resume"):
        refused = call(console, "POST", f"{PREFIX}/{zombie}/{action}", {})
        assert refused.status == 409 and "only be cancelled or deleted" in refused.body["error"]


def test_a_delete_whose_second_write_fails_can_be_done_again(data: Path, monkeypatch) -> None:
    console = make_console(data)
    run_id = call(console, "POST", PREFIX, QUESTION).body["run"]["run_id"]
    real = runs.delete

    def locked(conn, run_id):
        raise db.sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(runs, "delete", locked)
    assert call(console, "DELETE", f"{PREFIX}/{run_id}").status == 500
    monkeypatch.setattr(runs, "delete", real)
    left = rows(data, run_id)
    assert (left["question_runs"], left["question_tasks"]) == (1, 0)
    assert call(console, "POST", f"{PREFIX}/{run_id}/cancel", {}).body["run"]["status"] == "cancelled"
    assert call(console, "DELETE", f"{PREFIX}/{run_id}").status == 204


def test_a_refused_ask_whose_clean_up_fails_still_answers_429(data: Path, monkeypatch, caplog) -> None:
    console = make_console(data)
    for _ in range(queue.ACTIVE_LIMIT):
        assert call(console, "POST", PREFIX, QUESTION).status == 202

    def locked(conn, run_id):
        raise db.sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(runs, "delete", locked)
    assert call(console, "POST", PREFIX, QUESTION).status == 429
    assert "could not delete it after the queue refused it" in caplog.text


def test_a_stream_on_a_run_that_is_deleted_ends(data: Path) -> None:
    console = make_console(data)
    run_id = call(console, "POST", PREFIX, QUESTION).body["run"]["run_id"]
    reply = stream_reply(console, f"{PREFIX}/{run_id}/events")
    assert parse(reply.stream.poll()) and reply.stream.poll() == b""  # queued, waiting
    assert call(console, "DELETE", f"{PREFIX}/{run_id}").status == 204
    assert reply.stream.poll() is None
    reply.stream.close()


@pytest.mark.parametrize("route", ["/papers", "/field", "/sets/s1.candidates", "/export.csv", ""])
def test_a_read_racing_a_delete_is_whole_or_a_404(data: Path, route: str, monkeypatch) -> None:
    # The run is deleted by another connection between the read's first rows and
    # the paper records they refer to: the read is the whole run (one snapshot).
    console = make_console(data)
    run_id = finished_run(console, data)
    one = f"{PREFIX}/{run_id}{route}"
    want = call(console, "GET", one)
    assert want.status == 200
    real_get, deleted = papers.get, []

    def get_after_a_delete(conn, refs):
        if not deleted:
            with closing(db.connect(data / "isbe.db")) as other:
                runs.delete(other, run_id)
            deleted.append(run_id)
        return real_get(conn, refs)

    monkeypatch.setattr(papers, "get", get_after_a_delete)
    got = call(console, "GET", one)
    assert (got.status, got.body) == (want.status, want.body)
    assert deleted == [run_id] or route == ""
    if deleted:
        assert call(console, "GET", one).status == 404
