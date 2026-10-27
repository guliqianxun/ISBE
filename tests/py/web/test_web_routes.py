"""Console routes: generation, settings, topics, research evidence, host parsing and the read views."""

import json
import re
from contextlib import closing
from pathlib import Path

from conftest import TOKEN, FakeManager, call, make_console

from isbe.core import db
from isbe.web.console import Static
from isbe.web.guards import Access, authority
from isbe.web.pages import figure, research_evidence_page
from isbe.web.routes import (
    CLASSIC_PATHS,
    REACT_HOME,
    REACT_PATHS,
    REACT_PREFIXES,
    react_page,
)
from isbe.web.wire import Request

TOPIC = "id: papers\nlabel: Papers\ncadence: weekly\narxiv:\n  categories: [cs.LG]\n  include_keywords: [learning]\n"


def test_generation_api_requires_protection_and_returns_only_safe_validation(data: Path) -> None:
    manager = FakeManager()
    console = make_console(data, generation=manager)
    idle = call(console, "GET", "/api/generation")
    assert idle.status == 200
    assert idle.body == {"csrf_token": TOKEN, "job": None}
    for method, host, token, content_type, expected in [
        ("GET", "attacker.example:8080", "", "", 403),
        ("POST", "localhost:8080", "wrong", "application/json", 403),
        ("POST", "localhost:8080", TOKEN, "text/plain", 415),
        ("PUT", "localhost:8080", TOKEN, "application/json", 405),
    ]:
        result = call(console, method, "/api/generation", {}, host=host, token=token, content_type=content_type)
        assert result.status == expected, (method, host)
    bodies: list[object] = [[], {"topic": "../private-secret"}, {"topic": "papers", "endpoint": "private-secret"}]
    for body in bodies:
        result = call(console, "POST", "/api/generation", body)
        assert result.status == 400
        assert result.body["code"] == "invalid_topic"
        assert "private-secret" not in result.text
        assert str(data) not in result.text
    assert manager.list("local") == []


def test_research_evidence_escapes_source_content_and_keeps_owner() -> None:
    e = {
        "source_id": "S1",
        "title": "<script>bad</script>",
        "quote": "<img src=x onerror=bad>",
        "text": "secret & <b>source</b>",
        "url": "https://example.org/?a=1&b=2",
        "limitations": ["<unsafe>"],
        "start": 0,
        "end": 20,
    }
    html = research_evidence_page(e, {"topic": "t", "period": "v1", "owner": "owner-a"})
    assert "<script>" not in html
    assert "<img" not in html
    assert "&lt;unsafe&gt;" in html
    assert "owner=owner-a&amp;id=S1" in html
    assert "https://example.org/?a=1&amp;b=2" in html
    details = html.index("<details><summary>核验记录</summary>")
    assert html.index("阅读深度") > details
    assert html.index("SHA256") > details
    assert "<details open" not in html


# The evidence page's template as the earlier Rust build rendered it, verbatim; filled in below
# and compared with the rendered page.
LEGACY_TEMPLATE = r"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>来源 · {title}</title><style>body{{max-width:850px;margin:40px auto;padding:0 24px;font:17px/1.8 system-ui;color:#20302b;background:#fbfaf6}}a{{color:#176455}}blockquote{{margin:20px 0;padding:16px 24px;border-left:4px solid #176455;background:#edf2ec;white-space:pre-wrap}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.8 system-ui}}small{{overflow-wrap:anywhere;color:#58645e}}summary{{cursor:pointer}}dt{{font-weight:600}}dd{{margin:0 0 12px}}</style><a href="/reports?{back}">← 返回报告</a><h1>{title}</h1><p>{publisher} · {published}</p><blockquote>{quote}</blockquote><dl><dt>适用范围</dt><dd>{coverage}</dd></dl><p><a href="{url}" rel="noreferrer">{publisher}｜{title}</a></p><details><summary>核验记录</summary><p>{location}</p><dl><dt>阅读深度</dt><dd>{depth}</dd><dt>使用限制</dt><dd>{limitations}</dd></dl><p><a href="/api/research/original?{original}">下载本期冻结原件</a></p><small>原件 SHA256：{sha}<br>文本 SHA256：{text_sha}<br>文本位置：UTF-8 字节 {start}–{end}（左闭右开）</small><details><summary>查看本期保存的材料文本</summary><pre>{text}</pre></details></details></html>"""


