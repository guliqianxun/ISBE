"""Shared console routes: login, sessions, accounts, report and image isolation, and administration."""

from contextlib import closing
from pathlib import Path

import pytest
from conftest import PASSWORD, TOKEN, FakeManager, add_user, call, login, make_console

from isbe import auth, mail
from isbe.core import db, paths, settings
from isbe.web import routes_sources
from isbe.web.pages import research_owner_links

ZEPTO = {
    "ZEPTOMAIL_API_URL": "https://api.zeptomail.com/v1.1/email",
    "ZEPTOMAIL_TOKEN": "private-mail-token",
    "SMTP_FROM": "sender@example.com",
}


def test_research_links_keep_the_reviewed_workspace() -> None:
    html = (
        '<a href="/api/research/evidence?topic=t&amp;period=v1&amp;id=E1">quote</a>'
        '<a href="https://source.invalid/">original</a>'
    )
    rewritten = research_owner_links(html, "owner-a")
    assert 'id=E1&amp;owner=owner-a"' in rewritten
    assert 'href="https://source.invalid/"' in rewritten


def report_fixture(root: Path, owner: str, marker: str) -> None:
    data = paths.workspace(root, owner)
    (data / "reports").mkdir(exist_ok=True)
    (data / "figures").mkdir(exist_ok=True)
    (data / "reports/same.md").write_text(f"# {marker}\n\n![private](/figures/private.png)\n")
    (data / "figures/private.png").write_text(marker)
    with closing(db.connect(data / "isbe.db")) as conn:
        conn.execute(
            "INSERT INTO reports (id,topic_id,period,kind,path,fingerprint,created_at) VALUES "
            "('r','same','2026-W42','weekly','reports/same.md','{}','2026-10-15T00:00:00Z')"
        )


SOURCES = ["/api/sources", "/api/sources/export", "/api/sources/materials", "/api/sources/jobs", "/api/sources/search"]


def test_shared_routes_isolate_accounts_reports_images_and_administration(data: Path) -> None:
    admin = add_user(data, "admin", admin=True)
    alice = add_user(data, "alice")
    bob = add_user(data, "bob")
    report_fixture(data, admin.id, "ADMIN PRIVATE")
    report_fixture(data, alice.id, "ALICE PRIVATE")
    report_fixture(data, bob.id, "BOB PRIVATE")
    console = make_console(data, shared=True, secure_cookies=True, sources=routes_sources.api)

    def get(path: str, token: str = ""):  # type: ignore[no-untyped-def]
        return call(console, "GET", path, cookie=token)

    unauthenticated = ["/api/reports", "/api/report?topic=same", "/api/report.md?topic=same",
                       "/figures/private.png", "/api/tasks", "/api/users", "/api/settings", *SOURCES]
    for path in unauthenticated:
        result = get(path)
        assert (result.status, result.body) == (401, {"error": "Please sign in to continue."}), path
    signed_in = call(console, "POST", "/api/login", {"username": "alice", "password": PASSWORD})
    assert signed_in.status == 200
    assert signed_in.body["user"]["id"] == alice.id
    cookie = signed_in.header("Set-Cookie") or ""
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "; Secure" in cookie
    token = cookie.split("isbe_session=")[1].split(";")[0]
    for path in ["/api/settings", "/api/users", "/api/runs", "/api/triage", "/api/call?id=guess", *SOURCES]:
        assert get(path, token).status == 403, path
    settings.save(data, ZEPTO)
    shown = get("/api/mail", token)
    assert shown.status == 200
    assert shown.body["mail"]["configured"] is True
    assert shown.body["mail"]["sender"] == "sender@example.com"
    assert shown.body["recipients"] == ""
    assert "private-mail-token" not in shown.text
    assert call(console, "PUT", "/api/settings", {"SMTP_FROM": "attacker@example.com"}, cookie=token).status == 403
    reports = get("/api/reports", token)
    assert len(reports.body) == 1
    assert "ALICE PRIVATE" in reports.text and "BOB PRIVATE" not in reports.text
    report = get("/api/report?topic=same&period=2026-W42", token)
    assert "ALICE PRIVATE" in report.body["html"]
    image = get("/figures/private.png", token)
    assert (image.status, image.raw) == (200, b"ALICE PRIVATE")
    assert "no-store" in (image.header("Cache-Control") or "")
    for path in [f"/api/report?topic=same&owner={bob.id}", f"/api/report.md?topic=same&owner={bob.id}",
                 f"/figures/private.png?owner={bob.id}", f"/api/sources?owner={bob.id}",
                 f"/api/sources/materials?owner={bob.id}"]:
        result = get(path, token)
        assert result.status == 403, path
        if "/api/sources" not in path:
            assert result.body == {"error": "This workspace is not available."}
    config = {"id": "research", "label": "My papers", "cadence": "weekly",
              "arxiv": {"categories": ["cs.LG"], "include_keywords": ["robot learning"]}}
    assert call(console, "POST", "/api/topics", {"config": config}, cookie=token).status == 201
    unsafe = {**config, "id": "unsafe", "metrail": {"base_url": "http://private-service"}}
    assert call(console, "POST", "/api/topics", {"config": unsafe}, cookie=token).status == 400
    with closing(db.connect(data / "isbe.db")) as conn:
        _, admin_token = auth.login(conn, "admin", PASSWORD, "127.0.0.1")
    for path in SOURCES:
        assert get(path, admin_token).status == 200, path
    for path in ["/api/sources", "/api/sources/import", "/api/sources/discover", "/api/sources/search",
                 "/api/sources/search/run", "/api/sources/search/plan/recheck",
                 "/api/sources/search/plan/weekly", "/api/sources/city/collect"]:
        assert call(console, "POST", path, {}, cookie=token).status == 403, path
        assert call(console, "POST", f"{path}?owner={alice.id}", {}, cookie=admin_token).status == 403, path
    reviewed = get(f"/api/report?topic=same&owner={alice.id}", admin_token)
    assert reviewed.status == 200
    assert f"private.png?owner={alice.id}" in reviewed.body["html"]
    assert get(f"/figures/private.png?owner={alice.id}", admin_token).raw == b"ALICE PRIVATE"
    assert get("/api/report?topic=same&owner=00000000000000000000000000000000", admin_token).status == 404
    out = call(console, "POST", "/api/logout", {}, cookie=token)
    assert (out.status, out.body) == (200, {"ok": True})
    assert "Max-Age=0" in (out.header("Set-Cookie") or "")
    assert get("/api/reports", token).status == 401


