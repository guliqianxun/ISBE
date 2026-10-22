"""The report queue's routes: `/api/tasks*` and `/api/generation`.

Members see and cancel their own tasks; administrators see every task.
`/api/generation` is the console's "latest report task" of the caller.
"""

import secrets
from typing import Any

from isbe import auth

from .console import Console
from .generation_api import Job, StartError, error_json, job_json
from .wire import (
    STR,
    Reply,
    Request,
    error,
    fields,
    json_reply,
    not_found,
    request_json,
)

_SUBMIT = (("topic", STR, False), ("request_id", STR, True))


def _job(job: Job | None) -> dict[str, Any] | None:
    return None if job is None else job_json(job)


def route(console: Console, req: Request, user: auth.User) -> Reply:
    if (refused := console.access.rejects(req)) is not None:
        return refused
    path, manager, token = req.path, console.generation, console.access.token
    scope = None if user.role == "admin" else user.id
    if req.method == "GET":
        if path == "/api/generation":
            # Stable: equal timestamps keep the queue's order.
            jobs = sorted(manager.list(user.id), key=lambda j: j.created_at, reverse=True)
            return json_reply(200, {"csrf_token": token, "job": _job(jobs[0] if jobs else None)})
        if path == "/api/tasks":
            return json_reply(200, {"csrf_token": token, "tasks": [job_json(j) for j in manager.list(scope)]})
        id = path.removeprefix("/api/tasks/") if path.startswith("/api/tasks/") else None
        if id is not None and "/" not in id:
            job = manager.get(id)
            if job is not None and (scope is None or job.owner == scope):
                return json_reply(200, {"csrf_token": token, "job": job_json(job)})
        return not_found()
    if req.method != "POST":
        return error(405, "Use GET or POST for tasks.")
    body = request_json(req)
    rest = path.removeprefix("/api/tasks/") if path.startswith("/api/tasks/") else ""
    cancel = rest.removesuffix("/cancel") if rest.endswith("/cancel") else None
    try:
        if path in ("/api/tasks", "/api/generation"):
            submit = fields(body, _SUBMIT)
            if submit is None:
                return json_reply(
                    400, {"code": "invalid_topic", "error": "Choose a valid topic and request ID."}
                )
            key = submit["request_id"] if submit["request_id"] is not None else secrets.token_hex(16)
            job = manager.submit(user.id, submit["topic"], key)
        elif cancel is not None and "/" not in cancel:
            if body != {}:
                return error(400, "Cancellation does not accept options.")
            job = manager.cancel(cancel, scope)
        else:
            return not_found()
    except Exception as e:
        if isinstance(e, StartError):
            return json_reply(e.status, error_json(e))
        raise
    return json_reply(200 if path.endswith("/cancel") else 202, {"job": job_json(job)})
