"""SMTP delivery over the standard library.

The dialogue: greeting, `EHLO [127.0.0.1]`, then for `starttls`
a required `STARTTLS` (never a downgrade: without it nothing else is sent)
and a second EHLO, `AUTH PLAIN` (else `AUTH LOGIN`) when credentials are
set, `MAIL FROM`, one `RCPT TO` per recipient, `DATA`, `QUIT`. `tls` is
implicit TLS from the first byte. Certificates are verified against the
host name (or address) with the same roots as `core.http`. One connection,
one submission, no retries; `timeout` bounds every connect, read and write.
Credentials and server replies never appear in results or logs.
"""

import base64
import binascii
import email.errors
import email.policy
import email.utils
import logging
import smtplib
import ssl
from datetime import UTC, datetime
from email.headerregistry import Address
from email.message import EmailMessage
from typing import Any, cast

import httpx

from .compose import MAX_MESSAGE
from .config import Config, MailError, SmtpConfig
from .outcome import UNKNOWN_SMTP, outcome

log = logging.getLogger(__name__)

# The EHLO name is an address literal, so no host name is leaked.
EHLO_NAME = "[127.0.0.1]"
# MIME text parts as quoted-printable or base64, so the message is 7-bit
# and needs no 8BITMIME.
_POLICY = email.policy.SMTP.clone(cte_type="7bit")
_MAX_CHALLENGES = 10

ACCEPTED = "The SMTP server accepted this email. Inbox delivery is not yet confirmed."
REJECTED = (
    "The SMTP server rejected the email. Check authentication, sender permissions and server "
    "logs. No automatic retry was made."
)
TLS_FAILED = "SMTP TLS negotiation failed. Check the hostname, certificate and TLS mode."


class _Negative(Exception):
    """A reply outside 2xx/3xx (transient for 4xx, permanent for 5xx)."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class _Tls(Exception):
    """TLS could not be established: handshake, certificate, or no STARTTLS offered."""


def _part(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    return value if isinstance(value, str) else ""


def _address(value: Any, error: str, name: str = "") -> Address:
    try:
        if not isinstance(value, str):
            raise ValueError(value)
        return Address(display_name=name, addr_spec=value)
    except (ValueError, IndexError, email.errors.MessageError):
        raise MailError(error) from None


def _recipient(entry: Any) -> Address:
    mailbox = entry.get("email_address") if isinstance(entry, dict) else None
    address = mailbox.get("address") if isinstance(mailbox, dict) else None
    return _address(address, "Invalid recipient")


def _image(image: Any) -> tuple[bytes, str, str, str]:
    if not isinstance(image, dict):
        raise MailError("Invalid image content")
    try:
        content = base64.b64decode(_part(image, "content"), validate=True)
    except binascii.Error:
        raise MailError("Invalid image content") from None
    maintype, slash, subtype = _part(image, "mime_type").partition("/")
    if not (maintype and slash and subtype) or not (maintype + subtype).isascii():
        raise MailError("Invalid image type")
    cid = image.get("cid")
    if not isinstance(cid, str) or not cid:
        raise MailError("Invalid image CID")
    return content, maintype, subtype, cid


def message(config: Config, body: dict[str, Any]) -> tuple[bytes, list[str]]:
    """The MIME message — alternative(text/plain, related(text/html, inline images)) —
    and its envelope recipients."""
    sender = _address(config.sender, "Invalid sender address", config.name)
    entries = body.get("to")
    if not isinstance(entries, list):
        raise MailError("Missing recipients")
    to = [_recipient(entry) for entry in entries]
    images = body.get("inline_images")
    if not isinstance(images, list):
        raise MailError("Invalid inline images")
    parts = [_image(image) for image in images]
    try:
        mail = EmailMessage(policy=_POLICY)
        mail["From"] = sender
        mail["To"] = to
        mail["Subject"] = _part(body, "subject")
        # A Date header is added, but no Message-ID (the provider assigns one).
        mail["Date"] = email.utils.format_datetime(datetime.now(UTC))
        mail.set_content(_part(body, "textbody"))
        mail.add_alternative(_part(body, "htmlbody"), subtype="html")
        html = cast(EmailMessage, mail.get_payload(1))
        for content, maintype, subtype, cid in parts:
            html.add_related(content, maintype, subtype, cid=f"<{cid}>", disposition="inline")
        for part in mail.walk():
            if part is not mail:
                del part["MIME-Version"]  # EmailMessage repeats it in every part; only the message itself needs it
        raw = mail.as_bytes(policy=_POLICY)
    except (ValueError, TypeError, UnicodeError):
        raise MailError("Could not build email") from None
    if len(raw) > MAX_MESSAGE:
        raise MailError("Email exceeds the 10 MiB message limit")
    return raw, [address.addr_spec for address in to]


def tls_context() -> ssl.SSLContext:
    """Certificate and host name verification with `core.http`'s roots; TLS 1.2 or newer."""
    context = httpx.create_ssl_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def deliver(
    config: Config,
    smtp: SmtpConfig,
    body: dict[str, Any],
    timeout: float,
    *,
    context: ssl.SSLContext | None = None,
) -> dict[str, Any]:
    """Submit `body` once. `context` replaces the verifying TLS context (tests)."""
    try:
        raw, recipients = message(config, body)
    except MailError as error:
        return outcome("failed", error.message)
    try:
        _submit(smtp, config.sender, recipients, raw, timeout, context or tls_context())
        result = outcome("accepted", ACCEPTED)
    except (_Negative, smtplib.SMTPResponseException) as error:
        code = error.code if isinstance(error, _Negative) else error.smtp_code
        result = outcome("failed", REJECTED) if 400 <= code < 600 else outcome("unknown", UNKNOWN_SMTP)
    except _Tls:
        result = outcome("failed", TLS_FAILED)
    except (OSError, smtplib.SMTPException, ValueError, UnicodeError):
        result = outcome("unknown", UNKNOWN_SMTP)
    log.info("mail transport=smtp state=%s", result["state"])
    return result


