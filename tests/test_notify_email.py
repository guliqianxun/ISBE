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


def test_body_contains_full_artifact_when_readable(monkeypatch, tmp_path):
    """When artifact_path points to a readable file (e.g. latest.md), the
    email body must carry its full contents — not just an excerpt — so the
    recipient can read the digest without server file access."""
    _set_env(monkeypatch)
    artifact = tmp_path / "latest.md"
    full_text = (
        "# Nowcasting Digest 2026-W19\n\n"
        "## Highlights\n\n"
        "- FlashAttention-4 lands in mainline torch\n"
        "- New SOTA on radar nowcasting from DeepMind\n\n"
        "## Detailed notes\n\nLorem ipsum dolor sit amet.\n"
    )
    artifact.write_text(full_text, encoding="utf-8")

    captured = {}
    fake_srv = MagicMock()

    def _capture_send(message):
        captured["msg"] = message

    fake_srv.send_message.side_effect = _capture_send
    fake_smtp = MagicMock()
    fake_smtp.__enter__ = MagicMock(return_value=fake_srv)
    fake_smtp.__exit__ = MagicMock(return_value=False)
    with patch("isbe.notify.smtplib.SMTP", return_value=fake_smtp):
        ok = send_digest_notification(
            topic_label="nowcasting",
            period_label="2026-W19",
            artifact_path=artifact,
            excerpt="short excerpt that should NOT be the body",
        )

    assert ok is True
    msg = captured["msg"]
    # Message is now multipart/alternative (text/plain + text/html).
    # Extract the plaintext part to verify artifact contents.
    if msg.is_multipart():
        plain_part = next(p for p in msg.walk() if p.get_content_type() == "text/plain")
        body = plain_part.get_content()
    else:
        body = msg.get_content()
    # Full artifact text is embedded
    assert "FlashAttention-4 lands in mainline torch" in body
    assert "Lorem ipsum dolor sit amet." in body
    # Header lines still present
    assert "Topic:" in body and "nowcasting" in body
    assert "Period:" in body and "2026-W19" in body


def test_body_falls_back_to_excerpt_when_artifact_missing(monkeypatch, tmp_path):
    """If artifact_path does not exist (or is None), body falls back to the
    excerpt-style content so notify still delivers something useful."""
    _set_env(monkeypatch)
    missing = tmp_path / "does-not-exist.md"
    fake_srv = MagicMock()
    captured = {}
    fake_srv.send_message.side_effect = lambda m: captured.setdefault("msg", m)
    fake_smtp = MagicMock()
    fake_smtp.__enter__ = MagicMock(return_value=fake_srv)
    fake_smtp.__exit__ = MagicMock(return_value=False)
    with patch("isbe.notify.smtplib.SMTP", return_value=fake_smtp):
        ok = send_digest_notification(
            topic_label="nowcasting",
            period_label="2026-W19",
            artifact_path=missing,
            excerpt="EXCERPT-SENTINEL",
        )
    assert ok is True
    body = captured["msg"].get_content()
    assert "EXCERPT-SENTINEL" in body
    assert "--- excerpt ---" in body


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


# ---------------------------------------------------------------------------
# render_html unit tests (ft-002 Academic Ink brand)
# ---------------------------------------------------------------------------

from isbe.notify.render import render_html  # noqa: E402

_SAMPLE_MD = (
    "# Hello World\n\n"
    "This is a **test** with [a link](https://example.com).\n\n"
    "- bullet 1\n"
    "- bullet 2\n\n"
    "```python\nprint('code block')\n```\n\n"
    "| col1 | col2 |\n|------|------|\n| a    | b    |\n\n"
    "> This is a blockquote\n"
)


def test_render_html_link_has_accent_color():
    """Links must carry accent color #0d6e6e as an inline style."""
    html = render_html(
        topic_label="nowcasting",
        period_label="2026-W21",
        artifact_md=_SAMPLE_MD,
        artifact_path=None,
    )
    # premailer inlines the style; check the anchor has the accent
    assert "#0d6e6e" in html
    assert "<a " in html


def test_render_html_background_color_inlined():
    """Background color #fdfcf8 (ivory) must appear inline on body/container."""
    html = render_html(
        topic_label="nowcasting",
        period_label="2026-W21",
        artifact_md=_SAMPLE_MD,
        artifact_path=None,
    )
    assert "#fdfcf8" in html


def test_render_html_ink_color_inlined():
    """Foreground ink #1a1a1a must appear inline."""
    html = render_html(
        topic_label="nowcasting",
        period_label="2026-W21",
        artifact_md=_SAMPLE_MD,
        artifact_path=None,
    )
    assert "#1a1a1a" in html


def test_render_html_font_family_present():
    """Serif font stack must appear in the rendered HTML."""
    html = render_html(
        topic_label="nowcasting",
        period_label="2026-W21",
        artifact_md=_SAMPLE_MD,
        artifact_path=None,
    )
    # Check for key fonts in the stack
    assert "Georgia" in html
    assert "serif" in html


def test_render_html_table_rendered():
    """Markdown table should produce a <table> element with inline borders."""
    html = render_html(
        topic_label="nowcasting",
        period_label="2026-W21",
        artifact_md=_SAMPLE_MD,
        artifact_path=None,
    )
    assert "<table" in html
    # Table should have top/bottom ink border from CSS
    assert "#1a1a1a" in html  # ink borders inlined


def test_render_html_code_block_has_code_bg():
    """Fenced code blocks must get the code-bg color #f4f1ea."""
    html = render_html(
        topic_label="nowcasting",
        period_label="2026-W21",
        artifact_md=_SAMPLE_MD,
        artifact_path=None,
    )
    assert "<pre" in html
    assert "#f4f1ea" in html


