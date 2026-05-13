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