def _positive(code: int) -> None:
    if not 200 <= code < 400:
        raise _Negative(code)


def _submit(
    config: SmtpConfig,
    sender: str,
    recipients: list[str],
    raw: bytes,
    timeout: float,
    context: ssl.SSLContext,
) -> None:
    implicit = config.security == "tls"
    client = (
        smtplib.SMTP_SSL(local_hostname=EHLO_NAME, timeout=timeout, context=context)
        if implicit
        else smtplib.SMTP(local_hostname=EHLO_NAME, timeout=timeout)
    )
    client.ehlo_msg = "EHLO"
    # QUIT only while the dialogue is between replies.
    polite = False
    try:
        try:
            code, _ = client.connect(config.host, config.port)
        except ssl.SSLError as error:
            raise _Tls from error
        polite = True
        _positive(code)
        _positive(client.ehlo()[0])
        if not implicit:
            if not client.has_extn("starttls"):
                raise _Tls
            try:
                client.starttls(context=context)
            except ssl.SSLError as error:
                polite = False
                raise _Tls from error
            _positive(client.ehlo()[0])
        if config.user:
            _authenticate(client, config.user, config.password)
        _positive(client.docmd("MAIL", f"FROM:<{sender}>")[0])
        for address in recipients:
            _positive(client.docmd("RCPT", f"TO:<{address}>")[0])
        _positive(client.docmd("DATA")[0])
        data = smtplib.quotedata(raw.decode("ascii")).encode("ascii")
        client.send(data + (b"" if data.endswith(b"\r\n") else b"\r\n") + b".\r\n")
        _positive(client.getreply()[0])
    except (OSError, smtplib.SMTPServerDisconnected):
        polite = False
        raise
    finally:
        if polite:
            try:
                client.docmd("QUIT")
            except (OSError, smtplib.SMTPException):
                pass
        client.close()


def _authenticate(client: smtplib.SMTP, user: str, password: str) -> None:
    """PLAIN with an initial response, else LOGIN answering its prompts."""
    offered = client.esmtp_features.get("auth", "").upper().split()
    if "PLAIN" in offered:
        token = base64.b64encode(f"\0{user}\0{password}".encode()).decode("ascii")
        code, _ = client.docmd("AUTH", f"PLAIN {token}")
    elif "LOGIN" in offered:
        code, challenge = client.docmd("AUTH", "LOGIN")
        for _ in range(_MAX_CHALLENGES):
            if code != 334:
                break
            prompt = base64.b64decode(challenge.split(b" ", 1)[0]).decode("utf-8", "replace").lower()
            if "user name" in prompt or "username" in prompt:
                answer = user
            elif "password" in prompt:
                answer = password
            else:
                raise smtplib.SMTPException("unrecognized challenge")
            code, challenge = client.docmd(base64.b64encode(answer.encode()).decode("ascii"))
        else:
            raise smtplib.SMTPException("too many challenges")
    else:
        # No compatible authentication mechanism was found: a client error.
        raise smtplib.SMTPException("no compatible authentication mechanism")
    _positive(code)
