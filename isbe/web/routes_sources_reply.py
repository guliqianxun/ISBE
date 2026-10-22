"""Answers and request parts shared by the `/api/sources*` routes: error replies,
offset parsing and the id segments of their paths."""

import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from isbe import topics
from isbe.core import locks, settings
from isbe.core.errors import HttpError
from isbe.sources import registry

from .wire import Reply, Request, Respond, error, json_reply

log = logging.getLogger(__name__)

I64_MAX = (1 << 63) - 1
# ASCII digits with an optional leading `+`.
_USIZE = re.compile(r"\+?[0-9]+")


def text(e: BaseException) -> str:
    """The message shown for an error of the routes below: the outermost one."""
    if isinstance(e, OSError) and not isinstance(e, locks.LockBusy) and e.errno is not None:
        # The OS error text and number, without the path Python adds.
        return f"{os.strerror(e.errno)} (os error {e.errno})"
    return str(e) or type(e).__name__


def failure(e: Exception) -> Reply:
    """The reply for a failed request: the error's own status (or 500) and its message."""
    status = registry.status(e)
    message = text(e)
    if status >= 500:
        log.warning("source request failed: %s", message)
    return json_reply(status, {"error": message})


def respond(produce: Callable[[], Any]) -> Reply:
    """200 with the value `produce` returns, else `failure`."""
    try:
        value = produce()
    except Respond:
        raise
    except Exception as e:
        return failure(e)
    return json_reply(200, value)


def task_settings(data: Path) -> settings.Values:
    """`settings.for_task(data)`, whose errors answer 500, not 400."""
    try:
        return settings.for_task(data)
    except HttpError as e:
        raise RuntimeError(e.message) from e


def offset(req: Request, message: str) -> int:
    """`?offset=` as a non-negative integer up to 2**63 - 1, optionally with a leading `+` (absent: 0); `Respond` 400 with `message` otherwise."""
    raw = req.args.get("offset")
    if raw is None:
        return 0
    if not _USIZE.fullmatch(raw) or int(raw) > I64_MAX:
        raise Respond(error(400, message))
    return int(raw)


def segment(path: str, prefix: str, suffix: str = "") -> str:
    """The part of `path` between `prefix` and `suffix`, or "" if either is missing."""
    if not path.startswith(prefix):
        return ""
    rest = path[len(prefix) :]
    if suffix:
        if not rest.endswith(suffix):
            return ""
        rest = rest[: -len(suffix)]
    return rest


def valid_id(id: str) -> bool:
    """Whether `id` is a valid ID (`topics.check_id`)."""
    try:
        topics.check_id(id)
    except topics.TopicError:
        return False
    return True