def test_cookies_are_secure_only_when_asked(data: Path) -> None:
    add_user(data, "admin", admin=True)
    console = make_console(data, shared=True)
    result = call(console, "POST", "/api/login", {"username": "admin", "password": PASSWORD})
    cookie = result.header("Set-Cookie") or ""
    assert cookie.endswith("; Path=/; HttpOnly; SameSite=Strict; Max-Age=86400")
    assert result.header("Cache-Control") == "no-store"


def test_session_and_login_answers(data: Path) -> None:
    local = make_console(data)
    session = call(local, "GET", "/api/session").body
    assert session["mode"] == "local" and session["csrf_token"] == TOKEN
    assert session["user"] == {"id": "local", "username": "local", "role": "admin", "active": True}
    assert session["llm"] == {"ready": False} and session["mail"] == {"configured": False}
    refused = call(local, "POST", "/api/login", {"username": "a", "password": "b"})
    assert (refused.status, refused.body) == (400, {"error": "Local mode does not require login."})
    add_user(data, "admin", admin=True)
    shared = make_console(data, shared=True)
    assert call(shared, "GET", "/api/session").body["user"] is None
    malformed: list[object] = [[], {"username": "admin"}, {"username": "admin", "password": PASSWORD, "remember": True}]
    for body in malformed:
        result = call(shared, "POST", "/api/login", body)
        assert (result.status, result.body) == (400, {"error": "Enter a username and password."})
    wrong = call(shared, "POST", "/api/login", {"username": "admin", "password": "wrong-password-here"})
    assert (wrong.status, wrong.body) == (401, {"error": "Invalid username or password"})
    token = login(shared, "admin")
    # A new login replaces the session it was sent with.
    again = call(shared, "POST", "/api/login", {"username": "admin", "password": PASSWORD}, cookie=token)
    assert again.status == 200
    assert call(shared, "GET", "/api/tasks", cookie=token).status == 401


def test_admin_manages_accounts_and_deactivation_cancels_queued_tasks(data: Path) -> None:
    add_user(data, "admin", admin=True)
    manager = FakeManager()
    console = make_console(data, shared=True, generation=manager)
    token = login(console, "admin")
    created = call(console, "POST", "/api/users", {"username": "carol", "password": PASSWORD}, cookie=token)
    assert created.status == 200
    carol = created.body["user"]
    assert (carol["username"], carol["role"], carol["active"]) == ("carol", "user", True)
    duplicate = call(console, "POST", "/api/users", {"username": "carol", "password": PASSWORD}, cookie=token)
    assert (duplicate.status, duplicate.body) == (409, {"error": "Username is already in use"})
    listed = call(console, "GET", "/api/users", cookie=token).body
    assert [u["username"] for u in listed["users"]] == ["admin", "carol"] and listed["csrf_token"] == TOKEN
    member = login(console, "carol")
    queued = call(console, "POST", "/api/tasks", {"topic": "papers", "request_id": "r1"}, cookie=member)
    assert queued.status == 202
    off = call(console, "PUT", f"/api/users/{carol['id']}", {"active": False}, cookie=token)
    assert (off.status, off.body["user"]["active"]) == (200, False)
    assert manager.jobs[0].state == "cancelled"
    assert call(console, "GET", "/api/tasks", cookie=member).status == 401
    for method, path, body, status, message in [
        ("PUT", "/api/users", {}, 404, "not found"),
        ("POST", f"/api/users/{carol['id']}", {}, 405, "Use PUT to update an account."),
        ("DELETE", "/api/users", {}, 405, "Use GET, POST or PUT for accounts."),
        ("PUT", f"/api/users/{carol['id']}", {"role": "admin"}, 400, "Only password and active fields may be changed."),
        ("POST", "/api/users", {"username": "x"}, 400, "Enter a username and password."),
        ("PUT", "/api/users/not-an-id", {"active": True}, 400, "Invalid account identifier"),
    ]:
        result = call(console, method, path, body, cookie=token)
        assert (result.status, result.body) == (status, {"error": message}), path
    assert call(console, "GET", "/api/users", cookie=login(console, "admin")).status == 200


