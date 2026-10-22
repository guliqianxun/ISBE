"""The source search routes: plans, runs, rechecks and the media week."""

import os
import sqlite3
from pathlib import Path

import pytest
from web_sources_helpers import TOKEN, count, request, wait_for, weekly_fixture

from isbe import search
from isbe.core import locks, settings
from isbe.reports import library, media
from isbe.search import Provider, SearchPlan
from isbe.web import views
from isbe.web.console import Console

PLAN = {"id": "shanghai-news", "name": "Shanghai news", "topic": "上海 ESG 与绿色金融", "provider": "searxng",
        "queries": ["上海 ESG", "Shanghai green finance"], "start_date": "2026-10-01", "end_date": "2026-10-16",
        "language": "zh-CN", "max_results": 10}


def test_search_plans_save_with_revisions_and_missing_service_does_not_start_jobs(
    app: Console, conn: sqlite3.Connection
) -> None:
    code, empty = request(app, "GET", "/api/sources/search")
    assert code == 200
    assert empty["plans"] == []
    assert empty["csrf_token"] == TOKEN
    assert isinstance(empty["providers"]["searxng"]["configured"], bool)
    code, saved = request(app, "POST", "/api/sources/search", {"config": PLAN})
    assert code == 200
    assert saved["revision"] == "1"
    assert request(app, "POST", "/api/sources/search", {"config": PLAN})[0] == 409
    changed = {**PLAN, "name": "Revised Shanghai news"}
    assert request(app, "POST", "/api/sources/search", {"config": changed, "revision": "1"})[0] == 200
    assert request(app, "POST", "/api/sources/search", {"config": PLAN, "revision": "1"})[0] == 409
    for field, value in [("start_date", "2026-13-01"), ("end_date", "2025-01-01"), ("queries", []),
                         ("max_results", 21), ("provider", "google")]:
        invalid = {**changed, field: value}
        assert request(app, "POST", "/api/sources/search", {"config": invalid, "revision": "2"})[0] == 400, field
    run = {"config": PLAN, "request_id": "search-conflict"}
    assert request(app, "POST", "/api/sources/search/run", run)[0] == 409
    if os.environ.get("SEARXNG_URL") is None:
        missing = {"config": changed, "request_id": "missing-search-service"}
        code, _ = request(app, "POST", "/api/sources/search/run", missing)
        assert code == 400
        assert count(conn, "SELECT count(*) FROM source_jobs") == 0
    assert request(app, "GET", "/api/sources/materials?date_status=bogus")[0] == 400
    _, items = request(app, "GET", "/api/sources/materials?date_status=unknown")
    assert items["items"] == []


