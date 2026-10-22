"""Search plans and runs: validation, providers, deduplication, rechecks and stored formats."""

import sqlite3
import time
from collections.abc import Iterator
from datetime import date
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from registry_helpers import count, plan

from isbe import search
from isbe.core import db, jsonfmt, settings
from isbe.core.errors import HttpError
from isbe.search import Provider, SearchPlan, providers, rechecks, results, runner
from isbe.search.plans import BAIDU_REMOVED
from isbe.sources import registry


@pytest.fixture
def saved(conn: sqlite3.Connection) -> Iterator[tuple[sqlite3.Connection, SearchPlan]]:
    p = plan()
    search.save(conn, p, None, None)
    yield conn, p


def run_row(conn: sqlite3.Connection, id: str, p: SearchPlan) -> None:
    conn.execute(
        "INSERT INTO source_jobs(id,fingerprint,operation,state,created_at) "
        "VALUES (?1,'fingerprint','search','running','now')",
        (id,),
    )
    search.snapshot(conn, id, p)


def changed(p: SearchPlan, **fields: Any) -> SearchPlan:
    return p.model_copy(update=fields)


def raw(value: Any) -> bytes:
    return jsonfmt.dumps(value).encode()


def test_validates_dates_unique_queries_and_revisions(saved: tuple[sqlite3.Connection, SearchPlan]) -> None:
    conn, p = saved
    for invalid in [
        changed(p, start_date="2026-02-30"),
        changed(p, start_date="2026-1-01"),
        changed(p, queries=[*p.queries, " Shanghai ESG "]),
    ]:
        with pytest.raises(HttpError):
            invalid.validated()
    changed(p, queries=["上" * 200, *p.queries[1:]]).validated()
    renamed = changed(p, name="Renamed")
    with pytest.raises(HttpError):
        search.validate_saved(conn, renamed)
    with pytest.raises(HttpError):
        search.save(conn, renamed, "0", renamed.id)
    assert search.save(conn, renamed, "1", renamed.id)["revision"] == "2"
    search.validate_saved(conn, renamed)


def test_validation_messages_and_stored_plan(saved: tuple[sqlite3.Connection, SearchPlan]) -> None:
    conn, p = saved
    cases = [
        (changed(p, name=" "), "Search name and topic must contain 1–120 visible characters"),
        (changed(p, queries=[]), "Use between 1 and 8 search queries"),
        (changed(p, end_date="2026-10-1"), "Use dates in YYYY-MM-DD format"),
        (changed(p, end_date="2026-09-30"), "Use an ordered date range, zh-CN or en, and 1–20 results per query"),
        (changed(p, language="de"), "Use an ordered date range, zh-CN or en, and 1–20 results per query"),
        (changed(p, max_results=21), "Use an ordered date range, zh-CN or en, and 1–20 results per query"),
    ]
    for bad, message in cases:
        with pytest.raises(HttpError, match=f"^{message}$"):
            bad.validated()
    with pytest.raises(HttpError, match="^Search plan ID already exists$"):
        search.save(conn, p, None, None)
    with pytest.raises(HttpError, match="^Search plan ID cannot change$"):
        search.save(conn, p, "1", "other")
    stored = conn.execute("SELECT config FROM search_plans").fetchone()[0]
    assert stored == (
        '{"id":"shanghai-esg","name":"Shanghai ESG","topic":"Shanghai","provider":"searxng",'
        '"queries":["Shanghai ESG","Shanghai green finance"],"start_date":"2026-10-01",'
        '"end_date":"2026-10-18","language":"en","max_results":20}'
    )
    padded = changed(p, name="  Padded  ", queries=["  Shanghai ESG"])
    assert search.save(conn, padded, "1", p.id)["config"]["name"] == "Padded"
    assert search.list(conn)[0]["config"]["queries"] == ["Shanghai ESG"]
    with pytest.raises(HttpError) as missing:
        search.get(conn, "missing")
    assert missing.value.status == 404