def test_research_evidence_page_matches_the_template() -> None:
    e = {
        "source_id": "S 1~*",
        "title": "T",
        "publisher": "",
        "published_on": None,
        "quote": "Q",
        "coverage": "C",
        "url": "https://x.example/",
        "location": "L",
        "reading_depth": "D",
        "limitations": ["a", "b"],
        "sha256": "s",
        "text_sha256": "ts",
        "text": "X",
        "start": 3,
        "end": 9,
    }
    html = research_evidence_page(e, {"period": "2026 W1", "topic": "t~*", "owner": "o"})
    back = "topic=t%7E*&amp;period=2026+W1&amp;owner=o"
    expected = LEGACY_TEMPLATE.format(
        title="T", back=back, publisher="发布机构未注明", published="发布日期未确认", quote="Q",
        coverage="C", url="https://x.example/", location="L", depth="D", limitations="a；b",
        original=f"{back}&amp;id=S 1~*", sha="s", text_sha="ts", start=3, end=9, text="X",
    )
    assert html == expected


def test_settings_and_topic_io_errors_do_not_disclose_storage_paths(tmp_path: Path) -> None:
    data = tmp_path / "isbe-private-io-path"
    (data / "topics").mkdir(parents=True)
    (data / "settings.json").mkdir()
    (data / "topics/papers.yaml").write_text(TOPIC)
    console = make_console(data)
    for path in ["/api/topics", "/api/topics/papers"]:
        result = call(console, "GET", path)
        assert result.status == 200
        assert "isbe-private-io-path" not in result.text
        assert "could not be read" in result.text
    result = call(console, "PUT", "/api/settings", {})
    assert result.status == 500
    assert "isbe-private-io-path" not in result.text


def test_topic_api_creates_updates_and_deletes_without_removing_reports(data: Path) -> None:
    console = make_console(data)

    def request(method: str, path: str, body: object = None) -> tuple[int, dict]:
        result = call(console, method, path, body)
        return result.status, result.body

    code, listed = request("GET", "/api/topics")
    assert code == 200
    assert listed["topics"] == []
    assert listed["csrf_token"] == TOKEN
    assert "data_dir" not in listed and "command_shell" not in listed
    config = {"id": "t", "label": "Topic", "cadence": "weekly",
              "arxiv": {"categories": ["cs.LG"], "include_keywords": ["forecasting"]}}
    code, created = request("POST", "/api/topics", {"config": config})
    assert code == 201, created
    assert created["config"] == config
    assert request("POST", "/api/topics", {"config": config})[0] == 409
    assert request("PUT", "/api/topics/t", {"config": config})[0] == 400
    edited = {**config, "label": "Updated topic"}
    code, saved = request("PUT", "/api/topics/t", {"config": edited, "revision": created["revision"]})
    assert code == 200, saved
    assert saved["config"]["label"] == "Updated topic"
    assert request("PUT", "/api/topics/t", {"config": config, "revision": created["revision"]})[0] == 409
    assert request("DELETE", "/api/topics/t", {"revision": created["revision"]})[0] == 409
    (data / "reports/t").mkdir(parents=True)
    (data / "reports/t/2026-W42.md").write_text("# Saved report\n\nKeep this report.")
    with closing(db.connect(data / "isbe.db")) as conn:
        conn.execute(
            "INSERT INTO reports (id,topic_id,period,kind,path,fingerprint,created_at) VALUES "
            "('r','t','2026-W42','weekly','reports/t/2026-W42.md','{}','2026-10-15')"
        )
    assert request("DELETE", "/api/topics/t", {"revision": saved["revision"]}) == (200, {"deleted": "t"})
    assert request("GET", "/api/topics/t")[0] == 404
    assert request("GET", "/api/topics")[1]["topics"] == []
    assert (data / "topics").is_dir()
    assert request("GET", "/api/report?topic=t&period=2026-W42")[0] == 200
    assert len(request("GET", "/api/reports")[1]) == 1
    code, overview = request("GET", "/api/overview")
    assert code == 200
    assert overview["topics"] == []
    assert "data_dir" not in overview and "command_shell" not in overview
    assert request("POST", "/api/topics", {"config": {**config, "id": "other"}})[0] == 201
    # A deleted topic must not silently show another topic.
    assert request("GET", "/api/triage?topic=t")[0] == 404


