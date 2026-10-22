"""Mail settings and the readiness view the console shows.

The settings are read from `<root>/settings.json` on every use, with
non-empty environment variables winning, so a saved change applies to the
next email without a restart. Validation is `core.validators`'s
`validate_mail_setting`; its messages never contain the checked value, so
they can be shown as they are.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from isbe.core import settings, validators
from isbe.core.errors import HttpError
from isbe.core.validators import Invalid, ascii_lower, trim

# ZeptoMail's token prefix; a token pasted with it is not prefixed again.
_PREFIX = "zoho-enczapikey "


class MailError(HttpError):
    """An email that was not sent; the message is safe to show (422, or 429 when rate limited).

    `retry_after` is the number of seconds after which a rate-limited send may
    succeed (for a `Retry-After` header), else None.
    """

    def __init__(self, message: str, status: int = 422, retry_after: int | None = None) -> None:
        super().__init__(status, message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    security: str  # "tls" (implicit TLS) or "starttls" (required STARTTLS)
    user: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class Config:
    """The active transport: `smtp` set for SMTP, else the ZeptoMail API endpoint."""

    sender: str
    name: str
    smtp: SmtpConfig | None = None
    endpoint: str = ""
    authorization: str = field(default="", repr=False)


def _check(name: str, value: str) -> None:
    try:
        validators.validate_mail_setting(name, value)
    except Invalid as error:
        raise MailError(error.message) from None


def load(env: Callable[[str], str | None]) -> Config:
    """The configuration from `env(name)`; raises `MailError`."""

    def get(key: str) -> str:
        return trim(env(key) or "")

    transport = get("EMAIL_TRANSPORT")
    if not transport:
        # Preserve existing API deployments that omitted the transport selector.
        api = get("ZEPTOMAIL_API_URL") or get("ZEPTOMAIL_TOKEN")
        transport = "zeptomail_api" if api else "smtp"
    _check("EMAIL_TRANSPORT", transport)
    sender = get("SMTP_FROM")
    _check("SMTP_FROM", sender)
    name = get("SMTP_FROM_NAME")
    _check("SMTP_FROM_NAME", name)
    if transport == "smtp":
        host = get("SMTP_HOST")
        _check("SMTP_HOST", host)
        security = get("SMTP_SECURITY") or "tls"
        _check("SMTP_SECURITY", security)
        port = get("SMTP_PORT") or ("465" if security == "tls" else "587")
        _check("SMTP_PORT", port)
        user = get("SMTP_USER")
        password = get("SMTP_PASSWORD")
        _check("SMTP_USER", user)
        _check("SMTP_PASSWORD", password)
        if bool(user) != bool(password):
            raise MailError(
                "Configure both SMTP_USER and SMTP_PASSWORD, or leave both empty for an "
                "unauthenticated relay"
            )
        number = validators.parse_uint(port, 65535)
        assert number is not None  # checked above
        return Config(sender, name, smtp=SmtpConfig(host, number, security, user, password))
    endpoint = get("ZEPTOMAIL_API_URL")
    _check("ZEPTOMAIL_API_URL", endpoint)
    token = get("ZEPTOMAIL_TOKEN")
    _check("ZEPTOMAIL_TOKEN", token)
    authorization = token if ascii_lower(token).startswith(_PREFIX) else f"Zoho-enczapikey {token}"
    return Config(sender, name, endpoint=endpoint, authorization=authorization)


def from_data(settings_data: Path) -> Config:
    """The configuration saved under the service root, environment first."""
    try:
        saved = settings.read(settings_data)
    except (Invalid, OSError):
        raise MailError("Could not read email settings") from None
    return load(lambda key: settings.environment(key) or saved.get(key))


def status(settings_data: Path) -> dict[str, Any]:
    """`{"configured": true, "sender": ...}` or `{"configured": false, "message": ...}`."""
    try:
        config = from_data(settings_data)
    except MailError as error:
        return {"configured": False, "message": error.message}
    return {"configured": True, "sender": config.sender}


def default_recipients(settings_data: Path) -> str:
    """`ISBE_EMAIL_DEFAULT_RECIPIENTS` as `/api/mail` shows it (an admin's view; may raise)."""
    value = settings.snapshot(settings_data)["values"]["ISBE_EMAIL_DEFAULT_RECIPIENTS"]
    return value if isinstance(value, str) else ""
