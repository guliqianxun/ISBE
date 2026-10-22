"""The material list marks duplicates and `/materials/<id>/timeline`
shows every appearance of the article, newest first."""

import sqlite3

from web_sources_helpers import config, request, send

from isbe.web.console import Console


def stored(conn: sqlite3.Connection, id: str, source: str, url: str, *versions: tuple[str, str]) -> None:
    conn.execute(
        "INSERT INTO source_materials(id,source_id,url,current_hash,first_seen,last_seen) VALUES "
        "(?1,?2,?3,?4,?5,?6)",
        (id, source, url, versions[-1][0], versions[0][1], versions[-1][1]),
    )
    for hash, at in versions:
        conn.execute(
            "INSERT INTO source_material_versions(material_id,hash,content,fetch_hash,collected_at) VALUES "
            "(?1,?2,?3,'fetch',?4)",
            (id, hash, f'{{"kind":"rss","title":"Notice {hash}"}}', at),
        )


def test_material_routes_mark_duplicates_and_answer_the_timeline(app: Console, conn: sqlite3.Connection) -> None:
    for id in ["city", "other"]:
        assert request(app, "POST", "/api/sources", {"config": config(id)})[0] == 200
    stored(conn, "a", "city", "https://example.org/n?id=1&utm_source=rss", ("a1", "2026-10-15T08:00:00Z"))
    stored(conn, "b", "other", "https://www.example.org/n?id=1", ("b1", "2026-10-14T08:00:00Z"), ("b2", "2026-10-16T08:00:00Z"))
    stored(conn, "c", "other", "https://example.org/n?id=2", ("c1", "2026-10-17T08:00:00Z"))

    code, listed = request(app, "GET", "/api/sources/materials?source=&offset=0")
    assert code == 200
    assert {item["id"]: item["duplicates"] for item in listed["items"]} == {"a": 1, "b": 1, "c": 0}
    assert listed["counts"] == {"hits": 0, "materials": 3, "versions": 4, "articles": 2}
    code, other = request(app, "GET", "/api/sources/materials?source=other&offset=0")
    assert other["counts"] == {"hits": 0, "materials": 2, "versions": 3, "articles": 2}
    # Duplicates count across sources, whatever the filter.
    assert {item["id"]: item["duplicates"] for item in other["items"]} == {"b": 1, "c": 0}

    code, timeline = request(app, "GET", "/api/sources/materials/a/timeline")
    assert code == 200
    assert timeline["identity"] == {"kind": "url", "key": "example.org/n?id=1"}
    assert [(e["at"], e["material_id"], e["source_name"], e["title"]) for e in timeline["events"]] == [
        ("2026-10-16T08:00:00Z", "b", "City news", "Notice b2"),
        ("2026-10-15T08:00:00Z", "a", "City news", "Notice a1"),
        ("2026-10-14T08:00:00Z", "b", "City news", "Notice b1"),
    ]
    assert timeline["counts"] == {"hits": 0, "materials": 2, "versions": 3}
    assert [m["id"] for m in timeline["materials"]] == ["b", "a"]

    assert request(app, "GET", "/api/sources/materials/absent/timeline") == (404, {"error": "Material not found"})
    for path in ["/api/sources/materials/a", "/api/sources/materials//timeline", "/api/sources/materials/a.b/timeline"]:
        assert request(app, "GET", path) == (404, {"error": "not found"}), path
    status, _, _ = send(app, "GET", "/api/sources/materials/a/timeline", host="attacker.example:8080")
    assert status == 403