def test_provider_schema_and_untrusted_dates() -> None:
    p = plan()
    body = raw(
        {
            "results": [
                {"url": "https://news.example/a#section", "title": "Headline", "publishedDate": "2026-10-15"},
                {"url": "javascript:alert(1)", "title": "bad"},
                {"url": "https://user:pass@news.example/a", "title": "bad"},
            ]
        }
    )
    parsed = results.parse(p, body)
    assert parsed.rejected == 2
    assert len(parsed.hits) == 1
    assert parsed.hits[0]["url"] == "https://news.example/a"
    assert parsed.hits[0]["search_date_hint"] == "2026-10-15"
    assert parsed.hits[0]["published_at"] is None
    assert parsed.hits[0]["date_status"] == "unknown"
    assert parsed.hits[0]["publisher"] == "news.example"
    assert parsed.hits[0]["rank"] == 1


def test_responses_without_a_results_array_or_json_are_refused() -> None:
    p = plan()
    ok = results.parse(p, raw({"results": [{"url": "https://a.example/x", "title": " T\x07 ", "website": "Site"}]}))
    assert (ok.hits[0]["title"], ok.hits[0]["search_date_hint"], ok.hits[0]["publisher"]) == ("T", None, "Site")
    # A body shaped like the removed Baidu provider's is not a SearXNG response.
    for body in (raw({"references": []}), raw({"results": {}}), raw([])):
        with pytest.raises(results.ResponseError, match="^Search provider response did not contain a results array$"):
            results.parse(p, body)
    with pytest.raises(results.ResponseError, match="^Search provider did not return valid JSON$"):
        results.parse(p, b"<html>")


def test_deduplicates_across_queries_and_runs_preserving_every_hit_and_raw_response(
    saved: tuple[sqlite3.Connection, SearchPlan],
) -> None:
    conn, p = saved
    body = raw({"results": [{"url": "https://news.example/a", "title": "A headline", "publishedDate": "2026-10-15"}]})
    verified = 0

    def verify(url: str, start: str, end: str) -> dict[str, Any]:
        nonlocal verified
        verified += 1
        return {"published_at": "2026-09-01", "date_status": "out_of_range", "read_status": "read"}

    for id in ["first", "second"]:
        run_row(conn, id, p)
        stats = runner.run_with(conn, id, p, lambda _p, _q: body, verify)
        assert stats["found"] == 2
        assert stats["new"] == (1 if id == "first" else 0)
        assert stats["checked"] == 1
    assert verified == 2
    assert count(conn, "SELECT count(*) FROM search_materials") == 1
    assert count(conn, "SELECT count(*) FROM search_hits") == 4
    assert count(conn, "SELECT count(*) FROM search_requests WHERE raw IS NOT NULL") == 4
    materials = registry.materials_filtered(conn, "", 0, "out_of_range")
    assert materials["total"] == 1
    assert len(materials["items"][0]["search_queries"]) == 2
    assert materials["items"][0]["published_at"] == "2026-09-01"
    assert materials["items"][0]["hit_count"] == 4
    assert materials["items"][0]["source_id"] == "search:shanghai-esg"


def test_partial_failure_and_check_limit_are_explicit(saved: tuple[sqlite3.Connection, SearchPlan]) -> None:
    conn, p = saved
    p = changed(p, queries=[*p.queries, "third"])
    run_row(conn, "partial", p)

    def fetch(_: SearchPlan, query: str) -> bytes:
        if query == "third":
            raise RuntimeError("provider unavailable")
        part = "a" if query == "Shanghai ESG" else "b"
        return raw({"results": [{"url": f"https://news.example/{part}/{i}", "title": "headline"} for i in range(20)]})

    stats = runner.run_with(conn, "partial", p, fetch, lambda *_: {"published_at": None, "date_status": "unknown"})
    assert stats["state"] == "partial"
    assert stats["failed"] == 1
    assert stats["checked"] == 20
    assert stats["unchecked"] == 20
    assert stats["queries"][2] == {
        "query": "third",
        "state": "failed",
        "error": "provider unavailable",
        "sent": "third",
        "constraints": {
            "executed": ["terms"],
            "deferred": ["language", "publication_window"],
            "unknown": ["language"],
        },
    }
    assert registry.materials_filtered(conn, "", 0, "")["total"] == 40
    row = conn.execute("SELECT state,error,raw FROM search_requests WHERE query_index=2").fetchone()
    assert tuple(row) == ("failed", "provider unavailable", None)


