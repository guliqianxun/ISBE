from pathlib import Path
from unittest.mock import MagicMock, patch

from isbe.notify import is_configured, send_digest_notification


def _clear_env(monkeypatch):
    for k in (
        "ISBE_SMTP_HOST",
        "ISBE_SMTP_PORT",
        "ISBE_SMTP_USER",
        "ISBE_SMTP_PASS",
        "ISBE_SMTP_FROM",
        "ISBE_SMTP_TO",
    ):
        monkeypatch.delenv(k, raising=False)


def _set_env(monkeypatch, **overrides):
    _clear_env(monkeypatch)
    defaults = {
        "ISBE_SMTP_HOST": "smtp.example.com",
        "ISBE_SMTP_FROM": "isbe@example.com",
        "ISBE_SMTP_TO": "me@example.com",
        "ISBE_SMTP_PORT": "587",
    }
    for k, v in {**defaults, **overrides}.items():
        monkeypatch.setenv(k, v)


def test_is_configured_false_when_env_missing(monkeypatch):
    _clear_env(monkeypatch)
    assert is_configured() is False


def test_is_configured_true_when_all_set(monkeypatch):
    _set_env(monkeypatch)
    assert is_configured() is True


def test_send_returns_false_when_not_configured(monkeypatch):
    _clear_env(monkeypatch)
    ok = send_digest_notification(
        topic_label="nowcasting",
        period_label="2026-W19",
        artifact_path=Path("nowhere.md"),
        excerpt="…",
    )
    assert ok is False


def test_send_starttls_path(monkeypatch):
    _set_env(monkeypatch, ISBE_SMTP_USER="u", ISBE_SMTP_PASS="p")
    fake_srv = MagicMock()
    fake_smtp = MagicMock()
    fake_smtp.__enter__ = MagicMock(return_value=fake_srv)
    fake_smtp.__exit__ = MagicMock(return_value=False)
    with patch("isbe.notify.smtplib.SMTP", return_value=fake_smtp) as smtp_cls:
        ok = send_digest_notification(
            topic_label="nowcasting",
            period_label="2026-W19",
            artifact_path=Path("artifacts/nowcasting/2026-W19/latest.md"),
            excerpt="hello",
        )
    assert ok is True
    smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=20)
    fake_srv.starttls.assert_called_once()
    fake_srv.login.assert_called_once_with("u", "p")
    fake_srv.send_message.assert_called_once()


def test_send_ssl_path_on_465(monkeypatch):
    _set_env(monkeypatch, ISBE_SMTP_PORT="465")
    fake_srv = MagicMock()
    fake_smtp = MagicMock()
    fake_smtp.__enter__ = MagicMock(return_value=fake_srv)
    fake_smtp.__exit__ = MagicMock(return_value=False)
    with patch("isbe.notify.smtplib.SMTP_SSL", return_value=fake_smtp) as smtp_cls:
        ok = send_digest_notification(
            topic_label="x",
            period_label="2026-05-12",
            artifact_path=None,
            excerpt="",
        )
    assert ok is True
    smtp_cls.assert_called_once_with("smtp.example.com", 465, timeout=20)
    fake_srv.send_message.assert_called_once()


def test_send_returns_false_on_transport_error(monkeypatch):
    _set_env(monkeypatch)
    with patch("isbe.notify.smtplib.SMTP", side_effect=OSError("boom")):
        ok = send_digest_notification(
            topic_label="x",
            period_label="2026-W19",
            artifact_path=None,
            excerpt="",
        )
    assert ok is False


def test_starttls_rejected_refuses_plaintext_by_default(monkeypatch, capsys):
    """Security: when STARTTLS fails on port 587, do NOT silently send
    credentials in cleartext. Return False and warn to stderr."""
    _set_env(monkeypatch, ISBE_SMTP_USER="u", ISBE_SMTP_PASS="p")
    fake_srv = MagicMock()
    fake_srv.starttls.side_effect = __import__("smtplib").SMTPException("no STARTTLS")
    fake_smtp = MagicMock()
    fake_smtp.__enter__ = MagicMock(return_value=fake_srv)
    fake_smtp.__exit__ = MagicMock(return_value=False)
    with patch("isbe.notify.smtplib.SMTP", return_value=fake_smtp):
        ok = send_digest_notification(
            topic_label="x",
            period_label="2026-W19",
            artifact_path=None,
            excerpt="",
        )
    assert ok is False
    fake_srv.send_message.assert_not_called()  # never sent in plaintext
    fake_srv.login.assert_not_called()         # creds never exposed
    err = capsys.readouterr().err
    assert "STARTTLS rejected" in err


def test_starttls_rejected_with_explicit_opt_in_sends_plaintext(monkeypatch, capsys):
    """If the operator explicitly opts in via ISBE_SMTP_ALLOW_PLAINTEXT=1
    (e.g. trusted localhost relay), plaintext is allowed but warned about."""
    _set_env(monkeypatch, ISBE_SMTP_ALLOW_PLAINTEXT="1")
    fake_srv = MagicMock()
    fake_srv.starttls.side_effect = __import__("smtplib").SMTPException("no STARTTLS")
    fake_smtp = MagicMock()
    fake_smtp.__enter__ = MagicMock(return_value=fake_srv)
    fake_smtp.__exit__ = MagicMock(return_value=False)
    with patch("isbe.notify.smtplib.SMTP", return_value=fake_smtp):
        ok = send_digest_notification(
            topic_label="x",
            period_label="2026-W19",
            artifact_path=None,
            excerpt="",
        )
    assert ok is True
    fake_srv.send_message.assert_called_once()
    err = capsys.readouterr().err
    assert "PLAINTEXT" in err  # the warn fires even on opt-in


def test_failure_message_goes_to_stderr(monkeypatch, capsys):
    """Surface failures on stderr — stdout is reserved for ordinary flow logs."""
    _set_env(monkeypatch)
    with patch("isbe.notify.smtplib.SMTP", side_effect=OSError("boom")):
        send_digest_notification(
            topic_label="x",
            period_label="2026-W19",
            artifact_path=None,
            excerpt="",
        )
    captured = capsys.readouterr()
    assert "boom" in captured.err
    assert "boom" not in captured.out
