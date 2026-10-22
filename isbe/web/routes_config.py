"""Routes that change configuration: service settings, report email and topic
files.
"""

import logging
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from isbe import auth, mail, topics
from isbe.core import settings
from isbe.core.errors import describe
from isbe.core.validators import Invalid

from .console import Console
from .wire import (
    ANY,
    STR,
    Reply,
    Request,
    Respond,
    error,
    fields,
    json_reply,
    request_json,
)

log = logging.getLogger(__name__)


def _changes(body: Any) -> dict[str, str | None] | None:
    """The settings changes in the body (names to strings or null), or None."""
    if not isinstance(body, dict) or not all(v is None or isinstance(v, str) for v in body.values()):
        return None
    return body


def settings_route(console: Console, data: Path, req: Request) -> Reply:
    """`/api/settings` of the service root `data`: GET the redacted view, PUT changes."""
    if (refused := console.access.rejects(req)) is not None:
        return refused
    if req.method == "PUT":
        changes = _changes(request_json(req))
        if changes is None:
            return error(400, "Expected an object of setting names and string or null values")
        try:
            settings.save(data, changes)
        except Invalid as e:
            return error(400, describe(e))
        except Exception as e:
            # Storage failures carry paths; the log keeps them, the reply does not.
            log.error("save settings: %s", describe(e))
            return error(500, "Settings could not be saved. Try again or review the service logs.")
    elif req.method != "GET":
        return error(405, "Use GET or PUT for settings")
    view = settings.snapshot(data)
    view["mail"] = mail.status(data)
    view["csrf_token"] = console.access.token
    return json_reply(200, view)


def _failed(status: int, message: str) -> Reply:
    return json_reply(status, {"state": "failed", "error": message})


_DELIVERY = (("topic", STR, False), ("period", STR, False), ("recipients", STR, False))
_TEST = (("recipients", STR, False),)


def mail_route(
    console: Console,
    conn: Connection,
    data: Path,
    settings_data: Path,
    req: Request,
    user: auth.User,
) -> Reply:
    """`/api/mail*`: reports of workspace `data` through the mail settings of `settings_data`.

    Every send counts against the signed-in account in the console's one
    `mail.Limiter`; a refusal answers its status with
    `Retry-After` when rate limited.
    """
    if (refused := console.access.rejects(req)) is not None:
        return refused
    path = req.path
    if req.method == "GET" and path == "/api/mail":
        try:
            values = settings.snapshot(settings_data)["values"]
        except Exception as e:
            log.error("mail settings: %s", describe(e))
            return _failed(500, "Could not read default recipients")
        return json_reply(
            200,
            {
                "mail": mail.status(settings_data),
                "csrf_token": console.access.token,
                "recipients": values.get("ISBE_EMAIL_DEFAULT_RECIPIENTS"),
            },
        )
    if req.method != "POST" or path not in ("/api/mail/report", "/api/mail/test"):
        return _failed(405, "Use GET /api/mail or POST /api/mail/report or /api/mail/test")
    body = request_json(req)
    admin = user.role == "admin"
    try:
        if path == "/api/mail/report":
            delivery = fields(body, _DELIVERY)
            if delivery is None:
                return _failed(400, "Only topic, period and recipients are accepted")
            result = mail.report(
                conn,
                data,
                settings_data,
                delivery["topic"],
                delivery["period"],
                delivery["recipients"],
                limiter=console.limiter,
                account=user.id,
                admin=admin,
            )
        else:
            test = fields(body, _TEST)
            if test is None:
                return _failed(400, "Only recipients are accepted for a test email")
            result = mail.test(
                settings_data, test["recipients"], limiter=console.limiter, account=user.id, admin=admin
            )
    except mail.MailError as e:
        refused = _failed(e.status, e.message)
        if e.retry_after is not None:
            refused.with_header("Retry-After", str(e.retry_after))
        return refused
    return json_reply(200, result)


_CHANGE = (("config", ANY, True), ("revision", STR, True))
_MEMBER_LIMITS = (
    "Shared accounts support paper topics with server-managed sources "
    "(up to 200 collected and 50 reviewed papers)."
)


def topics_route(
    console: Console, data: Path, req: Request, id: str | None, restricted: bool
) -> Reply:
    """`/api/topics` (id None) and `/api/topics/<id>` of workspace `data`.

    `restricted` (a shared-mode member) limits saved documents to what
    `topics.valid_member_topic` allows.
    """
    if (refused := console.access.rejects(req)) is not None:
        return refused
    try:
        if req.method == "GET":
            if id is None:
                view: Any = {"topics": topics.list_topics(data), "csrf_token": console.access.token}
            else:
                view = topics.get(data, id)
        else:
            allowed = (req.method == "POST" and id is None) or (
                req.method in ("PUT", "DELETE") and id is not None
            )
            if not allowed:
                return error(405, "Use GET/POST for the topic list, or GET/PUT/DELETE for one topic")
            change = fields(request_json(req), _CHANGE)
            if change is None:
                return error(400, "Expected config and revision fields")
            config = change["config"]
            if restricted and config is not None and not topics.valid_member_topic(config):
                return error(400, _MEMBER_LIMITS)
            if id is None:
                if config is None:
                    return error(400, "config is required")
                view = topics.create(data, config)
            else:
                revision = change["revision"]
                if not revision:
                    return error(400, "revision is required; reload the topic first")
                if req.method == "DELETE":
                    topics.delete(data, id, revision)
                    view = {"deleted": id}
                else:
                    if config is None:
                        return error(400, "config is required")
                    view = topics.update(data, id, config, revision)
    except Respond:
        raise
    except Exception as e:
        status = topics.status(e)
        if status == 500:
            log.error("topic settings: %s", describe(e))
            return error(500, "Could not read or save topic settings")
        return error(status, describe(e))
    return json_reply(201 if req.method == "POST" else 200, view)
