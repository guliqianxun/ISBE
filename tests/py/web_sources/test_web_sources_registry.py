"""The source registry routes: configuration, import, catalog,
batch, guards, materials, and a collection job started against a local site."""

import sqlite3
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest
from web_sources_helpers import TOKEN, config, count, request, send, wait_for

from isbe.sources import registry
from isbe.web.console import Console


def test_source_configuration_create_edit_conflicts_and_export(app: Console) -> None:
    code, empty = request(app, "GET", "/api/sources")
    assert code == 200
    assert empty == {
        "sources": [], "total": 0, "offset": 0, "limit": 50,
        "summary": {"total": 0, "enabled": 0, "disabled": 0, "failed": 0, "empty": 0, "never": 0},
        "facets": {"sites": [], "topics": []}, "csrf_token": TOKEN,
    }
    code, created = request(app, "POST", "/api/sources", {"config": config("city")})
    assert code == 200
    assert created["revision"] == "1"
    assert created["origin"] == "manual"
    assert created["config"]["max_items"] == 100
    assert created["config"]["enabled"] is False
    assert created["last_checked"] is None
    assert request(app, "POST", "/api/sources", {"config": config("city")})[0] == 409
    updated = {**created["config"], "name": "Revised city source", "enabled": True}
    code, saved = request(app, "PUT", "/api/sources/city", {"config": updated, "revision": "1"})
    assert code == 200
    assert saved["revision"] == "2"
    assert request(app, "PUT", "/api/sources/city", {"config": config("city"), "revision": "1"})[0] == 409
    assert request(app, "PUT", "/api/sources/city", {"config": config("other"), "revision": "2"})[0] == 400
    assert request(app, "PUT", "/api/sources/city", {"config": updated})[0] == 409
    code, exported = request(app, "GET", "/api/sources/export")
    assert code == 200
    assert exported == {"sources": [updated]}


def test_source_import_rejects_whole_batch_on_conflict_or_invalid_config(app: Console) -> None:
    assert request(app, "POST", "/api/sources", {"config": config("existing")})[0] == 200
    for sources, status in [
        ([config("fresh"), config("existing")], 409),
        ([config("fresh"), config("fresh")], 409),
        ([config("fresh"), {"id": "bad", "name": "Invalid"}], 400),
        ([], 400),
    ]:
        assert request(app, "POST", "/api/sources/import", {"sources": sources})[0] == status
        _, listed = request(app, "GET", "/api/sources")
        assert len(listed["sources"]) == 1
        assert listed["sources"][0]["config"]["id"] == "existing"
    code, imported = request(app, "POST", "/api/sources/import", {"sources": [config("fresh"), config("second")]})
    assert code == 200
    assert imported == {"imported": 2}


def test_source_catalog_pages_and_loads_reserved_ids_on_demand(app: Console) -> None:
    configs = [config(f"city-{i:03}") for i in range(55)]
    configs += [config(id) for id in ["config", "jobs", "export", "materials"]]
    assert request(app, "POST", "/api/sources/import", {"sources": configs})[0] == 200
    code, first = request(app, "GET", "/api/sources")
    assert code == 200
    assert len(first["sources"]) == 50
    assert first["total"] == 59
    assert first["summary"]["total"] == 59
    assert first["sources"][0]["site"] == "example.org"
    _, second = request(app, "GET", "/api/sources?offset=50")
    assert len(second["sources"]) == 9
    assert second["offset"] == 50
    _, filtered = request(app, "GET", "/api/sources?q=city-053&site=example.org&status=disabled")
    assert filtered["total"] == 1
    assert filtered["sources"][0]["config"]["id"] == "city-053"
    assert filtered["summary"]["total"] == 59
    for id in ["config", "jobs", "export", "materials", "city-054"]:
        code, detail = request(app, "GET", f"/api/sources/config/{id}")
        assert code == 200
        assert detail["config"]["id"] == id
        assert detail["revision"] == "1"
    assert request(app, "GET", "/api/sources/config/missing")[0] == 404
    for query in ["offset=-1", "offset=oops", "offset=18446744073709551616", "status=oops", f"q={'x' * 201}"]:
        assert request(app, "GET", f"/api/sources?{query}")[0] == 400, query


def test_offsets_parse_as_unsigned_integers(app: Console) -> None:
    # An offset is a plain non-negative integer: a percent-encoded leading `+` is accepted, nothing
    # else is; the largest offset is 2**63 - 1.
    for query, status in [("offset=%2B1", 200), ("offset=", 400), ("offset=+1", 400), ("offset=1_0", 400),
                          ("offset=%D9%A1", 400), (f"offset={(1 << 63) - 1}", 200), (f"offset={1 << 63}", 400)]:
        assert request(app, "GET", f"/api/sources?{query}")[0] == status, query
        code, value = request(app, "GET", f"/api/sources/materials?{query}")
        assert code == status, query
        if status == 400:
            assert value == {"error": "Invalid material offset"}
    assert request(app, "GET", "/api/sources?offset=x")[1] == {"error": "Invalid source offset"}