def test_topic_api_enforces_request_protection_and_rejects_invalid_paths(data: Path) -> None:
    console = make_console(data)
    for method, host, token, content_type, expected in [
        ("GET", "attacker.example:8080", "", "", 403),
        ("POST", "localhost:8080", "", "application/json", 403),
        ("PUT", "localhost:8080", "wrong", "application/json", 403),
        ("DELETE", "localhost:8080", TOKEN, "text/plain", 415),
    ]:
        result = call(console, method, "/api/topics/t", {}, host=host, token=token, content_type=content_type)
        assert result.status == expected
    for path in ["/api/topics/../secret", "/api/topics/%2e%2e", "/api/topics/", "/api/topics/C:\\secret"]:
        assert call(console, "GET", path).status == 400, path
    large = {"config": {"id": "t", "label": "x" * 65537}}
    assert call(console, "POST", "/api/topics", large).status == 413
    assert call(console, "POST", "/api/topics", []).status == 400


def test_settings_require_local_host_json_and_request_token(data: Path) -> None:
    console = make_console(data)
    for method, host, token, content_type, body, code in [
        ("GET", "attacker.example:8080", "", "", b"", 403),
        ("GET", "localhost:9999", "", "", b"", 403),
        ("PUT", "localhost:8080", "wrong", "application/json", b"{}", 403),
        ("PUT", "localhost:8080", TOKEN, "text/plain", b"{}", 415),
        ("PUT", "localhost:8080", TOKEN, "application/json", b"[]", 400),
        ("POST", "localhost:8080", TOKEN, "application/json", b"{}", 405),
    ]:
        result = call(console, method, "/api/settings", host=host, token=token, content_type=content_type, raw=body)
        assert result.status == code, (method, host, token)

    def request(host: str) -> Request:
        return Request("GET", "/api/settings", "", [("host", host)], "127.0.0.1")

    access = Access(TOKEN, 8080)
    for host in ["localhost:8080", "127.0.0.1:8080", "[::1]:8080", "LocalHost:8080", "admin@localhost:8080"]:
        assert access.allows_host(request(host)), host
    for host in ["localhost", "[::1]", "localhost:8080/x", "localhost:8080:1", "::1:8080", "", "local host:8080",
                 "10.0.0.1:8080", "[::ffff:127.0.0.1]:8080", "localhost:%38080"]:
        assert not access.allows_host(request(host)), host
    wildcard = Access(TOKEN, None)
    assert not wildcard.allows_host(request("attacker.example:8080"))
    for host in ["localhost:18080", "127.0.0.1:18080", "192.0.2.1:8080"]:
        assert wildcard.allows_host(request(host)), host
    assert call(console, "PUT", "/api/settings", raw=b" " * 65537).status == 413


def test_host_authority_follows_the_http_crate() -> None:
    assert authority("localhost:8080") == ("localhost", 8080)
    assert authority("[::1]:8080") == ("[::1]", 8080)
    assert authority("[::1]") == ("[::1]", None)
    assert authority("user:pw@host") == ("host", None)
    # A leading '+' is accepted in the port number.
    assert authority("localhost:+8080") == ("localhost", 8080)
    assert authority("localhost:99999") == ("localhost", None)
    for bad in ["", "a b", "x@", "[::1", "::1]", "a:1:2", "h%41", "h/x", "h?q", "é"]:
        assert authority(bad) is None, bad


