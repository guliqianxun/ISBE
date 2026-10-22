"""Manual delivery: the entry points behind `POST /api/mail/report` and
`POST /api/mail/test`.

Everything that can be checked is checked before the transport is used, in
this order: recipients, the exact report and its figures, the settings,
the message size; then the sender's rate limit, then one bounded attempt.
A `MailError` means nothing was sent; otherwise the result is an outcome
(`outcome.py`), which the route answers with 200.
"""

import sqlite3
from pathlib import Path
from typing import Any

from isbe.core import validators
from isbe.core.validators import Invalid

from . import compose, smtp, zeptomail
from .config import Config, MailError, from_data
from .limit import Limiter

TIMEOUT = 20.0


def _recipients(value: str) -> list[str]:
    try:
        return validators.recipients(value, False)
    except Invalid as error:
        raise MailError(error.message) from None


def deliver(config: Config, body: dict[str, Any], timeout: float = TIMEOUT) -> dict[str, Any]:
    """Send `body` (a `compose.payload`) once through the configured transport."""
    if config.smtp is not None:
        return smtp.deliver(config, config.smtp, body, timeout)
    return zeptomail.deliver(config, body, timeout)


def _send(
    config: Config, body: dict[str, Any], limiter: Limiter | None, account: str, admin: bool
) -> dict[str, Any]:
    if limiter is not None:
        limiter.acquire(account, admin=admin)
    return deliver(config, body)


def report(
    conn: sqlite3.Connection,
    data: Path,
    settings_data: Path,
    topic: str,
    period: str,
    to: str,
    *,
    limiter: Limiter | None,
    account: str = "",
    admin: bool = False,
) -> dict[str, Any]:
    """Email the exact report `topic`/`period` of workspace `data` to `to`.

    `conn` is that workspace's database; `settings_data` is the service root
    whose settings hold the mail configuration. `limiter` counts the send
    against `account` (the signed-in user's id; `admin` for the higher
    limit); pass None only where no untrusted user can trigger a send.
    Raises `MailError` (422, or 429 when the limit is reached).
    """
    addresses = _recipients(to)
    text, html, images = compose.report_content(conn, data, topic, period)
    config = from_data(settings_data)
    body = compose.payload(config, addresses, compose.subject(topic, period), text, html, images)
    return _send(config, body, limiter, account, admin)


def test(
    settings_data: Path,
    to: str,
    *,
    limiter: Limiter | None,
    account: str = "",
    admin: bool = False,
) -> dict[str, Any]:
    """Send the configuration test email (administrators only, enforced by the route)."""
    addresses = _recipients(to)
    config = from_data(settings_data)
    text, html, images = compose.test_content()
    body = compose.payload(config, addresses, compose.TEST_SUBJECT, text, html, images)
    return _send(config, body, limiter, account, admin)
