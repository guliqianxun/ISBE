"""Duplicate materials grouped by article identity into a timeline, newest first,
with nothing merged in storage."""

import sqlite3
from typing import Any

import pytest
from registry_helpers import count, plan, source

from isbe import search
from isbe.core import jsonfmt
from isbe.core.errors import HttpError
from isbe.search import SearchPlan, runner
from isbe.sources import registry
from isbe.sources.identity import Identity, identity
from isbe.sources.reader import Entry, ReadResult


def test_a_doi_in_the_address_is_the_identity() -> None:
    doi = Identity("doi", "10.1016/j.jclepro.2024.141234")
    for url in [
        "https://doi.org/10.1016/J.JCLEPRO.2024.141234",
        "http://dx.doi.org/10.1016/j.jclepro.2024.141234",
        "https://www.sciencedirect.example/doi/abs/10.1016/j.jclepro.2024.141234",
        "https://journal.example/doi/full/10.1016%2Fj.jclepro.2024.141234",
        "https://repo.example/view?doi=10.1016/j.jclepro.2024.141234&utm_source=x",
    ]:
        assert identity(url) == doi, url
    assert identity("https://link.springer.example/article/10.1007/s11356-024-1") == Identity(
        "doi", "10.1007/s11356-024-1"
    )
    # A number that only looks like a DOI prefix, outside a DOI position, is part of the address.
    assert identity("https://news.example/10.2024/report").kind == "url"
    assert identity("https://doi.org/not-a-doi").kind == "url"


def test_listed_sites_are_matched_by_their_own_article_id() -> None:
    shared = (
        "https://mp.weixin.qq.com/s?__biz=MzA5NjUwODAwNA==&mid=2649613970&idx=3"
        "&sn=9e684f9394509a0d9887e67a33a50323&chksm=89b9&scene=21#wechat_redirect"
    )
    again = "https://mp.weixin.qq.com/s?idx=3&mid=2649613970&__biz=MzA5NjUwODAwNA==&sn=other"
    assert identity(shared) == identity(again) == Identity(
        "site", "mp.weixin.qq.com:MzA5NjUwODAwNA==:2649613970:3"
    )
    # Another position in the same message is another article; a short link stays an address.
    assert identity(shared.replace("idx=3", "idx=2")) != identity(shared)
    assert identity("https://mp.weixin.qq.com/s/AbCdEf").kind == "url"
    assert identity("https://www.jiemian.com/article/15183381.html") == identity(
        "https://m.jiemian.com/article/15183381.html"
    ) == Identity("site", "jiemian.com:article:15183381")
    assert identity("https://www.jiemian.com/article/15183382.html") != identity(
        "https://m.jiemian.com/article/15183381.html"
    )


def test_tracking_is_stripped_and_meaningful_parameters_kept() -> None:
    plain = identity("https://news.example/a?id=7&page=2")
    for url in [
        "https://NEWS.example/a?page=2&id=7",
        "http://www.news.example:80/a/?id=7&page=2&utm_source=wechat&UTM_Medium=x#top",
        "https://news.example:443/a?spm=1.2&id=7&from=timeline&page=2&fbclid=abc&isappinstalled=0",
    ]:
        assert identity(url) == plain, url
    assert plain == Identity("url", "news.example/a?id=7&page=2")
    assert identity("https://news.example/a?id=8&page=2") != plain
    assert identity("https://news.example:8443/a?id=7&page=2") != plain
    assert identity("https://news.example/A?id=7&page=2") != plain
    assert identity("https://news.example") == identity("https://news.example/") == Identity("url", "news.example/")
    assert identity("not a url#x") == Identity("url", "not a url")


def response(url: str, title: str) -> ReadResult:
    return ReadResult(
        entries=[Entry(url, title, "summary", None, None)],
        response_url="https://example.com/feed",
        content_type="application/rss+xml",
        raw=title.encode(),
        truncated=False,
    )


def searched(conn: sqlite3.Connection, run_id: str, p: SearchPlan, rows: list[dict[str, Any]], at: str) -> None:
    conn.execute(
        "INSERT INTO source_jobs(id,fingerprint,operation,state,created_at) "
        "VALUES (?1,'fingerprint','search','running','now')",
        (run_id,),
    )
    search.snapshot(conn, run_id, p)
    body = jsonfmt.dumps({"results": rows}).encode()
    runner.run_with(conn, run_id, p, lambda _p, _q: body, lambda *_: {"published_at": None, "date_status": "unknown"})
    conn.execute("UPDATE search_runs SET created_at=?2 WHERE id=?1", (run_id, at))
    conn.execute("UPDATE source_jobs SET state='succeeded' WHERE id=?1", (run_id,))