def test_partial_engines_and_exact_limit_are_not_reported_as_complete(
    saved: tuple[sqlite3.Connection, SearchPlan],
) -> None:
    conn, p = saved
    p = changed(p, queries=p.queries[:1], max_results=1)
    run_row(conn, "engines", p)
    body = raw({"results": [{"url": "https://news.example/a", "title": "headline"}], "unresponsive_engines": [["bing", "timeout"]]})
    stats = runner.run_with(conn, "engines", p, lambda *_: body, lambda *_: {"published_at": None, "date_status": "unknown"})
    assert stats["state"] == "partial"
    assert stats["partial"] == 1
    assert stats["queries"][0]["unresponsive_engines"] == 1
    assert stats["limited"] is True
    assert stats["queries"][0]["state"] == "partial"


def test_baidu_removed_plans_still_list_and_recheck_but_cannot_be_saved_or_run(
    saved: tuple[sqlite3.Connection, SearchPlan],
) -> None:
    # Baidu search was removed on 2026-10-21; plans saved for it stay readable.
    conn, p = saved
    old = changed(p, id="old-baidu", provider=Provider.BAIDU)
    conn.execute("INSERT INTO search_plans(id,config) VALUES (?1,?2)", (old.id, old.dumps()))
    assert [row["config"]["provider"] for row in search.list(conn)] == ["baidu", "searxng"]
    revision = search.get(conn, old.id)["revision"]
    for attempt in (
        old.validated,
        lambda: search.save(conn, changed(old, id="new-baidu"), None, None),
        lambda: search.save(conn, old, revision, old.id),
        lambda: search.validate_saved(conn, old),
    ):
        with pytest.raises(HttpError) as refused:
            attempt()
        assert (refused.value.status, refused.value.message) == (400, BAIDU_REMOVED)
    assert count(conn, "SELECT count(*) FROM search_plans WHERE id='new-baidu'") == 0
    assert search.get(conn, old.id)["revision"] == revision

    material(conn, old, "kept", None)
    recheck_row(conn, "old-recheck", old.id, None)
    found = {"published_at": "2026-10-10", "updated_at": None, "date_status": "in_range",
             "date_evidence": [{"field": "datePublished", "value": "2026-10-10"}],
             "date_error": None, "read_status": "read"}  # fmt: skip
    assert rechecks.recheck_with(conn, "old-recheck", lambda *_: found)["checked"] == 1
    conn.execute("UPDATE source_jobs SET state='succeeded' WHERE id='old-recheck'")
    switched = search.save(conn, changed(old, provider=Provider.SEARXNG), revision, old.id)
    assert switched["config"]["provider"] == "searxng"
    search.validate_saved(conn, changed(old, provider=Provider.SEARXNG))


def test_searxng_http_request_is_encoded_and_provider_errors_are_redacted(serve) -> None:
    request_plan = changed(plan(), start_date="2024-01-01")
    for code in [200, 401, 302]:
        seen: list[BaseHTTPRequestHandler] = []

        def handle(req: BaseHTTPRequestHandler, code: int = code) -> None:
            seen.append(req)
            body = '{"results":[]}' if code == 200 else "echoed-provider-secret"
            serve.reply(req, code, body, {"Location": "/elsewhere"})

        base = serve(handle)
        with settings.task_scope({"SEARXNG_URL": base}):
            if code == 200:
                assert providers.request(request_plan, "上海 ESG & green") == b'{"results":[]}'
            else:
                with pytest.raises(providers.ProviderError) as failure:
                    providers.request(request_plan, "上海 ESG & green")
                assert str(code) in str(failure.value)
                assert "echoed-provider-secret" not in str(failure.value)
        (req,) = seen
        url = urlsplit(req.path)
        assert url.path == "/search"
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        assert query == {"q": "上海 ESG & green", "format": "json", "language": "en", "pageno": "1"}
        assert req.headers.get("Authorization") is None
    assert providers.form_encode([("q", "a b*~/é")]) == "q=a+b*%7E%2F%C3%A9"


def test_availability_preflight_and_service_url() -> None:
    conn = db.connect(":memory:")
    try:
        with settings.task_scope({}):
            assert search.availability(conn) == {"searxng": {"configured": False}}
            with pytest.raises(HttpError, match="^Configure SEARXNG_URL before searching$"):
                search.preflight(conn, plan())
        with settings.task_scope({"SEARXNG_URL": "https://s.example/"}):
            assert search.availability(conn) == {"searxng": {"configured": True}}
            search.preflight(conn, plan())
        for base, url in [("https://s.example/", "https://s.example/search"), ("https://s.example/x//", "https://s.example/x/search")]:
            with settings.task_scope({"SEARXNG_URL": base}):
                assert providers.searxng_url() == url
        with settings.task_scope({"SEARXNG_URL": "https://s.example/?a=1"}), pytest.raises(HttpError, match="query"):
            providers.searxng_url()
    finally:
        conn.close()