def test_source_batch_api_requires_revisions_and_rolls_back_conflicts(app: Console, conn: sqlite3.Connection) -> None:
    request(app, "POST", "/api/sources/import", {"sources": [config("a"), config("b")]})
    for body, status in [
        ({"sources": [{"id": "a", "revision": "1"}, {"id": "b", "revision": "stale"}], "enabled": True}, 409),
        ({"sources": [{"id": "a"}], "enabled": True}, 400),
        ({"sources": [{"id": "a", "revision": "1"}, {"id": "a", "revision": "1"}], "enabled": True}, 400),
        ({"sources": [{"id": "a", "revision": "1"}], "enabled": True, "extra": True}, 400),
    ]:
        assert request(app, "POST", "/api/sources/batch", body)[0] == status
        assert registry.get(conn, "a")["revision"] == "1"
    code, result = request(
        app, "POST", "/api/sources/batch",
        {"sources": [{"id": "a", "revision": "1"}, {"id": "b", "revision": "1"}], "enabled": True},
    )
    assert code == 200
    assert result == {"updated": 2}
    assert registry.get(conn, "a")["config"]["enabled"] is True


GUARDS = [
    ("POST", "/api/sources/article", "localhost:8080", "", "application/json", 403),
    ("POST", "/api/sources/search", "localhost:8080", "", "application/json", 403),
    ("POST", "/api/sources/search/run", "localhost:8080", "", "application/json", 403),
    ("POST", "/api/sources/search/plan/recheck", "localhost:8080", "", "application/json", 403),
    ("POST", "/api/sources/search/plan/weekly", "localhost:8080", "", "application/json", 403),
    ("GET", "/api/sources", "attacker.example:8080", "", "", 403),
    ("POST", "/api/sources", "localhost:8080", "wrong", "application/json", 403),
    ("POST", "/api/sources/discover", "localhost:8080", "", "application/json", 403),
    ("POST", "/api/sources/batch", "localhost:8080", "", "application/json", 403),
    ("POST", "/api/sources/city/collect", "localhost:8080", "", "application/json", 403),
    ("POST", "/api/sources/import", "localhost:8080", TOKEN, "text/plain", 415),
    ("DELETE", "/api/sources/city", "localhost:8080", TOKEN, "application/json", 405),
    ("GET", "/api/sources/missing", "localhost:8080", "", "", 404),
]
UNSUPPORTED = [
    ("/api/sources/article",
     {"topic": "news", "period": "2020-W42-media", "id": "one", "url": "http://127.0.0.1/private"}),
    ("/api/sources/article", {"topic": "news", "period": "2020-W42-media"}),
    ("/api/sources/article", {"topic": "news", "period": "", "id": "one"}),
    ("/api/sources/article", {"topic": "../outside", "period": "2020-W42-media", "id": "one"}),
    ("/api/sources/search", {"config": {}, "unexpected": True}),
    ("/api/sources/search/run", {"config": {}, "request_id": "req", "unexpected": True}),
    ("/api/sources/search/plan/recheck", {"request_id": "req", "unexpected": True}),
    ("/api/sources/search/plan/weekly", {"topic_id": "news", "week_end": "2026-10-18", "unexpected": True}),
    ("/api/sources", {"config": config("city"), "unexpected": True}),
    ("/api/sources/discover", {"url": "https://example.org/", "topic": "ESG", "request_id": "req"}),
    ("/api/sources/city/collect", {"request_id": "req", "unexpected": True}),
]


def test_source_routes_require_csrf_host_json_and_supported_requests(app: Console) -> None:
    for method, path, host, token, content_type, expected in GUARDS:
        status, _, _ = send(app, method, path, raw=b"{}", host=host, token=token, content_type=content_type)
        assert status == expected, path
    for path, body in UNSUPPORTED:
        assert request(app, "POST", path, body)[0] == 400, (path, body)
    assert request(app, "GET", "/api/sources/jobs") == (200, {"jobs": []})


def test_unmatched_routes_answer(app: Console) -> None:
    assert request(app, "DELETE", "/api/sources/city") == (405, {"error": "Method not allowed"})
    assert request(app, "POST", "/api/sources/city", {}) == (405, {"error": "Method not allowed"})
    for method, path in [("GET", "/api/sources/missing"), ("GET", "/api/sources/config/"),
                         ("GET", "/api/sources/config/Bad"), ("PUT", "/api/sources"),
                         ("PUT", "/api/sources/a/b"), ("POST", "/api/sources/collect"),
                         ("POST", "/api/sources/search/x/collect"), ("POST", "/api/sources/search/weekly"),
                         ("POST", "/api/sources/search//recheck")]:
        assert request(app, method, path, {}) == (404, {"error": "not found"}), (method, path)


