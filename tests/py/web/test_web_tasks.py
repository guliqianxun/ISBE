"""The report queue's routes over the generation interface."""

from pathlib import Path

from conftest import TOKEN, FakeManager, FakeReport, add_user, call, login, make_console

from isbe.web.generation_api import UnavailableManager, error_json, job_json


def test_tasks_submit_list_get_and_cancel_with_owner_scope(data: Path) -> None:
    add_user(data, "admin", admin=True)
    add_user(data, "hank")
    add_user(data, "ivy")
    manager = FakeManager()
    console = make_console(data, shared=True, generation=manager)
    hank, ivy, admin = login(console, "hank"), login(console, "ivy"), login(console, "admin")
    first = call(console, "POST", "/api/tasks", {"topic": "papers", "request_id": "req-1"}, cookie=hank)
    assert first.status == 202
    job = first.body["job"]
    assert set(job) == {"id", "owner", "topic", "state", "stage", "error", "result", "created_at",
                        "started_at", "finished_at", "position"}
    replay = call(console, "POST", "/api/generation", {"topic": "papers", "request_id": "req-1"}, cookie=hank)
    assert (replay.status, replay.body["job"]["id"]) == (202, job["id"])
    generated = call(console, "POST", "/api/generation", {"topic": "papers"}, cookie=ivy)
    assert generated.status == 202
    # Without a request_id the console makes one: 16 random bytes in hex.
    keys = [key for owner, key in manager.requests if owner == generated.body["job"]["owner"]]
    assert len(keys) == 1 and len(keys[0]) == 32
    assert [j["id"] for j in call(console, "GET", "/api/tasks", cookie=hank).body["tasks"]] == [job["id"]]
    assert len(call(console, "GET", "/api/tasks", cookie=admin).body["tasks"]) == 2
    latest = call(console, "GET", "/api/generation", cookie=hank).body
    assert latest == {"csrf_token": TOKEN, "job": job}
    assert call(console, "GET", f"/api/tasks/{job['id']}", cookie=hank).body["job"] == job
    assert call(console, "GET", f"/api/tasks/{job['id']}", cookie=ivy).status == 404
    assert call(console, "GET", f"/api/tasks/{job['id']}", cookie=admin).status == 200
    for path in ["/api/tasks/", "/api/tasks/x/y", f"/api/tasks/{job['id']}/cancel"]:
        assert call(console, "GET", path, cookie=hank).body == {"error": "not found"}, path
    foreign = call(console, "POST", f"/api/tasks/{job['id']}/cancel", {}, cookie=ivy)
    assert (foreign.status, foreign.body) == (404, {"code": "not_found", "error": "Task not found."})
    options = call(console, "POST", f"/api/tasks/{job['id']}/cancel", {"force": True}, cookie=hank)
    assert (options.status, options.body) == (400, {"error": "Cancellation does not accept options."})
    cancelled = call(console, "POST", f"/api/tasks/{job['id']}/cancel", {}, cookie=hank)
    assert (cancelled.status, cancelled.body["job"]["state"]) == (200, "cancelled")
    again = call(console, "POST", f"/api/tasks/{job['id']}/cancel", {}, cookie=admin)
    assert (again.status, again.body["code"]) == (409, "not_queued")
    for path in ["/api/tasks/cancel", "/api/tasks/a/b/cancel", "/api/tasks/x"]:
        assert call(console, "POST", path, {}, cookie=hank).body == {"error": "not found"}, path
    put = call(console, "PUT", "/api/tasks", {}, cookie=hank)
    assert (put.status, put.body) == (405, {"error": "Use GET or POST for tasks."})
    bad = call(console, "POST", "/api/tasks", {"topic": "papers", "request_id": "bad id"}, cookie=hank)
    assert (bad.status, bad.body) == (400, {"code": "invalid_request", "error": "A valid request identifier is required."})


def test_job_and_error_json_serialization() -> None:
    manager = FakeManager()
    job = manager.submit("local", "papers", "r")
    job.state, job.result = "succeeded", FakeReport("papers", "2026-W42")
    assert job_json(job)["result"] == {"topic": "papers", "period": "2026-W42"}
    try:
        manager.cancel("missing", None)
    except Exception as e:
        assert error_json(e) == {"code": "not_found", "error": "Task not found."}  # type: ignore[arg-type]


def test_unavailable_queue_refuses_submissions_safely(data: Path) -> None:
    console = make_console(data, generation=UnavailableManager(data))
    assert call(console, "GET", "/api/tasks").body == {"csrf_token": TOKEN, "tasks": []}
    refused = call(console, "POST", "/api/generation", {"topic": "papers"})
    assert (refused.status, refused.body) == (
        500, {"code": "queue", "error": "The task queue is unavailable. Try again later."}
    )


def test_queue_failures_are_generic_errors(data: Path) -> None:
    class Broken(FakeManager):
        def list(self, owner: str | None) -> list:  # type: ignore[type-arg]
            raise OSError(f"database at {data} is locked")

    console = make_console(data, generation=Broken())
    result = call(console, "GET", "/api/tasks")
    assert result.status == 500
    assert str(data) not in result.text