def test_completed_search_request_is_idempotent_after_plan_changes(data: Path) -> None:
    conn = db.connect(data / "isbe.db")
    try:
        p = plan()
        search.save(conn, p, None, None)
        operation = registry.Search(p)
        fingerprint = db.sha256(jsonfmt.dumps(operation.to_json(), sort_keys=False))
        assert jsonfmt.dumps(operation.to_json(), sort_keys=False).startswith('{"operation":"search","plan":{"id":')
        conn.execute(
            "INSERT INTO source_jobs(id,fingerprint,operation,state,created_at) VALUES ('same',?1,'search','succeeded','now')",
            (fingerprint,),
        )
        edited = changed(p, name="New name")
        search.save(conn, edited, "1", edited.id)
        assert registry.start(data, operation, "same")["state"] == "succeeded"
        with pytest.raises(HttpError) as refused:
            registry.start(data, operation, "new")
        assert refused.value.status == 409
        assert count(conn, "SELECT count(*) FROM source_jobs") == 1
    finally:
        conn.close()


def test_coarse_recency_hint_covers_recent_start_and_leaves_old_ranges_unrestricted() -> None:
    today = date(2026, 10, 18)
    assert providers.time_range("2026-10-18", today) == "month"
    assert providers.time_range("2026-09-21", today) == "month"
    assert providers.time_range("2026-09-19", today) == "month"
    assert providers.time_range("2026-09-18", today) == "year"
    assert providers.time_range("2025-10-19", today) == "year"
    assert providers.time_range("2025-10-18", today) is None
    assert providers.time_range("2026-10-19", today) is None


def material(conn: sqlite3.Connection, p: SearchPlan, suffix: str, published: str | None) -> str:
    url = f"https://news.example/{suffix}"
    id = db.sha256(f"search:{p.id}\n{url}")
    content = {
        "url": url, "title": "Original headline", "kind": "web_search", "plan_id": p.id,
        "source_name": p.name, "topic": p.topic, "provider": "searxng", "query": "Original query",
        "search_date_hint": "Original search date hint", "run_id": "original-search",
        "start_date": p.start_date, "end_date": p.end_date, "published_at": published, "updated_at": None,
        "date_status": "out_of_range" if published else "unknown",
        "date_evidence": [{"field": "datePublished", "value": published}] if published else [],
        "date_error": None, "read_status": "read",
    }  # fmt: skip
    conn.execute(
        "INSERT INTO search_materials(id,plan_id,url,content,first_seen,last_seen) "
        "VALUES (?1,?2,?3,?4,'first-discovery','last-search')",
        (id, p.id, url, jsonfmt.dumps(content)),
    )
    return id


def recheck_row(conn: sqlite3.Connection, id: str, plan_id: str, after: str | None) -> None:
    conn.execute(
        "INSERT INTO source_jobs(id,fingerprint,operation,state,created_at) "
        "VALUES (?1,'fingerprint','recheck','running','now')",
        (id,),
    )
    search.snapshot_recheck(conn, id, plan_id, after)