def test_mail_routes_require_host_token_json_and_reject_client_transport_overrides(data: Path) -> None:
    console = make_console(data)
    for host, token, content_type, body, code in [
        ("attacker.example:8080", TOKEN, "application/json", b"{}", 403),
        ("localhost:8080", "wrong", "application/json", b"{}", 403),
        ("localhost:8080", TOKEN, "text/plain", b"{}", 415),
        ("localhost:8080", TOKEN, "application/json", b" " * 65537, 413),
        ("localhost:8080", TOKEN, "application/json", b"[]", 400),
        ("localhost:8080", TOKEN, "application/json", b'{"recipients":"a@example.com"}', 400),
        ("localhost:8080", TOKEN, "application/json",
         b'{"topic":"","period":"2026-W40","recipients":"a@example.com"}', 422),
    ]:
        result = call(console, "POST", "/api/mail/report", host=host, token=token, content_type=content_type, raw=body)
        assert result.status == code, body[:40]
    for key in ["from", "sender", "endpoint", "token", "subject"]:
        override = {"topic": "topic", "period": "2026-W40", "recipients": "a@example.com", key: "must-not-be-echoed"}
        result = call(console, "POST", "/api/mail/report", override)
        assert result.status == 400
        assert "must-not-be-echoed" not in result.text
    assert call(console, "POST", "/api/mail/test", {"recipients": "bad"}).status == 422


def test_settings_api_saves_without_disclosing_secrets(data: Path) -> None:
    console = make_console(data)
    result = call(console, "PUT", "/api/settings", {"OPENAI_API_KEY": "isbe-test-secret", "ISBE_LLM_PROVIDER": "openai"})
    assert result.status == 200
    assert "isbe-test-secret" not in result.text
    assert result.body["configured"]["OPENAI_API_KEY"] is True
    assert result.body["csrf_token"] == TOKEN
    assert "isbe-test-secret" in (data / "settings.json").read_text()


def test_settings_validation_and_method_errors(data: Path) -> None:
    console = make_console(data)
    wrong: list[object] = [[], {"ISBE_LLM_TIMEOUT_S": 5}, "x"]
    for body in wrong:
        result = call(console, "PUT", "/api/settings", body)
        assert result.body == {"error": "Expected an object of setting names and string or null values"}
    result = call(console, "PUT", "/api/settings", {"NOT_A_SETTING": "x"})
    assert result.status == 400
    assert result.body == {"error": "unsupported setting name"}
    assert call(console, "DELETE", "/api/settings", {}).body == {"error": "Use GET or PUT for settings"}
    assert call(console, "PUT", "/api/settings", raw=b"{").body == {"error": "Expected a JSON object"}


def test_figures_stay_inside_their_directory(tmp_path: Path) -> None:
    for bad in ["../isbe.db", "..%2fisbe.db", ".hidden.png", "a/b.png", "x.svg", ""]:
        assert figure(Path("does-not-matter"), bad).status == 404, bad
    data = tmp_path / "data"
    (data / "figures").mkdir(parents=True)
    (tmp_path / "outside.png").write_bytes(b"secret")
    (data / "figures/link.png").symlink_to(tmp_path / "outside.png")
    (data / "figures/real.png").write_bytes(b"png")
    assert figure(data, "link.png").status == 404
    shown = figure(data, "real.png")
    assert (shown.status, shown.body, shown.get("Cache-Control")) == (200, b"png", "private, no-store")
    (data / "figures/real.png").rename(tmp_path / "moved.png")
    (data / "figures/link.png").unlink()
    (data / "figures").rmdir()
    (data / "figures").symlink_to(tmp_path)
    assert figure(data, "moved.png").status == 404


