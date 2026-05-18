"""ISBE notification channels.

v1 仅实现 SMTP email：digest 落盘后投递一段摘要 + artifact 路径。
所有 env 变量缺失 → no-op，digest 流程绝不因推送失败而 fail。
"""
from __future__ import annotations

import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path


def is_configured() -> bool:
    """True iff the three required env vars are set: HOST + FROM + TO."""
    return all(
        os.getenv(k)
        for k in ("ISBE_SMTP_HOST", "ISBE_SMTP_FROM", "ISBE_SMTP_TO")
    )


def _warn(msg: str) -> None:
    """Surface notify failures on stderr so they don't disappear into
    stdout next to ordinary flow logs."""
    print(f"[notify] WARN: {msg}", file=sys.stderr)


def send_digest_notification(
    *,
    topic_label: str,
    period_label: str,
    artifact_path: Path | None,
    excerpt: str,
) -> bool:
    """Push a digest-ready notice via SMTP.

    Returns True iff the message was successfully sent; False on any
    configuration miss or transport failure. **Never raises** —
    digester pipelines must not fail because of notify side-effects.

    Env contract:
      ISBE_SMTP_HOST     (required)
      ISBE_SMTP_FROM     (required)
      ISBE_SMTP_TO       (required, single recipient — comma-list TBD)
      ISBE_SMTP_PORT     (default 587 → STARTTLS; 465 → SMTP_SSL)
      ISBE_SMTP_USER     (optional — login if set)
      ISBE_SMTP_PASS     (optional)
    """
    if not is_configured():
        return False

    host = os.environ["ISBE_SMTP_HOST"]
    port = int(os.getenv("ISBE_SMTP_PORT", "587"))
    user = os.getenv("ISBE_SMTP_USER", "")
    password = os.getenv("ISBE_SMTP_PASS", "")
    sender = os.environ["ISBE_SMTP_FROM"]
    recipient = os.environ["ISBE_SMTP_TO"]

    msg = EmailMessage()
    msg["Subject"] = f"[ISBE] {topic_label} — {period_label}"
    msg["From"] = sender
    msg["To"] = recipient
    body = [
        f"Topic:    {topic_label}",
        f"Period:   {period_label}",
        f"Artifact: {artifact_path if artifact_path else '(no local mirror)'}",
        "",
        "--- excerpt ---",
        excerpt,
    ]
    msg.set_content("\n".join(body))

    allow_plaintext = os.getenv("ISBE_SMTP_ALLOW_PLAINTEXT", "").strip() == "1"

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20) as srv:
                if user:
                    srv.login(user, password)
                srv.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as srv:
                srv.ehlo()
                try:
                    srv.starttls()
                    srv.ehlo()
                except smtplib.SMTPException as e:
                    # Refuse to send creds + content over plaintext unless the
                    # operator explicitly opted in (e.g. localhost relay).
                    if not allow_plaintext:
                        _warn(
                            f"STARTTLS rejected by {host}:{port} ({e}); "
                            "refusing plaintext send. Set ISBE_SMTP_ALLOW_PLAINTEXT=1 "
                            "to explicitly allow plaintext (e.g. trusted local relay)."
                        )
                        return False
                    _warn(f"sending over PLAINTEXT to {host}:{port} (opted in)")
                if user:
                    srv.login(user, password)
                srv.send_message(msg)
    except Exception as e:  # noqa: BLE001 — notify must never raise
        _warn(f"email send to {host}:{port} failed: {e}")
        return False
    return True