def test_recheck_fills_unknown_retains_failed_evidence_and_uses_current_window(
    saved: tuple[sqlite3.Connection, SearchPlan],
) -> None:
    conn, p = saved
    unknown = material(conn, p, "unknown", None)
    known = material(conn, p, "known", "2026-09-01")
    p = changed(p, start_date="2026-09-01", end_date="2026-09-30")
    search.save(conn, p, "1", p.id)
    classified = registry.materials_filtered(conn, "", 0, "in_range")
    assert classified["total"] == 1
    assert classified["items"][0]["id"] == known
    assert classified["items"][0]["start_date"] == "2026-09-01"
    assert registry.materials_filtered(conn, "", 0, "unknown")["total"] == 1
    recheck_row(conn, "recheck-1", p.id, None)

    def verify(url: str, start: str, end: str) -> dict[str, Any]:
        assert (start, end) == ("2026-09-01", "2026-09-30")
        if url.endswith("/unknown"):
            return {"published_at": "2026-09-10", "updated_at": None, "date_status": "in_range",
                    "date_evidence": [{"field": "datePublished", "value": "2026-09-10"}],
                    "date_error": None, "read_status": "read"}  # fmt: skip
        return {"published_at": None, "updated_at": None, "date_status": "unknown", "date_evidence": [],
                "date_error": "timed out", "read_status": "failed"}  # fmt: skip

    stats = rechecks.recheck_with(conn, "recheck-1", verify)
    assert stats["state"] == "partial"
    assert stats["checked"] == 2
    assert stats["failed"] == 1
    assert stats["retained"] == 1
    assert stats["in_range"] == 2
    items = registry.materials_filtered(conn, "", 0, "")["items"]

    def got(id: str) -> dict[str, Any]:
        return next(row for row in items if row["id"] == id)

    assert got(unknown)["published_at"] == "2026-09-10"
    kept = got(known)
    assert kept["published_at"] == "2026-09-01"
    assert kept["date_status"] == "in_range"
    assert kept["date_check_status"] == "failed"
    assert kept["date_error"] == "timed out"
    assert kept["date_evidence_run_id"] == "original-search"
    assert kept["date_check_run_id"] == "recheck-1"
    assert kept["date_evidence"][0]["value"] == "2026-09-01"
    for row in items:
        assert row["first_seen"] == "first-discovery"
        assert row["last_seen"] == "last-search"
        assert row["query"] == "Original query"
        assert row["search_date_hint"] == "Original search date hint"
        assert row["run_id"] == "original-search"
        assert row["start_date"] == p.start_date
    previous, result = conn.execute(
        "SELECT previous_content,result FROM search_date_checks WHERE material_id=?1", (known,)
    ).fetchone()
    assert jsonfmt.loads(previous)["published_at"] == "2026-09-01"
    assert jsonfmt.loads(result)["published_at"] is None
    assert count(conn, "SELECT count(*) FROM search_hits") == 0
    assert count(conn, "SELECT count(*) FROM search_requests") == 0
    assert registry.job(conn, "recheck-1")["stats"]["checked"] == 2  # saved after each material
    conn.execute("UPDATE source_jobs SET state='partial' WHERE id='recheck-1'")
    recheck_row(conn, "recheck-2", p.id, None)
    rechecks.recheck_with(
        conn, "recheck-2", lambda *_: {"published_at": None, "date_status": "unknown", "date_error": "offline", "read_status": "failed"}
    )
    assert count(conn, "SELECT count(*) FROM search_date_checks WHERE checked_at IS NOT NULL") == 4


def test_recheck_batches_reach_every_material_and_reject_other_plan_cursors(
    saved: tuple[sqlite3.Connection, SearchPlan],
) -> None:
    conn, p = saved
    other = changed(p, id="other-plan")
    search.save(conn, other, None, None)
    foreign = material(conn, other, "foreign", None)
    for i in range(45):
        material(conn, p, f"article-{i}", None)
    for cursor in [foreign, "not-a-cursor", "a" * 64]:
        with pytest.raises(HttpError):
            search.validate_recheck(conn, p.id, cursor)
    after: str | None = None
    seen: set[str] = set()
    for i, expected in enumerate([20, 20, 5]):
        id = f"batch-{i}"
        recheck_row(conn, id, p.id, after)

        def verify(url: str, *_: str) -> dict[str, Any]:
            assert url not in seen
            seen.add(url)
            return {"published_at": None, "date_status": "unknown", "date_error": None, "read_status": "read"}

        stats = rechecks.recheck_with(conn, id, verify)
        assert stats["checked"] == expected
        assert stats["total"] == 45
        assert stats["remaining"] == 45 - len(seen)
        after = stats["next_cursor"]
        assert (after is not None) == (i < 2)
        conn.execute("UPDATE source_jobs SET state='succeeded' WHERE id=?1", (id,))
    assert len(seen) == 45


