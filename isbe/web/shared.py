"""Authentication and workspace selection, before any data route.

Local mode serves one implicit administrator, `auth.local_user()`, whose
workspace is the data directory itself. Shared mode requires a session
cookie; each account works in `workspaces/<id>` (`core.paths.workspace`),
administrators may read another account's workspace with `?owner=<id>`,
and service settings, accounts, sources, runs, model calls, triage, the mail
test and `/help` are for administrators only.
"""

from contextlib import closing
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from isbe import auth, mail
from isbe.core import paths, settings
from isbe.reports import library

from . import accounts, routes, tasks, views
from .connect import open_db
from .console import Console
from .guards import cookie, session_token
from .pages import figure_owner_links, research_owner_links
from .routes_config import mail_route, topics_route
from .wire import (
    STR,
    Reply,
    Request,
    Respond,
    error,
    fields,
    json_reply,
    not_found,
    request_json,
)

_ADMIN_ONLY = frozenset({"/api/settings", "/api/runs", "/api/call", "/api/triage", "/api/mail/test", "/help"})


def user_json(user: auth.User | None) -> dict[str, Any] | None:
    if user is None:
        return None
    return {"id": user.id, "username": user.username, "role": user.role, "active": user.active}


def session(console: Console, user: auth.User | None) -> dict[str, Any]:
    snapshot = settings.snapshot(console.root)
    return {
        "mode": "shared" if console.shared else "local",
        "user": user_json(user),
        "csrf_token": console.access.token,
        "llm": {"ready": snapshot["llm"]["ready"] is True},
        "mail": {"configured": mail.status(console.root)["configured"] is True},
    }


def _no_store(response: Reply) -> Reply:
    """Every reply here is `Cache-Control: no-store`: a header that already says so
    is left alone, any other is replaced."""
    current = response.get("Cache-Control")
    if current is None or "no-store" not in current:
        response.set("Cache-Control", "no-store")
    return response


def route(console: Console, req: Request) -> Reply:
    """Answer one request; runs in a worker thread."""
    path = req.path
    # The UI contains no private data; every dynamic request below is checked.
    if not path.startswith("/api/") and not path.startswith("/figures/") and path != "/help":
        return routes.ui(console, req)
    if (refused := console.access.rejects(req)) is not None:
        return refused
    try:
        with closing(open_db(console.root)) as conn:
            return _no_store(_route(console, conn, req))
    except Respond as r:
        return _no_store(r.reply)


def _route(console: Console, conn: Connection, req: Request) -> Reply:
    path = req.path
    user = auth.authenticate(conn, session_token(req)) if console.shared else auth.local_user()
    if path == "/api/session" and req.method == "GET":
        return json_reply(200, session(console, user))
    if path == "/api/login" and req.method == "POST":
        return _login(console, conn, req)
    if user is None:
        return error(401, "Please sign in to continue.")
    return _authenticated(console, conn, req, user)


_LOGIN = (("username", STR, False), ("password", STR, False))


def _login(console: Console, conn: Connection, req: Request) -> Reply:
    if not console.shared:
        return error(400, "Local mode does not require login.")
    login = fields(request_json(req), _LOGIN)
    if login is None:
        return error(400, "Enter a username and password.")
    try:
        # The real client address, so one source cannot throttle everybody.
        user, token = auth.login(conn, login["username"], login["password"], req.client)
    except Exception as e:
        return accounts.auth_error(e)
    auth.logout(conn, session_token(req))
    return json_reply(200, session(console, user)).with_header(
        "Set-Cookie", cookie(token, console.secure_cookies)
    )


def _authenticated(console: Console, conn: Connection, req: Request, user: auth.User) -> Reply:
    path, method, admin = req.path, req.method, user.role == "admin"
    if path == "/api/logout" and method == "POST":
        if console.shared:
            auth.logout(conn, session_token(req))
        return json_reply(200, {"ok": True}).with_header("Set-Cookie", cookie("", console.secure_cookies))
    if path == "/api/users" or path.startswith("/api/users/"):
        if not admin:
            return error(403, "Administrator access is required.")
        return accounts.users(console, conn, req)
    if path == "/api/tasks" or path.startswith("/api/tasks/") or path == "/api/generation":
        return tasks.route(console, req, user)
    sources = path == "/api/sources" or path.startswith("/api/sources/")
    if not admin and (sources or path in _ADMIN_ONLY):
        return error(403, "Administrator access is required.")
    if path in ("/api/settings", "/help"):
        return routes.route(console, conn, console.root, req)
    owner = req.args.get("owner", user.id)
    if owner != user.id:
        if not admin or method != "GET":
            return error(403, "This workspace is not available.")
        if not any(u.id == owner for u in auth.list_users(conn)):
            return not_found()
    data = paths.workspace(console.root, owner)
    with closing(open_db(data)) as workspace:
        return _workspace(console, workspace, data, req, user, owner)


def _workspace(
    console: Console, conn: Connection, data: Path, req: Request, user: auth.User, owner: str
) -> Reply:
    path, member = req.path, console.shared and user.role != "admin"
    if path == "/api/topics" or path.startswith("/api/topics/"):
        id = path.removeprefix("/api/topics/") if path.startswith("/api/topics/") else None
        return topics_route(console, data, req, id, member)
    if path == "/api/overview":
        value = views.overview(conn, data, console.root)
        # Stage errors can contain provider details; ordinary users get task-safe errors.
        if member:
            value["runs"] = []
            for topic in value["topics"]:
                topic["last_run"] = None
            ready = session(console, user)["llm"]["ready"]
            value["services"] = {"llm": "configured" if ready else None}
        return json_reply(200, value)
    if path == "/api/report" and owner != user.id:
        found = library.report(conn, data, req.arg("topic"), req.arg("period"), articles=console.articles)
        if found is None:
            return not_found()
        if isinstance(found.get("html"), str):
            found["html"] = research_owner_links(figure_owner_links(found["html"], owner), owner)
        return json_reply(200, found)
    if member and path == "/api/mail" and req.method == "GET":
        return json_reply(
            200,
            {"mail": mail.status(console.root), "csrf_token": console.access.token, "recipients": ""},
        )
    if path == "/api/mail" or path.startswith("/api/mail/"):
        return mail_route(console, conn, data, console.root, req, user)
    return routes.route(console, conn, data, req)
