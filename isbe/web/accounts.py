"""Account administration."""

import logging
from sqlite3 import Connection

from isbe import auth
from isbe.core.errors import describe

from .console import Console
from .wire import (
    BOOL,
    STR,
    Reply,
    Request,
    error,
    fields,
    json_reply,
    not_found,
    request_json,
)

log = logging.getLogger(__name__)

_CREATE = (("username", STR, False), ("password", STR, False))
_UPDATE = (("password", STR, True), ("active", BOOL, True))


def auth_error(e: BaseException) -> Reply:
    status = auth.status(e)
    if status == 500:
        log.error("account request: %s", describe(e))
        return error(500, "The account request could not be completed.")
    return error(status, str(e))


def _user(user: auth.User) -> dict[str, object]:
    return {"id": user.id, "username": user.username, "role": user.role, "active": user.active}


def users(console: Console, conn: Connection, req: Request) -> Reply:
    """Administrators only; the caller checked the role."""
    path, method = req.path, req.method
    if path == "/api/users" and method == "GET":
        listed = [_user(u) for u in auth.list_users(conn)]
        return json_reply(200, {"users": listed, "csrf_token": console.access.token})
    if method not in ("POST", "PUT"):
        return error(405, "Use GET, POST or PUT for accounts.")
    body = request_json(req)
    id = path.removeprefix("/api/users/") if path.startswith("/api/users/") else ""
    update: dict[str, object] = {}
    try:
        if path == "/api/users" and method == "POST":
            create = fields(body, _CREATE)
            if create is None:
                return error(400, "Enter a username and password.")
            user = auth.create_user(conn, create["username"], create["password"], False)
        elif id and "/" not in id:
            if method != "PUT":
                return error(405, "Use PUT to update an account.")
            changes = fields(body, _UPDATE)
            if changes is None:
                return error(400, "Only password and active fields may be changed.")
            update = changes
            user = auth.update_user(conn, id, changes["password"], changes["active"])
        else:
            return not_found()
    except Exception as e:
        return auth_error(e)
    if update.get("active") is False:
        _cancel_queued(console, id)
    return json_reply(200, {"user": _user(user)})


def _cancel_queued(console: Console, owner: str) -> None:
    """A deactivated account's queued reports do not run (a cancel that fails is only logged)."""
    for task in console.generation.list(owner):
        if task.state == "queued":
            try:
                console.generation.cancel(task.id, owner)
            except Exception as e:
                log.warning("cancel task of deactivated account: %s", describe(e))
