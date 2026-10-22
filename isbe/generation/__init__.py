"""The durable generation queue and its single worker.

For the web layer:
- `Manager.open(root) -> Manager` at startup: takes `.queue-worker.lock`
  (raises `locks.LockBusy` if another server owns the root), recovers tasks a
  dead worker left running and starts the worker thread; it matches
  `isbe.web.generation_api.ManagerFactory`. `manager.close()` at shutdown
  waits for the current task (it is not interrupted); pass a `timeout` to
  bound the wait.
- `manager.list(owner) -> list[Job]`, `manager.get(id) -> Job | None`,
  `manager.submit(owner, topic, request_id) -> Job`,
  `manager.cancel(id, owner) -> Job`; the last two raise `StartError`, whose
  `status` and `to_json()` are the HTTP response. `owner` None means every
  owner (an admin's scope).
- `Job.to_json()` is the task object of `/api/generation` and `/api/tasks`.

The workspace layout and the execution lock are in `core.paths` and
`core.locks`. `Stages` is the pipeline seam tests replace; `GENERATION`
lists the settings a queued task is fingerprinted on (`fingerprint`).
"""

from .fingerprint import GENERATION
from .jobs import Job, Report, StartError
from .queue import Manager
from .stages import Endpoints, Stages
from .worker import failure_message

__all__ = [
    "GENERATION",
    "Endpoints",
    "Job",
    "Manager",
    "Report",
    "Stages",
    "StartError",
    "failure_message",
]