def test_search_listing_uses_the_settings_a_run_would_use(
    app: Console, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The listing reads the workspace's saved settings, not the process cache.
    monkeypatch.setattr(settings, "_saved", {"SEARXNG_URL": "https://cached.example/"})
    assert request(app, "GET", "/api/sources/search")[1]["providers"]["searxng"]["configured"] is False
    (root / "settings.json").write_text('{"SEARXNG_URL": "https://search.example/"}')
    assert request(app, "GET", "/api/sources/search")[1]["providers"]["searxng"]["configured"] is True
    (root / "settings.json").write_text('{"SEARXNG_URL": "not a url"}')
    code, value = request(app, "GET", "/api/sources/search")
    # A broken settings file is a server error, not a request error.
    assert code == 500
    assert set(value) == {"error"}


def test_weekly_media_route_uses_saved_material_and_shared_execution_lock(
    app: Console, conn: sqlite3.Connection, root: Path
) -> None:
    weekly_fixture(conn)
    week = {"topic_id": "shanghai-esg", "week_end": "2020-10-18"}
    guard = locks.execution_lock(root)
    busy = request(app, "POST", "/api/sources/search/plan/weekly", week)
    assert busy == (409, {"error": "Another collection or report operation is running. Wait and try again."})
    guard.release()
    for invalid in [{"topic_id": "../outside", "week_end": "2020-10-18"},
                    {"topic_id": "shanghai-esg", "week_end": "2020-10-19"}]:
        assert request(app, "POST", "/api/sources/search/plan/weekly", invalid)[0] == 400
    missing = request(app, "POST", "/api/sources/search/missing/weekly", week)
    assert missing == (404, {"error": "Search plan not found"})
    code, result = request(app, "POST", "/api/sources/search/plan/weekly", week)
    assert code == 200, result
    assert result["weekly"] == 1
    assert result["materials"] == 1
    period = result["period"]
    found = library.report_file(conn, root, "shanghai-esg", period)
    assert found is not None
    assert "机构发布 ESG 报告" in found[1]
    shelf = library.library(conn, root)
    assert shelf[0]["media"] is True
    assert shelf[0]["materials"] == 1
    reader = library.report(conn, root, "shanghai-esg", period, articles=media.articles)
    assert reader is not None
    assert reader["issues"][0]["media"] is True
    assert reader["issues"][0]["materials"] == 1
    cached = '{"fetched_at":"2026-10-19T08:00:00Z","final_url":"https://example.org/article","paragraphs":["来自原文的完整段落。"]}'
    conn.execute("INSERT INTO article_text VALUES ('https://example.org/article', ?1)", (cached,))
    status, original = request(app, "POST", "/api/sources/article", {"topic": "shanghai-esg", "period": period, "id": "one"})
    assert status == 200
    assert original["cached"] is True
    assert original["paragraphs"] == ["来自原文的完整段落。"]
    for body in [{"topic": "shanghai-esg", "period": period, "id": "missing"},
                 {"topic": "shanghai-esg", "period": "missing", "id": "one"},
                 {"topic": "other-topic", "period": period, "id": "one"}]:
        assert request(app, "POST", "/api/sources/article", body) == (404, {"error": "这期周报中没有找到这篇报道。"})
    overview = views.overview(conn, root, root)
    assert overview["reports"][0]["media"] is True
    assert overview["reports"][0]["materials"] == 1
    assert reader["articles"][0]["id"] == "one"
    (root / f"reports/shanghai-esg/{period}.sources.json").write_bytes(b"{}")
    readable = library.report(conn, root, "shanghai-esg", period, articles=media.articles)
    assert readable is not None
    assert readable["html"] == reader["html"]
    assert readable["articles"] == []
    changed = request(app, "POST", "/api/sources/article", {"topic": "shanghai-esg", "period": period, "id": "one"})
    assert changed == (409, {"error": "这期周报的材料暂时无法读取，请打开来源链接查看。"})
    assert count(conn, "SELECT count(*) FROM search_materials") == 1
    assert count(conn, "SELECT count(*) FROM llm_calls") == 0
    assert count(conn, "SELECT count(*) FROM runs WHERE job='media-weekly' AND status='ok'") == 1


def test_recheck_route_validates_inputs_shares_execution_lock_and_replays_same_job(
    app: Console, conn: sqlite3.Connection, root: Path
) -> None:
    plan = SearchPlan(id="plan", name="ESG news", topic="Shanghai", provider=Provider.SEARXNG, queries=["Shanghai ESG"],
                      start_date="2026-10-01", end_date="2026-10-19", language="en", max_results=10)
    search.save(conn, plan, None, None)
    search.save(conn, plan.model_copy(update={"id": "other"}), None, None)
    assert request(app, "POST", "/api/sources/search/missing/recheck", {"request_id": "missing"})[0] == 404
    for body in [{"request_id": ""}, {"request_id": "bad", "after": "invalid"},
                 {"request_id": "bad", "after": "a" * 64}, {"request_id": "bad", "after": 3}]:
        assert request(app, "POST", "/api/sources/search/plan/recheck", body)[0] == 400, body
    guard = locks.execution_lock(root)
    assert request(app, "POST", "/api/sources/search/plan/recheck", {"request_id": "busy"})[0] == 409
    guard.release()
    code, started = request(app, "POST", "/api/sources/search/plan/recheck", {"request_id": "once"})
    assert code == 200
    assert started["job"]["operation"] == "recheck"
    wait_for(conn, root, "once")
    code, replayed = request(app, "POST", "/api/sources/search/plan/recheck", {"request_id": "once"})
    assert code == 200
    assert replayed["job"]["state"] == "succeeded"
    assert replayed["job"]["stats"]["checked"] == 0
    assert replayed["job"]["stats"]["start_date"] == "2026-10-01"
    assert count(conn, "SELECT count(*) FROM search_runs") == 1
    assert count(conn, "SELECT count(*) FROM search_requests") == 0
    assert request(app, "POST", "/api/sources/search/other/recheck", {"request_id": "once"})[0] == 409


def test_plans_take_region_institution_and_refuse_bad_query_syntax(
    app: Console, conn: sqlite3.Connection
) -> None:
    code, saved = request(app, "POST", "/api/sources/search", {"config": {**PLAN, "region": " 上海 ", "institution": "上交所"}})
    assert code == 200
    assert saved["config"]["region"] == "上海" and saved["config"]["institution"] == "上交所"
    code, listing = request(app, "GET", "/api/sources/search")
    assert listing["plans"][0]["config"] == saved["config"]
    # A plan without them is stored without the members, as older plans were.
    code, plain = request(app, "POST", "/api/sources/search", {"config": {**PLAN, "id": "plain"}})
    assert code == 200 and "region" not in plain["config"] and "institution" not in plain["config"]
    for bad, message in [({"queries": ['上海 "ESG']}, "close every quotation mark"),
                         ({"queries": ["ESG OR"]}, "OR must join two words or phrases"),
                         ({"queries": ["ESG author:Zhang"]}, "Google Scholar field"),
                         ({"region": "x" * 61}, "Region and institution allow up to 60 characters"),
                         ({"institution": 'a"b'}, "Region and institution allow up to 60 characters")]:
        code, body = request(app, "POST", "/api/sources/search", {"config": {**PLAN, "id": "bad", **bad}})
        assert code == 400 and message in body["error"], bad
    assert request(app, "POST", "/api/sources/search", {"config": {**PLAN, "id": "bad", "region": 5}})[0] == 400
    assert count(conn, "SELECT count(*) FROM search_plans") == 2
