"""Mail settings and readiness."""

from pathlib import Path

import pytest

from isbe import mail
from isbe.core import settings
from isbe.core.validators import Invalid, recipients


def zepto(**overrides: str) -> mail.Config:
    values = {
        "ZEPTOMAIL_API_URL": "https://api.zeptomail.com/v1.1/email",
        "ZEPTOMAIL_TOKEN": "dummy-secret",
        "SMTP_FROM": "sender@example.com",
        "SMTP_FROM_NAME": "研究报告",
    } | overrides
    return mail.load(values.get)


def test_recipients_validate_deduplicate_and_allow_empty_defaults() -> None:
    assert recipients("One@example.com; one@EXAMPLE.com, two@example.org\n", False) == [
        "One@example.com",
        "two@example.org",
    ]
    assert recipients("", True) == []
    for value in ["", "bad", "a@b", "a@b..com", "a@-b.com", ".a@b.com", "a..b@x.com", "Name <a@b.com>", "a@b.com\0"]:
        with pytest.raises(Invalid):
            recipients(value, False)
    with pytest.raises(Invalid):
        recipients(",".join(f"a{n}@example.com" for n in range(21)), False)


def test_smtp_configuration_uses_tls_defaults_and_requires_matching_credentials() -> None:
    def load(security: str, user: str, password: str) -> mail.Config:
        values = {
            "EMAIL_TRANSPORT": "smtp",
            "SMTP_HOST": "smtp.example.com",
            "SMTP_FROM": "sender@example.com",
            "SMTP_SECURITY": security,
            "SMTP_USER": user,
            "SMTP_PASSWORD": password,
        }
        return mail.load(values.get)

    config = load("", "", "")
    assert config.smtp is not None and config.smtp.port == 465 and config.smtp.security == "tls"
    starttls = load("starttls", "user", "password").smtp
    assert starttls is not None and starttls.port == 587
    for security, user, password in [("tls", "user", ""), ("tls", "", "password"), ("none", "user", "password")]:
        with pytest.raises(mail.MailError) as error:
            load(security, user, password)
        assert error.value.status == 422
    with pytest.raises(mail.MailError, match="SMTP_SECURITY must be tls or starttls"):
        load("none", "", "")
    with pytest.raises(mail.MailError, match="SMTP_PORT"):
        mail.load({"SMTP_HOST": "h.example", "SMTP_FROM": "a@b.co", "SMTP_PORT": "0"}.get)
    with pytest.raises(mail.MailError, match="SMTP_HOST"):
        mail.load({"SMTP_HOST": "smtp://h.example", "SMTP_FROM": "a@b.co"}.get)


def test_saved_configuration_reloads_without_restart(data: Path) -> None:
    settings.save(
        data,
        {
            "ZEPTOMAIL_API_URL": "https://api.zeptomail.com/v1.1/email",
            "ZEPTOMAIL_TOKEN": "saved-mail-token",
            "SMTP_FROM": "first@example.com",
        },
    )
    assert mail.from_data(data).sender == "first@example.com"
    settings.save(data, {"SMTP_FROM": "second@example.com"})
    assert mail.from_data(data).sender == "second@example.com"
    # Another process's save is seen too: the file is read on every use.
    (data / "settings.json").write_text(
        '{"ZEPTOMAIL_API_URL":"https://api.zeptomail.com/v1.1/email",'
        '"ZEPTOMAIL_TOKEN":"saved-mail-token","SMTP_FROM":"third@example.com"}'
    )
    assert mail.status(data) == {"configured": True, "sender": "third@example.com"}
    settings.save(data, {"ZEPTOMAIL_TOKEN": None})
    assert mail.status(data) == {"configured": False, "message": "Configure a valid ZEPTOMAIL_TOKEN"}


def test_environment_wins_and_status_never_shows_secrets(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.save(data, {"ZEPTOMAIL_API_URL": "https://api.zeptomail.com/v1.1/email",
                         "ZEPTOMAIL_TOKEN": "saved-mail-token", "SMTP_FROM": "file@example.com"})
    monkeypatch.setenv("SMTP_FROM", "env@example.com")
    monkeypatch.setenv("SMTP_FROM_NAME", "   ")  # blank environment values do not override
    assert mail.status(data) == {"configured": True, "sender": "env@example.com"}
    assert "saved-mail-token" not in str(mail.status(data))
    (data / "settings.json").write_text("not json")
    assert mail.status(data) == {"configured": False, "message": "Could not read email settings"}


def test_missing_configuration_names_what_to_set(data: Path) -> None:
    assert mail.status(data) == {"configured": False, "message": "Configure a valid SMTP_FROM address"}
    settings.save(data, {"SMTP_FROM": "a@example.com"})
    assert mail.status(data)["message"].startswith("Configure SMTP_HOST")
    settings.save(data, {"SMTP_HOST": "smtp.example.com", "SMTP_USER": "user"})
    assert mail.status(data)["message"].startswith("Configure both SMTP_USER and SMTP_PASSWORD")


def test_config_requires_verified_server_values_and_never_repeats_token_prefix() -> None:
    with pytest.raises(mail.MailError):
        mail.load(lambda _: None)
    base = zepto()
    assert base.authorization == "Zoho-enczapikey dummy-secret"
    assert zepto(ZEPTOMAIL_TOKEN="zoho-enczapikey dummy-secret").authorization == "zoho-enczapikey dummy-secret"
    for endpoint in [
        "http://api.example.com/email",
        "https://user:secret@example.com/email",
        "https://example.com/email?token=secret",
        "https://example.com/email#secret",
    ]:
        with pytest.raises(mail.MailError) as error:
            zepto(ZEPTOMAIL_API_URL=endpoint, ZEPTOMAIL_TOKEN="secret")
        assert "secret" not in error.value.message
    with pytest.raises(mail.MailError):
        zepto(ZEPTOMAIL_TOKEN="bad\r\nsecret")
    with pytest.raises(mail.MailError, match="ZEPTOMAIL_TOKEN"):
        zepto(ZEPTOMAIL_TOKEN="Zoho-enczapikey")
    # Without EMAIL_TRANSPORT, an API URL or token selects the API (existing deployments).
    assert base.smtp is None and base.endpoint == "https://api.zeptomail.com/v1.1/email"
    with pytest.raises(mail.MailError, match="SMTP_HOST"):
        zepto(EMAIL_TRANSPORT="smtp")
    assert "dummy-secret" not in repr(base)


def test_default_recipients_come_from_the_saved_settings(data: Path) -> None:
    assert mail.default_recipients(data) == ""
    settings.save(data, {"ISBE_EMAIL_DEFAULT_RECIPIENTS": "a@example.com;b@example.com"})
    assert mail.default_recipients(data) == "a@example.com, b@example.com"