def test_render_html_banner_contains_topic_uppercase():
    """Banner must show the topic label in UPPERCASE."""
    html = render_html(
        topic_label="nowcasting",
        period_label="2026-W21",
        artifact_md="# Test",
        artifact_path=None,
    )
    assert "NOWCASTING" in html


def test_render_html_banner_contains_period():
    """Banner must show the period label verbatim."""
    html = render_html(
        topic_label="nowcasting",
        period_label="2026-W21",
        artifact_md="# Test",
        artifact_path=None,
    )
    assert "2026-W21" in html


def test_render_html_footer_dots_and_tagline():
    """Footer must contain '· · ·' dots and the ISBE tagline."""
    html = render_html(
        topic_label="nowcasting",
        period_label="2026-W21",
        artifact_md="# Test",
        artifact_path=None,
    )
    assert "·  ·  ·" in html
    assert "self-hosted research radar" in html


# ---------------------------------------------------------------------------
# multipart integration tests
# ---------------------------------------------------------------------------


def test_send_produces_multipart_when_artifact_readable(monkeypatch, tmp_path):
    """When artifact_path is a readable file, the email must be multipart with
    both text/plain and text/html parts."""
    _set_env(monkeypatch)
    artifact = tmp_path / "latest.md"
    artifact.write_text(_SAMPLE_MD, encoding="utf-8")

    captured = {}
    fake_srv = MagicMock()
    fake_srv.send_message.side_effect = lambda m: captured.setdefault("msg", m)
    fake_smtp = MagicMock()
    fake_smtp.__enter__ = MagicMock(return_value=fake_srv)
    fake_smtp.__exit__ = MagicMock(return_value=False)

    with patch("isbe.notify.smtplib.SMTP", return_value=fake_smtp):
        ok = send_digest_notification(
            topic_label="nowcasting",
            period_label="2026-W21",
            artifact_path=artifact,
            excerpt="short excerpt",
        )

    assert ok is True
    msg = captured["msg"]
    assert msg.is_multipart()
    content_types = [part.get_content_type() for part in msg.walk()]
    assert "text/plain" in content_types
    assert "text/html" in content_types


def test_send_html_part_contains_topic_and_period(monkeypatch, tmp_path):
    """The HTML part must contain the uppercased topic label and period."""
    _set_env(monkeypatch)
    artifact = tmp_path / "latest.md"
    artifact.write_text(_SAMPLE_MD, encoding="utf-8")

    captured = {}
    fake_srv = MagicMock()
    fake_srv.send_message.side_effect = lambda m: captured.setdefault("msg", m)
    fake_smtp = MagicMock()
    fake_smtp.__enter__ = MagicMock(return_value=fake_srv)
    fake_smtp.__exit__ = MagicMock(return_value=False)

    with patch("isbe.notify.smtplib.SMTP", return_value=fake_smtp):
        send_digest_notification(
            topic_label="nowcasting",
            period_label="2026-W21",
            artifact_path=artifact,
            excerpt="",
        )

    msg = captured["msg"]
    html_part = next(
        p for p in msg.walk() if p.get_content_type() == "text/html"
    )
    html_content = html_part.get_content()
    assert "NOWCASTING" in html_content
    assert "2026-W21" in html_content


def test_send_plaintext_only_when_artifact_missing(monkeypatch, tmp_path):
    """When artifact_path does not exist, email stays plaintext-only (no HTML part)."""
    _set_env(monkeypatch)
    missing = tmp_path / "gone.md"

    captured = {}
    fake_srv = MagicMock()
    fake_srv.send_message.side_effect = lambda m: captured.setdefault("msg", m)
    fake_smtp = MagicMock()
    fake_smtp.__enter__ = MagicMock(return_value=fake_srv)
    fake_smtp.__exit__ = MagicMock(return_value=False)

    with patch("isbe.notify.smtplib.SMTP", return_value=fake_smtp):
        ok = send_digest_notification(
            topic_label="nowcasting",
            period_label="2026-W21",
            artifact_path=missing,
            excerpt="fallback excerpt",
        )

    assert ok is True
    msg = captured["msg"]
    content_types = [part.get_content_type() for part in msg.walk()]
    assert "text/html" not in content_types


def test_send_html_render_failure_falls_back_to_plaintext(monkeypatch, tmp_path, capsys):
    """When HTML rendering raises, the email is still sent as plaintext and
    a WARN is emitted to stderr. Must not raise."""
    _set_env(monkeypatch)
    artifact = tmp_path / "latest.md"
    artifact.write_text(_SAMPLE_MD, encoding="utf-8")

    captured = {}
    fake_srv = MagicMock()
    fake_srv.send_message.side_effect = lambda m: captured.setdefault("msg", m)
    fake_smtp = MagicMock()
    fake_smtp.__enter__ = MagicMock(return_value=fake_srv)
    fake_smtp.__exit__ = MagicMock(return_value=False)

    with (
        patch("isbe.notify.smtplib.SMTP", return_value=fake_smtp),
        patch("isbe.notify.render_html", side_effect=RuntimeError("render boom")),
    ):
        ok = send_digest_notification(
            topic_label="nowcasting",
            period_label="2026-W21",
            artifact_path=artifact,
            excerpt="fallback excerpt",
        )

    assert ok is True  # email still sent
    msg = captured["msg"]
    content_types = [part.get_content_type() for part in msg.walk()]
    assert "text/html" not in content_types  # HTML part absent
    err = capsys.readouterr().err
    assert "HTML render failed" in err