@pytest.fixture
def duplicated(conn: sqlite3.Connection) -> sqlite3.Connection:
    """One article found by two plans (two runs of the first) and an RSS source, at
    three addresses; another article sharing its title; an unrelated one."""
    first = plan().model_copy(update={"queries": ["Shanghai ESG"]})
    second = first.model_copy(update={"id": "green-finance", "name": "Green finance"})
    for p in (first, second):
        search.save(conn, p, None, None)
    article = {"url": "https://news.example/esg?id=1&utm_source=feed", "title": "ESG rules"}
    namesake = {"url": "https://other.example/esg", "title": "ESG rules"}
    searched(conn, "run-1", first, [article, namesake], "2026-10-15T08:00:00Z")
    searched(conn, "run-2", first, [article], "2026-10-17T08:00:00Z")
    searched(conn, "run-3", second, [{**article, "url": "https://www.news.example/esg?id=1#top"}], "2026-10-16T08:00:00Z")
    registry.save(conn, source("city"), None, None)
    registry.record(conn, source("city"), response("https://news.example/esg?id=1", "ESG rules"))
    registry.record(conn, source("city"), response("https://news.example/esg?id=1", "ESG rules, revised"))
    registry.record(conn, source("city"), response("https://unrelated.example/x", "Other"))
    return conn


def versions_at(conn: sqlite3.Connection, *times: str) -> None:
    rows = conn.execute(
        "SELECT v.rowid FROM source_material_versions v JOIN source_materials m ON m.id=v.material_id "
        "WHERE m.url='https://news.example/esg?id=1' ORDER BY v.rowid"
    ).fetchall()
    for (rowid,), at in zip(rows, times, strict=True):
        conn.execute("UPDATE source_material_versions SET collected_at=?2 WHERE rowid=?1", (rowid, at))


def test_one_article_from_two_plans_and_a_feed_is_one_timeline_newest_first(
    duplicated: sqlite3.Connection,
) -> None:
    conn = duplicated
    versions_at(conn, "2026-10-14T08:00:00Z", "2026-10-18T08:00:00Z")
    # Nothing is merged in storage: three addresses stay three materials, and a
    # repeated run adds hits, not materials.
    assert count(conn, "SELECT count(*) FROM search_materials") == 3
    assert count(conn, "SELECT count(*) FROM source_materials") == 2
    listed = registry.materials_filtered(conn, "", 0, "")
    by_url = {item["url"]: item for item in listed["items"]}
    article = by_url["https://news.example/esg?id=1&utm_source=feed"]
    assert article["identity"] == {"kind": "url", "key": "news.example/esg?id=1"}
    assert article["duplicates"] == 2
    assert by_url["https://news.example/esg?id=1"]["duplicates"] == 2
    assert by_url["https://other.example/esg"]["duplicates"] == 0
    assert by_url["https://unrelated.example/x"]["duplicates"] == 0
    # Hits, materials, versions and articles are different numbers.
    assert listed["counts"] == {"hits": 4, "materials": 5, "versions": 6, "articles": 3}
    assert registry.materials_filtered(conn, "search:shanghai-esg", 0, "")["counts"] == {
        "hits": 3, "materials": 2, "versions": 2, "articles": 2,
    }

    timeline = registry.timeline(conn, article["id"])
    assert timeline["identity"] == article["identity"]
    assert timeline["counts"] == {"hits": 3, "materials": 3, "versions": 4}
    events = [(e["at"], e["kind"], e.get("plan_id") or e.get("source_id"), e["url"]) for e in timeline["events"]]
    assert events == [
        ("2026-10-18T08:00:00Z", "source_version", "city", "https://news.example/esg?id=1"),
        ("2026-10-17T08:00:00Z", "search_hit", "shanghai-esg", "https://news.example/esg?id=1&utm_source=feed"),
        ("2026-10-16T08:00:00Z", "search_hit", "green-finance", "https://www.news.example/esg?id=1"),
        ("2026-10-15T08:00:00Z", "search_hit", "shanghai-esg", "https://news.example/esg?id=1&utm_source=feed"),
        ("2026-10-14T08:00:00Z", "source_version", "city", "https://news.example/esg?id=1"),
    ]
    # The revision is kept with its own title; a hit names its plan, query and rank.
    assert [e["title"] for e in timeline["events"] if e["kind"] == "source_version"] == ["ESG rules, revised", "ESG rules"]
    hit = timeline["events"][1]
    assert (hit["plan_name"], hit["query"], hit["rank"]) == ("Shanghai ESG", "Shanghai ESG", 1)
    # Every member's timeline is the same; the namesake is not in it.
    for member in timeline["materials"]:
        assert registry.timeline(conn, member["id"])["events"] == timeline["events"]
    assert "https://other.example/esg" not in {m["url"] for m in timeline["materials"]}
    namesake = registry.timeline(conn, by_url["https://other.example/esg"]["id"])
    assert namesake["counts"] == {"hits": 1, "materials": 1, "versions": 1}
    with pytest.raises(HttpError) as missing:
        registry.timeline(conn, "absent")
    assert missing.value.status == 404
