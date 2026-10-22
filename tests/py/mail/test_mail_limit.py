"""Per-account send limits and the send entry points."""

import json
from dataclasses import replace
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import pytest

from isbe import mail
from isbe.core import db, settings
from isbe.mail import Limiter, Window


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def refused(limiter: Limiter, account: str, admin: bool = False) -> mail.MailError:
    with pytest.raises(mail.MailError) as caught:
        limiter.acquire(account, admin=admin)
    return caught.value


def test_members_are_limited_per_account_by_hour_and_day() -> None:
    clock = Clock()
    limiter = Limiter(clock=clock)
    for _ in range(10):
        limiter.acquire("member", admin=False)
        clock.now += 1
    error = refused(limiter, "member")
    assert error.status == 429 and error.retry_after == 3600 - 10
    assert error.message == (
        "Email limit reached: this account can send 10 emails per hour. Try again in 60 minutes."
    )
    limiter.acquire("other", admin=False)  # accounts are counted separately
    clock.now += 3600 - 10  # the first send leaves the hour
    limiter.acquire("member", admin=False)
    for _ in range(19):  # 400 s apart: never 10 in an hour, but 30 in the day
        clock.now += 400
        limiter.acquire("member", admin=False)
    error = refused(limiter, "member")
    assert error.retry_after == 1000 + 86400 - clock.now
    assert error.message.endswith("30 emails per day. Try again in 21 hours.")
    clock.now += 86400
    limiter.acquire("member", admin=False)


def test_admins_have_a_higher_limit_not_an_exemption() -> None:
    clock = Clock()
    limiter = Limiter(clock=clock)
    for _ in range(60):
        limiter.acquire("admin", admin=True)
    assert "60 emails per hour" in refused(limiter, "admin", admin=True).message
    assert mail.ADMIN[0].limit > mail.MEMBER[0].limit and mail.ADMIN[1].limit > mail.MEMBER[1].limit
    with pytest.raises(ValueError):
        limiter.acquire("", admin=False)


@pytest.fixture(autouse=True)
def no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(name, raising=False)


def test_report_sends_count_only_when_the_transport_is_used(
    data: Path, serve: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def handle(req: BaseHTTPRequestHandler) -> None:
        seen.append(json.loads(req.rfile.read(int(req.headers["Content-Length"]))))
        serve.reply(req, 200, '{"data":[{"code":"EM_104"}],"request_id":"r-1"}')

    base = serve(handle)
    settings.save(data, {"ZEPTOMAIL_API_URL": "https://api.zeptomail.com/v1.1/email",
                         "ZEPTOMAIL_TOKEN": "token", "SMTP_FROM": "sender@example.com"})
    # The validated endpoint must be HTTPS; point the transport at the local server instead.
    real = mail.from_data
    monkeypatch.setattr(mail.send, "from_data", lambda root: replace(real(root), endpoint=f"{base}/email"))
    (data / "reports" / "t").mkdir()
    (data / "reports" / "t" / "2026-W40.md").write_text("# 报告\n")
    conn = db.connect(data / "isbe.db")
    conn.execute("INSERT INTO reports (id,topic_id,period,kind,path,fingerprint,created_at) "
                 "VALUES ('t/2026-W40','t','2026-W40','weekly','reports/t/2026-W40.md','{}','2026-10-01')")
    limiter = Limiter(member=[Window(2, 60, "minute")], admin=[Window(5, 60, "minute")])

    def send(to: str = "a@example.com", period: str = "2026-W40") -> dict[str, Any]:
        return mail.report(conn, data, data, "t", period, to, limiter=limiter, account="u1", admin=False)

    for bad in [lambda: send(to="not-an-address"), lambda: send(period="2026-W41")]:
        with pytest.raises(mail.MailError) as caught:
            bad()
        assert caught.value.status == 422
    assert send()["state"] == "accepted" and send()["request_id"] == "r-1"
    with pytest.raises(mail.MailError) as caught:
        send()
    assert caught.value.status == 429 and caught.value.retry_after is not None
    assert len(seen) == 2, "rejected requests never reach the provider"
    assert seen[0]["subject"] == "ISBE · t · 2026-W40" and seen[0]["textbody"] == "# 报告\n"
    # The test email shares the counter; an administrator's account has its own.
    with pytest.raises(mail.MailError):
        mail.test(data, "a@example.com", limiter=limiter, account="u1", admin=False)
    assert mail.test(data, "a@example.com", limiter=limiter, account="admin", admin=True)["state"] == "accepted"
    assert seen[-1]["subject"] == mail.TEST_SUBJECT
    assert [i["cid"] for i in seen[-1]["inline_images"]] == ["isbe-test"]
    # Without a limiter (trusted callers only), nothing is counted.
    assert mail.report(conn, data, data, "t", "2026-W40", "a@example.com", limiter=None)["state"] == "accepted"
    conn.close()
