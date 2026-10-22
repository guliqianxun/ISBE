"""What the console needs from the report queue.

The queue lives in `isbe/generation/`, so the web layer talks to it only
through this structural interface and neither package imports the other.
The routes call exactly these methods:

- `list(owner)`: every task of `owner` (all owners for None), newest first
  (`ORDER BY seq DESC`). `GET /api/tasks`, `GET /api/generation`, and the
  queued tasks cancelled when an administrator deactivates an account.
- `get(id)`: one task or None. `GET /api/tasks/<id>`; the route checks the owner.
- `submit(owner, topic, request_id)`: queue a report, or return the existing
  task for the same idempotency key. `POST /api/tasks`, `POST /api/generation`.
- `cancel(id, owner)`: cancel a queued task of `owner` (any owner for None).
  `POST /api/tasks/<id>/cancel`.
- `close()`: stop and join the worker thread.

`submit` and `cancel` raise a `StartError` (any exception with its four
attributes) for every refusal; the route answers `status` with
`{"code", "error"[, "job"]}`. Any other exception is an internal error
(logged, generic 500), as for errors of `list` and `get`.

Starting the worker: `serve` opens the queue with `Manager.open(data)`, which
recovers interrupted tasks, takes `.queue-worker.lock` and spawns the worker,
after binding the socket and before serving. `isbe.web.serve` does the same
with its `generation` argument, a `ManagerFactory` such as
`isbe.generation.Manager.open`, and calls `close()` when the server stops.
Until the queue is wired in, `serve` runs with `UnavailableManager`: no tasks,
and submissions refused with a "queue unavailable" error.
"""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "Job",
    "Manager",
    "ManagerFactory",
    "Report",
    "StartError",
    "UnavailableManager",
    "error_json",
    "job_json",
]


class Report(Protocol):
    """A finished task's report (`tasks.result_period` with the task's topic)."""

    @property
    def topic(self) -> str: ...
    @property
    def period(self) -> str: ...


class Job(Protocol):
    """One row of `tasks`, field for field.

    `state` is queued, running, succeeded, failed, cancelled or interrupted;
    `position` is the 1-based place in the queue while queued, else None.
    Timestamps are `db.now()` strings.
    """

    @property
    def id(self) -> str: ...
    @property
    def owner(self) -> str: ...
    @property
    def topic(self) -> str: ...
    @property
    def state(self) -> str: ...
    @property
    def stage(self) -> str: ...
    @property
    def error(self) -> str | None: ...
    @property
    def result(self) -> Report | None: ...
    @property
    def created_at(self) -> str: ...
    @property
    def started_at(self) -> str | None: ...
    @property
    def finished_at(self) -> str | None: ...
    @property
    def position(self) -> int | None: ...


@runtime_checkable
class StartError(Protocol):
    """A refused submission or cancellation.

    `status` is the HTTP status (never serialized), `code` a stable
    machine-readable name (`invalid_topic`, `queue_limit`, `not_queued`, …),
    `error` the safe message shown to the user, `job` None unless the refusal
    carries a task. Must not hold paths, settings or provider text.
    """

    status: int
    code: str
    error: str

    @property
    def job(self) -> Job | None: ...


class Manager(Protocol):
    def list(self, owner: str | None) -> Sequence[Job]: ...
    def get(self, id: str) -> Job | None: ...
    def submit(self, owner: str, topic: str, request_id: str) -> Job: ...
    def cancel(self, id: str, owner: str | None) -> Job: ...
    def close(self) -> None: ...


# Opens the queue under `root`; raising aborts `serve`.
type ManagerFactory = Callable[[Path], Manager]


def job_json(job: Job) -> dict[str, Any]:
    """The job as the JSON object the API returns."""
    result = job.result
    return {
        "id": job.id,
        "owner": job.owner,
        "topic": job.topic,
        "state": job.state,
        "stage": job.stage,
        "error": job.error,
        "result": None if result is None else {"topic": result.topic, "period": result.period},
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "position": job.position,
    }


def error_json(error: StartError) -> dict[str, Any]:
    """The refusal as the API's JSON error: `job` only when present, `status` never."""
    out: dict[str, Any] = {"code": error.code, "error": error.error}
    if error.job is not None:
        out["job"] = job_json(error.job)
    return out


class _Refused(Exception):
    def __init__(self, status: int, code: str, error: str) -> None:
        super().__init__(error)
        self.status = status
        self.code = code
        self.error = error
        self.job: Job | None = None


class UnavailableManager:
    """The queue before `isbe.generation` is wired in: no tasks, and every
    submission refused with an `unavailable` error (500, `queue`)."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def list(self, owner: str | None) -> list[Job]:
        return []

    def get(self, id: str) -> Job | None:
        return None

    def submit(self, owner: str, topic: str, request_id: str) -> Job:
        raise _Refused(500, "queue", "The task queue is unavailable. Try again later.")

    def cancel(self, id: str, owner: str | None) -> Job:
        raise _Refused(404, "not_found", "Task not found.")

    def close(self) -> None:
        pass
