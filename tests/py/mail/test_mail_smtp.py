"""SMTP delivery against the scripted local server."""

import base64
import email
import email.policy
import socket
from typing import Any

import pytest

from isbe import mail
from isbe.mail import compose, smtp
from isbe.mail.config import Config, SmtpConfig
from isbe.mail.outcome import UNKNOWN_SMTP

# Fixtures from conftest.py: `smtp_server` (factory of SmtpServer) and `certificate`.
type SmtpServer = Any
type Certificate = Any


def fixture(port: int, security: str = "starttls", user: str = "", password: str = "") -> tuple[Config, dict[str, Any]]:
    config = mail.load(
        {
            "EMAIL_TRANSPORT": "smtp",
            "SMTP_HOST": "localhost",
            "SMTP_PORT": str(port),
            "SMTP_SECURITY": security,
            "SMTP_FROM": "sender@example.com",
            "SMTP_FROM_NAME": "科研报告",
            "SMTP_USER": user,
            "SMTP_PASSWORD": password,
        }.get
    )
    image = {"cid": "test-image", "mime_type": "image/png", "content": base64.b64encode(b"image-bytes").decode()}
    body = compose.payload(
        config, ["reader@example.com"], "报告测试", "正文\n.dot line", '<p>正文<img src="cid:test-image"></p>', [image]
    )
    return config, body


def send(config: Config, body: dict[str, Any], context: Any, timeout: float = 5) -> dict[str, Any]:
    assert config.smtp is not None
    return smtp.deliver(config, config.smtp, body, timeout, context=context)


@pytest.mark.parametrize("reply", ["250 queued", "550 private-provider-text", None])
def test_smtp_mime_and_delivery_outcomes_use_one_submission_without_leaking_provider_text(
    smtp_server: type[SmtpServer], certificate: Certificate, reply: str | None
) -> None:
    server = smtp_server(tls=certificate.server(), data_reply=reply)
    config, body = fixture(server.port)
    result = send(config, body, certificate.client())
    expected = {"250 queued": "accepted", None: "unknown"}.get(reply, "failed")
    assert result["state"] == expected
    assert "private-provider-text" not in str(result)
    assert len(server.messages) == 1 and server.connections == 1
    wire = server.messages[0].decode("ascii")
    for header in [
        "multipart/alternative", "multipart/related", "text/plain", "text/html",
        "Content-ID: <test-image>", "Content-Disposition: inline", "reader@example.com",
    ]:
        assert header in wire, header
    # The dialogue: EHLO, STARTTLS, EHLO again over TLS, envelope, DATA, then QUIT.
    verbs = [c.split(" ", 1)[0].split(":", 1)[0].strip() for c in server.commands]
    assert verbs[:6] == ["EHLO", "STARTTLS", "EHLO", "MAIL", "RCPT", "DATA"]
    assert server.commands[0] == "EHLO [127.0.0.1]\r\n"
    assert server.commands[3] == "MAIL FROM:<sender@example.com>\r\n"
    assert server.commands[4] == "RCPT TO:<reader@example.com>\r\n"
    assert verbs[6:] == (["QUIT"] if reply else [])
    parsed = email.message_from_bytes(server.messages[0], policy=email.policy.default)
    assert parsed["Subject"] == "报告测试"
    assert parsed["From"].addresses[0].display_name == "科研报告"
    text = parsed.get_body(("plain",))
    html = parsed.get_body(("html",))
    assert text is not None and html is not None
    assert text.get_content().replace("\r\n", "\n") == "正文\n.dot line\n", "dot-stuffed on the wire"
    assert html.get_content().strip() == '<p>正文<img src="cid:test-image"></p>'
    image = next(p for p in parsed.walk() if p.get_content_type() == "image/png")
    assert image.get_content() == b"image-bytes"
    assert all(ord(c) < 128 for c in wire), "7-bit message: no 8BITMIME needed"


def test_required_starttls_never_downgrades_or_sends_credentials(
    smtp_server: type[SmtpServer], certificate: Certificate
) -> None:
    server = smtp_server(tls=certificate.server(), offer_starttls=False)
    config, body = fixture(server.port, user="private-user", password="private-password")
    result = send(config, body, certificate.client(), timeout=3)
    assert result["state"] != "accepted"
    # Nothing was submitted, so this is reported as a TLS failure (the earlier build said "unknown").
    assert result == {"state": "failed", "message": smtp.TLS_FAILED, "request_id": None}
    assert "private-password" not in str(result)
    assert not any(c.startswith(("AUTH", "MAIL")) for c in server.commands)
    assert server.credentials == [] and server.messages == []