def test_member_overview_hides_runs_and_provider_details(data: Path) -> None:
    add_user(data, "admin", admin=True)
    add_user(data, "dave")
    console = make_console(data, shared=True)
    overview = call(console, "GET", "/api/overview", cookie=login(console, "dave")).body
    assert overview["runs"] == [] and overview["services"] == {"llm": None}


def test_overview_services_read_root_settings(data: Path) -> None:
    add_user(data, "admin", admin=True)
    second = add_user(data, "second", admin=True)
    member = add_user(data, "erin")
    settings.save(data, {"ESSAIES_URL": "https://essaies.example", "METRAIL_API_URL": "https://metrail.example"})
    console = make_console(data, shared=True)
    token = login(console, "second")
    assert second.id != "local"
    for path in ["/api/overview", f"/api/overview?owner={member.id}"]:
        services = call(console, "GET", path, cookie=token).body["services"]
        assert (services["essaies"], services["metrail"]) == (True, True), path


class SpyLimiter(mail.Limiter):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, bool]] = []

    def acquire(self, account: str, *, admin: bool) -> None:
        self.calls.append((account, admin))
        raise mail.MailError("Email limit reached: try again later.", status=429, retry_after=120)


def test_mail_sends_count_per_account_and_answer_retry_after(data: Path) -> None:
    add_user(data, "admin", admin=True)
    frank = add_user(data, "frank")
    workspace = paths.workspace(data, frank.id)
    (workspace / "reports").mkdir()
    (workspace / "reports/t.md").write_text("# Frank's report\n\nText.\n")
    with closing(db.connect(workspace / "isbe.db")) as conn:
        conn.execute(
            "INSERT INTO reports (id,topic_id,period,kind,path,fingerprint,created_at) VALUES "
            "('r','t','2026-W42','weekly','reports/t.md','{}','2026-10-15T00:00:00Z')"
        )
    settings.save(data, ZEPTO)
    console = make_console(data, shared=True)
    spy = SpyLimiter()
    console.limiter = spy
    admin_token = login(console, "admin")
    tested = call(console, "POST", "/api/mail/test", {"recipients": "a@example.com"}, cookie=admin_token)
    assert (tested.status, tested.body) == (429, {"state": "failed", "error": "Email limit reached: try again later."})
    assert tested.header("Retry-After") == "120"
    sent = call(console, "POST", "/api/mail/report", {"topic": "t", "period": "2026-W42", "recipients": "a@example.com"},
                cookie=login(console, "frank"))
    assert sent.status == 429
    with closing(db.connect(data / "isbe.db")) as conn:
        admin_id = next(u.id for u in auth.list_users(conn) if u.username == "admin")
    assert spy.calls == [(admin_id, True), (frank.id, False)]
    assert isinstance(make_console(data).limiter, mail.Limiter)


def test_report_row_without_file_is_not_found(data: Path) -> None:
    with closing(db.connect(data / "isbe.db")) as conn:
        conn.execute(
            "INSERT INTO reports (id,topic_id,period,kind,path,fingerprint,created_at) VALUES "
            "('r','gone','2026-W42','weekly','reports/gone/2026-W42.md','{}','2026-10-15T00:00:00Z')"
        )
    console = make_console(data)
    for path in ["/api/report", "/api/report.md", "/api/report?topic=gone&period=2026-W42"]:
        assert call(console, "GET", path).body == {"error": "not found"}, path


def test_broken_topic_file_keeps_overview_and_triage(data: Path) -> None:
    (data / "topics/good.yaml").write_text(
        "id: good\nlabel: Good\ncadence: weekly\narxiv:\n  categories: [cs.LG]\n  include_keywords: [x]\n"
    )
    (data / "topics/broken.yaml").write_text("id: broken\nlabel: [unclosed\n")
    console = make_console(data)
    overview = call(console, "GET", "/api/overview")
    assert overview.status == 200
    assert [t["id"] for t in overview.body["topics"]] == ["good"]
    assert call(console, "GET", "/api/triage").body["topic"] == "good"


@pytest.mark.parametrize("path", ["/api/settings", "/help", "/api/runs", "/api/call", "/api/triage", "/api/mail/test"])
def test_members_cannot_reach_administration(data: Path, path: str) -> None:
    add_user(data, "admin", admin=True)
    add_user(data, "gina")
    console = make_console(data, shared=True)
    method = "POST" if path == "/api/mail/test" else "GET"
    result = call(console, method, path, {}, cookie=login(console, "gina"))
    assert (result.status, result.body) == (403, {"error": "Administrator access is required."})
