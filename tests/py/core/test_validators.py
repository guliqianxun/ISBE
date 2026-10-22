import pytest

from isbe.core import validators
from isbe.core.validators import Invalid


def test_provider_defaults() -> None:
    assert validators.provider_defaults("anthropic") == (
        "https://api.anthropic.com",
        "claude-haiku-5-5",
        "claude-sonnet-5-5",
    )
    assert validators.provider_defaults("deepseek") == (
        "https://api.deepseek.com",
        "deepseek-chat",
        "deepseek-chat",
    )
    assert validators.provider_defaults("openai") == ("https://api.openai.com/v1", "gpt-5-mini", "gpt-5")
    for name in ["OpenAI", "other", ""]:
        with pytest.raises(Invalid, match="must be anthropic, deepseek or openai"):
            validators.provider_defaults(name)


def test_url_validation() -> None:
    assert validators.validate_url("HTTPS://Example.COM:443/news") == "https://example.com/news"
    for value in [
        "",
        "file:///tmp/a",
        "javascript:alert(1)",
        "https://user:pass@example.com/",
        "https://@example.com/",
        "https://example.com/#part",
        "https://example.com/a b",
        "http:example.com",
        "http://[bad]/",
        "https://example.com\\other/",
        "https://example.com/" + "a" * 2048,
        "https://example.com/　",
    ]:
        with pytest.raises(Invalid):
            validators.validate_url(value)
    assert validators.validate_url("http://127.1/a") == "http://127.0.0.1/a"
    assert validators.validate_url("http://例子.测试/a?q=1") == "http://xn--fsqu00a.xn--0zwm56d/a?q=1"


def test_recipients_validate_deduplicate_and_allow_empty_defaults() -> None:
    assert validators.recipients("One@example.com; one@EXAMPLE.com, two@example.org\n", False) == [
        "One@example.com",
        "two@example.org",
    ]
    assert validators.recipients("", True) == []
    for value in [
        "",
        "bad",
        "a@b",
        "a@b..com",
        "a@-b.com",
        ".a@b.com",
        "a..b@x.com",
        "Name <a@b.com>",
        "a@b.com\0",
    ]:
        with pytest.raises(Invalid):
            validators.recipients(value, False)
    with pytest.raises(Invalid, match="At most 20"):
        validators.recipients(",".join(f"a{n}@example.com" for n in range(21)), False)


def test_mail_settings_validate() -> None:
    for name, value in [
        ("SMTP_PORT", "0"),
        ("SMTP_PORT", "65536"),
        ("SMTP_HOST", "smtp://host"),
        ("SMTP_HOST", "host:465"),
        ("SMTP_HOST", ""),
        ("SMTP_SECURITY", "none"),
        ("SMTP_PASSWORD", "secret\r\nheader"),
        ("EMAIL_TRANSPORT", "unsupported"),
        ("SMTP_FROM", "not-an-address"),
        ("SMTP_FROM_NAME", "x" * 201),
        ("ZEPTOMAIL_API_URL", "http://api.example.com/email"),
        ("ZEPTOMAIL_API_URL", "https://user:secret@example.com/email"),
        ("ZEPTOMAIL_API_URL", "https://example.com/email?token=secret"),
        ("ZEPTOMAIL_API_URL", "https://example.com/email?"),
        ("ZEPTOMAIL_API_URL", "https://example.com/email#secret"),
        ("ZEPTOMAIL_TOKEN", ""),
        ("ZEPTOMAIL_TOKEN", "Zoho-EncZapiKey"),
    ]:
        with pytest.raises(Invalid) as caught:
            validators.validate_mail_setting(name, value)
        assert "secret" not in caught.value.message
    for name, value in [
        ("SMTP_PORT", "587"),
        ("SMTP_PORT", "+587"),
        ("SMTP_HOST", "smtp.example.com"),
        ("SMTP_HOST", "::1"),
        ("SMTP_HOST", "192.0.2.1"),
        ("SMTP_SECURITY", "starttls"),
        ("ZEPTOMAIL_API_URL", "https://api.zeptomail.com/v1.1/email"),
        ("ZEPTOMAIL_TOKEN", "Zoho-enczapikey dummy"),
        ("ZEPTOMAIL_TOKEN", "Zoho-enczapiKey"),  # Kelvin sign is not ASCII K
    ]:
        validators.validate_mail_setting(name, value)
    assert validators.parse_ip("fe80::1%eth0") is None
    assert validators.parse_ip("127.1") is None
    assert validators.parse_ip("010.0.0.1") is None


def test_http_uri_follows_the_http_crate_grammar() -> None:
    uri = validators.http_uri("HTTP://user@[::1]:8000/v1?x=1#frag")
    assert uri is not None
    assert (uri.scheme, uri.authority, uri.host, uri.query) == ("http", "user@[::1]:8000", "[::1]", "x=1")
    assert validators.http_uri("https://example.com").host == "example.com"  # type: ignore[union-attr]
    for value in [
        "",
        "/path",
        "localhost:8000",
        "http:example.com",
        "file:///tmp/model",
        "http://a:1:2/",
        "http://host%/",
        "http://[::1/",
        "http://user@/",
        "http://exa mple.com/",
        "http://例子.com/",
        "http://example.com/a b",
        "http://example.com/a<b",
    ]:
        assert validators.http_uri(value) is None, value
    assert validators.http_uri("http://example.com/a{b}|c") is not None
    assert validators.http_uri("http://example.com/路径?q=值") is not None


def test_trim_and_numbers() -> None:
    assert validators.trim("　 x  ") == "x"
    assert validators.trim("\x1cx") == "\x1cx", "U+001C is not White_Space in Rust"
    assert validators.parse_uint("+5", 10) == 5
    for text in ["", "+", "-1", "1_0", "١", " 1", "11"]:
        assert validators.parse_uint(text, 10) is None, text