def test_implicit_tls_authenticates_with_plain_over_tls_only(
    smtp_server: type[SmtpServer], certificate: Certificate
) -> None:
    server = smtp_server(mode="tls", tls=certificate.server())
    config, body = fixture(server.port, security="tls", user="user@example.com", password="密码 pass")
    result = send(config, body, certificate.client())
    assert result == {"state": "accepted", "message": smtp.ACCEPTED, "request_id": None}
    assert server.credentials == [("user@example.com", "密码 pass")]
    assert server.encrypted == [True]
    assert server.commands[1].startswith("AUTH PLAIN ")


def test_login_is_used_when_plain_is_not_offered(smtp_server: type[SmtpServer], certificate: Certificate) -> None:
    server = smtp_server(tls=certificate.server(), auth="LOGIN")
    config, body = fixture(server.port, user="user", password="secret")
    assert send(config, body, certificate.client())["state"] == "accepted"
    assert server.credentials == [("user", "secret")]
    assert server.commands[3] == "AUTH LOGIN\r\n"
    assert server.encrypted == [True]


def test_rejected_authentication_is_a_failure_without_submission(
    smtp_server: type[SmtpServer], certificate: Certificate
) -> None:
    server = smtp_server(tls=certificate.server(), replies={"AUTH-RESULT": "535 bad credentials private"})
    config, body = fixture(server.port, user="user", password="secret")
    result = send(config, body, certificate.client())
    assert result == {"state": "failed", "message": smtp.REJECTED, "request_id": None}
    assert server.messages == [] and "private" not in str(result)


def test_rejected_recipient_is_a_failure(smtp_server: type[SmtpServer], certificate: Certificate) -> None:
    server = smtp_server(tls=certificate.server(), replies={"RCPT": "450 mailbox busy"})
    config, body = fixture(server.port)
    assert send(config, body, certificate.client())["state"] == "failed"
    assert server.messages == [] and server.commands[-1] == "QUIT\r\n"


@pytest.mark.parametrize("mode", ["tls", "starttls"])
def test_untrusted_certificates_fail_tls_before_anything_is_sent(
    smtp_server: type[SmtpServer], certificate: Certificate, mode: str
) -> None:
    server = smtp_server(mode=mode, tls=certificate.server())
    config, body = fixture(server.port, security=mode, user="user", password="private-password")
    # The production context: the self-signed certificate is not trusted.
    result = send(config, body, None)
    assert result == {"state": "failed", "message": smtp.TLS_FAILED, "request_id": None}
    assert server.credentials == [] and server.messages == []


def test_certificate_must_match_the_configured_host(
    smtp_server: type[SmtpServer], certificate: Certificate
) -> None:
    server = smtp_server(mode="tls", tls=certificate.server())
    config, body = fixture(server.port, security="tls")
    # The certificate names localhost only, so the same server by address does not verify.
    by_address = Config(config.sender, config.name, smtp=SmtpConfig("127.0.0.1", server.port, "tls", "", ""))
    result = send(by_address, body, certificate.client())
    assert result == {"state": "failed", "message": smtp.TLS_FAILED, "request_id": None}
    assert server.messages == []


def test_silent_server_and_refused_connection_are_unknown(
    smtp_server: type[SmtpServer], certificate: Certificate
) -> None:
    server = smtp_server(tls=certificate.server(), greeting=None)
    config, body = fixture(server.port)
    result = send(config, body, certificate.client(), timeout=0.3)
    assert result == {"state": "unknown", "message": UNKNOWN_SMTP, "request_id": None}
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    config, body = fixture(closed)
    assert send(config, body, certificate.client(), timeout=2)["state"] == "unknown"


def test_message_size_and_parts_are_checked_before_connecting() -> None:
    config, body = fixture(25)
    assert config.smtp is not None
    big = dict(body, htmlbody="x" * compose.MAX_MESSAGE)
    result = smtp.deliver(config, config.smtp, big, 1)
    assert result == {"state": "failed", "message": "Email exceeds the 10 MiB message limit", "request_id": None}
    broken = dict(body, inline_images=[{"cid": "a", "mime_type": "image/png", "content": "**"}])
    assert smtp.deliver(config, config.smtp, broken, 1)["message"] == "Invalid image content"
    assert "password" not in repr(config) and "authorization" not in repr(config)