def test_source_materials_filter_paginate_and_preserve_dates_and_versions(
    app: Console, conn: sqlite3.Connection
) -> None:
    for id in ["city", "other"]:
        assert request(app, "POST", "/api/sources", {"config": config(id)})[0] == 200
    for i in range(52):
        id = f"item-{i:02}"
        conn.execute(
            "INSERT INTO source_materials(id,source_id,url,current_hash,first_seen,last_seen) VALUES "
            "(?1,?2,?3,'current','2026-10-17T08:00:00Z','2026-10-18T08:00:00Z')",
            (id, "other" if i == 51 else "city", f"https://example.org/{i}"),
        )
        content = (
            f'{{"kind":"rss","published_at":null,"summary":"Source summary","title":"Notice {i}",'
            '"updated_at":"2026-10-16T08:00:00Z"}'
        )
        conn.execute(
            "INSERT INTO source_material_versions(material_id,hash,content,fetch_hash,collected_at) VALUES "
            "(?1,'current',?2,'fetch','2026-10-18T08:00:00Z')",
            (id, content),
        )
    conn.execute(
        "INSERT INTO source_material_versions(material_id,hash,content,fetch_hash,collected_at) VALUES "
        "('item-00','earlier','{}','earlier-fetch','2026-10-17T08:00:00Z')"
    )
    code, first = request(app, "GET", "/api/sources/materials?source=city&offset=0")
    assert code == 200
    assert first["total"] == 51
    assert first["limit"] == 50
    assert len(first["items"]) == 50
    item = first["items"][0]
    assert item["source_name"] == "City news"
    assert item["published_at"] is None
    assert item["updated_at"] == "2026-10-16T08:00:00Z"
    assert item["first_seen"] == "2026-10-17T08:00:00Z"
    assert item["last_seen"] == "2026-10-18T08:00:00Z"
    assert item["version_count"] == 2
    _, last = request(app, "GET", "/api/sources/materials?source=city&offset=50")
    assert len(last["items"]) == 1
    assert last["items"][0]["id"] == "item-50"
    _, empty = request(app, "GET", "/api/sources/materials?source=missing&offset=0")
    assert empty["total"] == 0
    for offset in ["-1", "no", "18446744073709551616"]:
        assert request(app, "GET", f"/api/sources/materials?offset={offset}")[0] == 400


NEWS = (
    '<rss version="2.0"><channel><title>News</title><link>https://example.org</link><description>news'
    "</description><item><title>Public policy update</title><link>https://example.org/article</link>"
    "</item></channel></rss>"
)


def test_collect_route_runs_the_job_and_replays_its_request_id(
    app: Console, conn: sqlite3.Connection, root: Path, serve, local_sites: None
) -> None:
    def feed(req: BaseHTTPRequestHandler) -> None:
        serve.reply(req, 200, NEWS, {"Content-Type": "application/rss+xml"})

    port = serve(feed).rsplit(":", 1)[1]
    site = {**config("city"), "url": f"http://feed.test:{port}/feed"}
    assert request(app, "POST", "/api/sources", {"config": site})[0] == 200
    assert request(app, "POST", "/api/sources/missing/collect", {"request_id": "nope"})[0] == 404
    code, started = request(app, "POST", "/api/sources/city/collect", {"request_id": "web-1"})
    assert code == 200
    assert started["job"]["operation"] == "collect"
    assert started["job"]["state"] == "running"
    done = wait_for(conn, root, "web-1")
    assert done["state"] == "succeeded"
    assert done["stats"]["new"] == 1
    code, replayed = request(app, "POST", "/api/sources/city/collect", {"request_id": "web-1"})
    assert (code, replayed["job"]) == (200, done)
    code, other = request(app, "POST", "/api/sources/discover",
                          {"url": "https://example.org/", "topic": "ESG", "auto_enable": False, "request_id": "web-1"})
    assert (code, other) == (409, {"error": "This request ID belongs to a different operation"})
    _, listed = request(app, "GET", "/api/sources/jobs")
    assert [job["id"] for job in listed["jobs"]] == ["web-1"]
    _, items = request(app, "GET", "/api/sources/materials?source=city")
    assert [item["url"] for item in items["items"]] == ["https://example.org/article"]


@pytest.mark.parametrize("path", ["/api/sources", "/api/sources/jobs"])
def test_every_request_recovers_jobs_a_stopped_process_left_running(
    app: Console, conn: sqlite3.Connection, path: str
) -> None:
    conn.execute(
        "INSERT INTO source_jobs(id,fingerprint,operation,state,created_at) "
        "VALUES ('stale','x','collect','running','2026-10-18T08:00:00Z')"
    )
    assert request(app, "GET", path)[0] == 200
    assert count(conn, "SELECT count(*) FROM source_jobs WHERE state='interrupted'") == 1