def test_not_found_method_and_static_replies(data: Path) -> None:
    console = make_console(data)
    missing = call(console, "GET", "/api/nope")
    assert (missing.status, missing.body) == (404, {"error": "not found"})
    assert missing.header("Cache-Control") == "no-store"
    posted = call(console, "POST", "/somewhere", {})
    assert (posted.status, posted.raw, posted.header("Content-Type")) == (405, b"method not allowed", "text/plain")
    runs = call(console, "POST", "/api/runs", {})
    assert (runs.status, runs.raw, runs.header("Cache-Control")) == (405, b"method not allowed", "no-store")
    page = call(console, "GET", "/topics", host="attacker.example")
    assert page.status == 200 and page.header("Content-Type") == "text/html; charset=utf-8"
    assert page.header("Cache-Control") == "no-cache"
    assert b'<script src="/app.js"></script>' in page.raw
    assert call(console, "GET", "/app.js").header("Content-Type") == "text/javascript; charset=utf-8"
    assert call(console, "GET", "/app.css").header("Content-Type") == "text/css; charset=utf-8"
    help = call(console, "GET", "/help/service")
    assert help.status == 200 and help.header("Cache-Control") == "no-store"
    assert "<h1>服务配置</h1>" in help.text and "<h1>Docker 部署、升级与备份</h1>" in help.text


def test_react_pages_and_build_files(data: Path, tmp_path: Path) -> None:
    build = tmp_path / "app"
    (build / "assets/nested").mkdir(parents=True)
    (build / "index.html").write_text('<!doctype html><script type="module" src="/app/assets/index-1.js"></script>')
    (build / "assets/index-1.js").write_text("console.log(1)")
    (build / "assets/index-1.css").write_text("a{}")
    (build / "assets/nested/font.woff2").write_bytes(b"woff")
    (build / "assets/notes.txt").write_text("not served")
    (tmp_path / "secret.js").write_text("outside")
    (build / "assets/link.js").symlink_to(tmp_path / "secret.js")
    console = make_console(data)
    console.static = Static(app_dir=build)
    # /settings moved here from the classic console (#79): its old links open the React page.
    for path in ("/", "/?g=run-1", "/galaxies", "/system", "/settings", "/help", "/question", "/question/", "/question/runs/7?x=1"):
        page = call(console, "GET", path, host="attacker.example")
        assert page.status == 200 and page.header("Content-Type") == "text/html; charset=utf-8", path
        assert page.header("Cache-Control") == "no-cache"
        assert b"/app/assets/index-1.js" in page.raw
    # The classic console's pages, exactly.
    for path in CLASSIC_PATHS:
        page = call(console, "GET", path)
        assert page.status == 200 and b'<script src="/app.js"></script>' in page.raw, path
    # Any other path: the React console's not-found page, with a 404.
    for path in (
        "/nope", "/questions", "/systems", "/topics/anything", "/overview/", "/galaxies/x", "/settings/x", "/help/x",
        "/question/runs/", "/question/runs/7/x", "/index.html",
    ):
        page = call(console, "GET", path, host="attacker.example")
        assert page.status == 404 and b"/app/assets/index-1.js" in page.raw, path
        assert page.header("Content-Type") == "text/html; charset=utf-8", path
    script = call(console, "GET", "/app/assets/index-1.js", host="attacker.example")
    assert (script.status, script.raw) == (200, b"console.log(1)")
    assert script.header("Content-Type") == "text/javascript; charset=utf-8"
    assert script.header("Cache-Control") == "public, max-age=31536000, immutable"
    assert call(console, "GET", "/app/assets/index-1.css").header("Content-Type") == "text/css; charset=utf-8"
    assert call(console, "GET", "/app/assets/nested/font.woff2").raw == b"woff"
    for path in (
        "/app/assets/notes.txt", "/app/assets/link.js", "/app/index.html", "/app/assets/../index.html",
        "/app/assets/%2e%2e/index.html", "/app/", "/app/assets/missing.js",
    ):
        assert call(console, "GET", path).status == 404, path
    assert call(console, "POST", "/question", {}).status == 405