def test_fresh_search_does_not_erase_verified_date_on_failure_or_twenty_check_limit(
    saved: tuple[sqlite3.Connection, SearchPlan],
) -> None:
    conn, p = saved
    target = raw({"results": [{"url": "https://news.example/target", "title": "Target headline"}]})
    run_row(conn, "verified", p)
    runner.run_with(conn, "verified", p, lambda *_: target, lambda *_: {
        "published_at": "2026-10-12", "date_status": "in_range",
        "date_evidence": [{"field": "published", "value": "2026-10-12"}], "date_error": None, "read_status": "read"})  # fmt: skip
    run_row(conn, "uncapped-search", p)

    def fetch(_: SearchPlan, query: str) -> bytes:
        if query == p.queries[0]:
            return raw({"results": [{"url": f"https://news.example/new-{i}", "title": "New headline"} for i in range(20)]})
        return target

    runner.run_with(conn, "uncapped-search", p, fetch, lambda *_: {
        "published_at": None, "date_status": "unknown", "date_error": None, "read_status": "read"})  # fmt: skip
    id = db.sha256(f"search:{p.id}\nhttps://news.example/target")

    def read() -> dict[str, Any]:
        value: dict[str, Any] = jsonfmt.loads(conn.execute("SELECT content FROM search_materials WHERE id=?1", (id,)).fetchone()[0])
        return value

    assert read()["published_at"] == "2026-10-12"
    assert read()["date_check_status"] == "not_checked"
    assert read()["date_evidence_run_id"] == "verified"
    run_row(conn, "failed-read", p)
    runner.run_with(conn, "failed-read", p, lambda *_: target, lambda *_: {
        "published_at": None, "date_status": "unknown", "date_error": "HTTP 503", "read_status": "failed"})  # fmt: skip
    assert read()["published_at"] == "2026-10-12"
    assert read()["date_check_status"] == "failed"
    assert read()["date_evidence_run_id"] == "verified"
    observed = conn.execute("SELECT content FROM search_hits WHERE run_id='failed-read' AND query_index=0").fetchone()[0]
    assert jsonfmt.loads(observed)["published_at"] is None
    # Stored as compact JSON with sorted keys.
    stored = conn.execute("SELECT content FROM search_materials WHERE id=?1", (id,)).fetchone()[0]
    assert stored == jsonfmt.dumps(jsonfmt.loads(stored))
    assert list(jsonfmt.loads(stored)) == sorted(jsonfmt.loads(stored))


def test_verify_date_marks_unread_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    from isbe import articles

    def fake(url: str, start: str, end: str) -> articles.DateCheck:
        return articles.DateCheck(date_error="original-page URL is not public") if "bad" in url else articles.DateCheck()

    monkeypatch.setattr(articles, "verify", fake)
    assert runner.verify_date("https://bad.example/", "2026-10-01", "2026-10-02") == {
        "published_at": None, "updated_at": None, "date_status": "unknown", "date_evidence": [],
        "date_error": "original-page URL is not public", "read_status": "failed",
    }  # fmt: skip
    assert runner.verify_date("https://ok.example/", "2026-10-01", "2026-10-02")["read_status"] == "read"


def test_started_search_runs_the_saved_plan(data: Path, serve, monkeypatch: pytest.MonkeyPatch) -> None:
    body = raw({"results": [{"url": "https://news.example/a", "title": "Headline", "publishedDate": "2026-10-15"}]})
    base = serve(lambda req: serve.reply(req, 200, body))
    (data / "settings.json").write_text(jsonfmt.dumps({"SEARXNG_URL": base}))
    monkeypatch.setattr(runner, "verify_date", lambda *_: {"published_at": "2026-10-15", "date_status": "in_range", "date_error": None, "read_status": "read"})
    conn = db.connect(data / "isbe.db")
    try:
        p = changed(plan(), queries=["Shanghai ESG"])
        search.save(conn, p, None, None)
        assert registry.start(data, registry.Search(p), "run-1")["state"] == "running"
        assert conn.execute("SELECT config FROM search_runs WHERE id='run-1'").fetchone()[0] == p.dumps()
        for _ in range(100):
            if registry.recover(data) and registry.job(conn, "run-1")["state"] != "running":
                break
            time.sleep(0.05)
        done = registry.job(conn, "run-1")
        assert done["state"] == "succeeded", done
        assert done["stats"]["new"] == 1
        assert search.get(conn, p.id)["last_status"] == "succeeded"
        assert search.get(conn, p.id)["item_count"] == 1
    finally:
        conn.close()