def test_react_pages_without_a_build(data: Path, tmp_path: Path) -> None:
    console = make_console(data)
    console.static = Static(app_dir=tmp_path / "missing")
    page = call(console, "GET", "/question")
    assert page.status == 503 and b"npm --prefix frontend run build" in page.raw
    assert page.header("Cache-Control") == "no-store"
    assert call(console, "GET", "/app/assets/index.js").status == 404
    assert b'<script src="/app.js"></script>' in call(console, "GET", "/overview").raw
    missing = call(console, "GET", "/nope")
    assert missing.status == 404 and b"npm --prefix frontend run build" in missing.raw


def test_classic_paths_are_the_classic_consoles_pages() -> None:
    app = (Path(__file__).parents[3] / "web/app.js").read_text(encoding="utf-8")
    pages = re.search(r"const PAGES = \{([^}]*)\}", app)
    assert pages is not None
    assert set(re.findall(r"'(/[a-z]+)'", pages.group(1))) == set(CLASSIC_PATHS)


def test_react_paths_are_the_react_consoles_routes() -> None:
    app = (Path(__file__).parents[3] / "frontend/src/App.tsx").read_text(encoding="utf-8")
    pages = re.search(r"export function Pages\(\)(.*?)\n}", app, re.S)
    assert pages is not None
    routes = set(re.findall(r'<Route path="([^"*]+)"', pages.group(1)))
    fixed = {r for r in routes if ":" not in r}
    assert fixed == {REACT_HOME, *REACT_PATHS}
    # A route with a parameter is exactly one segment under a prefix.
    params = routes - fixed
    assert {r.split(":")[0] for r in params} == set(REACT_PREFIXES)
    for route in params:
        prefix, param = route.split(":", 1)
        assert "/" not in param, f"{route} is deeper than one segment under {prefix}"
    for path in [REACT_HOME, *REACT_PATHS, *(r.replace(":id", "x") for r in params)]:
        assert react_page(path), path


def test_read_views_answer_the_established_shapes(data: Path) -> None:
    (data / "topics/papers.yaml").write_text(TOPIC)
    with closing(db.connect(data / "isbe.db")) as conn:
        conn.execute(
            "INSERT INTO llm_calls (id, run_id, purpose, model, prompt_sha, prompt, response, tokens_in, "
            "tokens_out, ms, created_at) VALUES ('c1', NULL, 'triage', 'm', 'abcdef123', ?, 'r', 3, 4, 5, ?)",
            (json.dumps({"system": "s", "user": "u", "max_tokens": 9}), db.now()),
        )
    console = make_console(data)
    overview = call(console, "GET", "/api/overview").body
    assert [t["id"] for t in overview["topics"]] == ["papers"]
    topic = overview["topics"][0]
    assert topic["daily"] == [0] * 7 and topic["cadence"] == "weekly" and topic["flavor"] == "papers"
    assert topic["since"].endswith("T00:00:00Z")
    assert overview["llm"] == {"calls": 1, "tokens_in": 3, "tokens_out": 4}
    assert overview["services"]["llm"] is None and overview["services"]["papers"] == {
        "source": "openalex", "ready": True}
    # Home's slim view: labels, the newest report covers and two service flags, no statistics.
    home = call(console, "GET", "/api/home").body
    assert home == {"topics": [{"id": "papers", "label": home["topics"][0]["label"]}], "reports": [],
                    "services": {"papers": {"source": "openalex", "ready": True}, "llm": None}}
    triage = call(console, "GET", "/api/triage").body
    assert (triage["topic"], triage["topics"], triage["items"]) == ("papers", ["papers"], [])
    runs = call(console, "GET", "/api/runs").body
    assert runs["purposes"] == [{"purpose": "triage", "calls": 1, "tokens": 7}]
    assert runs["calls"][0]["sha"] == "abcdef1"
    one = call(console, "GET", "/api/call?id=c1").body
    assert one["prompt"] == {"system": "s", "user": "u", "max_tokens": 9}
    assert call(console, "GET", "/api/call?id=missing").status == 404
