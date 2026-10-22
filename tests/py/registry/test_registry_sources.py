"""The source registry: saving, catalog, batches, collection, discovery, job recovery and stored formats."""

import sqlite3
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest
from registry_helpers import count, source

from isbe.core import db, jsonfmt, locks, netguard
from isbe.core.errors import HttpError
from isbe.sources import registry, registry_discover
from isbe.sources.reader import Entry, Kind, ReadResult
from isbe.sources.registry import CatalogQuery, SourceRevision


def response(title: str) -> ReadResult:
    return ReadResult(
        entries=[Entry("https://example.com/a", title, "summary", None, None)],
        response_url="https://example.com/feed",
        content_type="application/rss+xml",
        raw=title.encode(),
        truncated=False,
    )


def status(call: object) -> int:
    assert callable(call)
    with pytest.raises(HttpError) as caught:
        call()
    return caught.value.status


def test_saves_check_revision_and_import_is_atomic(conn: sqlite3.Connection) -> None:
    saved = registry.save(conn, source("a"), None, None)
    assert status(lambda: registry.save(conn, source("a"), "old", "a")) == 409
    edit = source("a").model_copy(update={"enabled": False})
    registry.save(conn, edit, saved["revision"], "a")
    assert registry.get(conn, "a")["config"]["enabled"] is False
    with pytest.raises(HttpError):
        registry.import_(conn, [source("b"), source("a")])
    with pytest.raises(HttpError):
        registry.get(conn, "b")
    bad = source("c").model_copy(update={"url": "file:///secret"})
    with pytest.raises(HttpError):
        registry.save(conn, bad, None, None)


def test_catalog_pages_one_thousand_sources_and_keeps_global_facets(conn: sqlite3.Connection) -> None:
    from isbe.sources.registry_config import insert

    with db.immediate(conn):
        for i in range(1000):
            cfg = source(f"source-{i:04}").model_copy(
                update={
                    "url": f"https://site-{i % 10}.example:8040/feed",
                    "topic": f"Topic {i % 4}",
                    "enabled": i % 3 != 0,
                    "name": "Public 100%_news" if i == 7 else "Public news",
                }
            )
            insert(conn, cfg, "manual", None)
            if i % 5 != 4:
                conn.execute(
                    "UPDATE source_definitions SET last_checked='2026-10-18',last_status=?2 WHERE id=?1",
                    (cfg.id, {0: "failed", 1: "empty"}.get(i % 5, "ok")),
                )
    first = registry.catalog(conn, CatalogQuery())
    assert first["total"] == 1000
    assert len(first["sources"]) == 50
    assert first["sources"][0]["config"]["id"] == "source-0000"
    assert first["sources"][49]["config"]["id"] == "source-0049"
    assert first["sources"][0]["site"] == "site-0.example:8040"
    assert first["sources"][0]["item_count"] == 0
    assert first["summary"] == {"total": 1000, "enabled": 666, "disabled": 334, "failed": 200, "empty": 200, "never": 200}
    assert len(first["facets"]["sites"]) == 10
    assert first["facets"]["sites"][0] == {"value": "site-0.example:8040", "count": 100}
    assert first["facets"]["topics"][0] == {"value": "Topic 0", "count": 250}
    assert registry.catalog(conn, CatalogQuery(offset=50))["sources"][0]["config"]["id"] == "source-0050"
    filtered = registry.catalog(
        conn, CatalogQuery(q="PUBLIC", site="site-1.example:8040", topic="Topic 1", status="enabled")
    )
    assert filtered["total"] == 33
    assert filtered["summary"] == first["summary"]
    assert filtered["facets"] == first["facets"]
    literal = registry.catalog(conn, CatalogQuery(q="%_"))
    assert literal["total"] == 1
    assert literal["sources"][0]["config"]["id"] == "source-0007"
    assert registry.catalog(conn, CatalogQuery(q="source-0007"))["total"] == 1
    assert registry.catalog(conn, CatalogQuery(q="Topic 2"))["total"] == 250
    assert registry.catalog(conn, CatalogQuery(site="site-1.example"))["total"] == 0
    for state, expected in [("failed", 200), ("empty", 200), ("never", 200), ("disabled", 334)]:
        assert registry.catalog(conn, CatalogQuery(status=state))["total"] == expected
    end = registry.catalog(conn, CatalogQuery(offset=1000))
    assert end["sources"] == []
    assert end["total"] == 1000
    assert (end["offset"], end["limit"]) == (1000, 50)
    for bad in [CatalogQuery(status="unknown"), CatalogQuery(q="x" * 201), CatalogQuery(offset=2**64 - 1)]:
        assert status(lambda: registry.catalog(conn, bad)) == 400


def test_batch_toggle_is_atomic_and_preserves_configuration(conn: sqlite3.Connection) -> None:
    for id in ["a", "b"]:
        registry.save(conn, source(id), None, None)

    def selected(id: str, revision: str) -> SourceRevision:
        return SourceRevision(id=id, revision=revision)

    for sources, expected in [
        ([selected("a", "1"), selected("b", "old")], 409),
        ([selected("a", "1"), selected("missing", "1")], 404),
        ([selected("a", "1"), selected("a", "1")], 400),
        ([], 400),
        ([selected("a", "1")] * 51, 400),
    ]:
        assert status(lambda: registry.batch(conn, sources, False)) == expected
        assert registry.get(conn, "a")["revision"] == "1"
        assert registry.get(conn, "a")["config"]["enabled"] is True
    conn.execute(
        "INSERT INTO source_jobs(id,fingerprint,operation,state,created_at) "
        "VALUES ('running','hash','collect','running','2026-10-18')"
    )
    assert status(lambda: registry.batch(conn, [selected("a", "1")], False)) == 409
    conn.execute("UPDATE source_jobs SET state='succeeded'")
    assert registry.batch(conn, [selected("a", "1"), selected("b", "1")], False) == {"updated": 2}
    for id in ["a", "b"]:
        saved = registry.get(conn, id)
        assert saved["config"] == source(id).model_copy(update={"enabled": False}).to_json()
        assert saved["revision"] == "2"
    assert registry.batch(conn, [selected("a", "2")], True)["updated"] == 1
    assert registry.get(conn, "a")["config"]["enabled"] is True


def test_collection_preserves_unknown_dates_versions_and_each_origin(conn: sqlite3.Connection) -> None:
    for id in ["a", "b"]:
        registry.save(conn, source(id), None, None)
    assert registry.record(conn, source("a"), response("first"))["new"] == 1
    assert registry.record(conn, source("a"), response("first"))["unchanged"] == 1
    assert registry.record(conn, source("a"), response("changed"))["updated"] == 1
    registry.record(conn, source("b"), response("changed"))
    listed = registry.materials_filtered(conn, "a", 0, "")
    assert listed["total"] == 1
    assert listed["items"][0]["version_count"] == 2
    assert listed["items"][0]["title"] == "changed"
    assert listed["items"][0]["published_at"] is None
    assert isinstance(listed["items"][0]["first_seen"], str)
    assert registry.materials_filtered(conn, "", 0, "")["total"] == 2
    edit = source("a").model_copy(update={"url": "https://example.com/another-feed"})
    assert status(lambda: registry.save(conn, edit, "1", "a")) == 409
    assert count(conn, "SELECT count(*) FROM source_fetches") == 3
    for bad in [("x" * 161, 0, ""), ("", -1, ""), ("", 0, "maybe")]:
        assert status(lambda: registry.materials_filtered(conn, *bad)) == 400


def test_rows_are_stored_in_the_established_format(conn: sqlite3.Connection) -> None:
    registry.save(conn, source("a"), None, None)
    config = conn.execute("SELECT config FROM source_definitions").fetchone()[0]
    # The config is stored in field declaration order, not sorted.
    assert config == (
        '{"id":"a","name":"Public news","topic":"Shanghai","kind":"rss",'
        '"url":"https://example.com/feed","enabled":true,"link_pattern":"","max_items":100}'
    )
    registry.record(conn, source("a"), response("中文"))
    content, hash = conn.execute("SELECT content,hash FROM source_material_versions").fetchone()
    # The material content is stored with keys sorted, raw UTF-8.
    assert content == (
        '{"kind":"rss","published_at":null,"summary":"summary","title":"中文",'
        '"updated_at":null,"url":"https://example.com/a"}'
    )
    assert hash == db.sha256(content)
    material = db.sha256("a\nhttps://example.com/a")
    assert conn.execute("SELECT id FROM source_materials").fetchone()[0] == material
    assert conn.execute("SELECT body FROM source_fetches").fetchone()[0] == "中文".encode()
    # Only the outermost message is kept, at most 500 characters.
    from isbe.sources.registry_materials import failed

    try:
        try:
            raise OSError("inner cause")
        except OSError as cause:
            raise RuntimeError("x" * 600) from cause
    except RuntimeError as error:
        failed(conn, "a", error)
    assert conn.execute("SELECT last_error FROM source_definitions").fetchone()[0] == "x" * 500


NEWS = (
    '<rss version="2.0"><channel><title>News</title><link>https://example.com</link><description>news'
    "</description><item><title>Public policy update</title><link>https://example.com/article</link>"
    "</item></channel></rss>"
)
EMPTY = (
    '<rss version="2.0"><channel><title>Empty</title><link>https://example.com</link>'
    "<description>empty</description></channel></rss>"
)


def test_discovery_enables_successful_new_sources_and_respects_a_pause(
    conn: sqlite3.Connection, serve, local_resolver: netguard.PublicResolver
) -> None:
    seen: list[str] = []

    def handle(req: BaseHTTPRequestHandler) -> None:
        seen.append(req.path)
        serve.reply(req, 200, EMPTY if req.path == "/empty" else NEWS)

    port = serve(handle).rsplit(":", 1)[1]
    url = f"http://feed.test:{port}/feed"
    first = registry.discover(conn, url, "Shanghai", True, resolver=local_resolver)
    assert first["enabled"] == 1
    assert first["new"] == 1
    rows = registry.list(conn)
    config = registry.Source.model_validate(rows[0]["config"]).model_copy(update={"enabled": False})
    registry.save(conn, config, rows[0]["revision"], config.id)
    again = registry.discover(conn, url, "Shanghai", True, resolver=local_resolver)
    assert again["enabled"] == 0
    assert again["unchanged"] == 1
    assert registry.get(conn, config.id)["config"]["enabled"] is False
    empty = registry.discover(conn, url.replace("/feed", "/empty"), "Shanghai", True, resolver=local_resolver)
    assert empty["enabled"] == 0
    assert empty["candidates"][0]["state"] == "candidate"
    assert count(conn, "SELECT count(*) FROM llm_calls") == 0
    assert len(seen) == 6
    # The derived id hashes the topic, the capitalized kind name and the URL.
    assert config.id == "src-" + db.sha256(f"Shanghai\nRss\n{url}")[:20]
    row = conn.execute("SELECT origin,discovered_from FROM source_definitions WHERE id=?1", (config.id,)).fetchone()
    assert tuple(row) == ("discovered", url)


def site(serve, pages: dict[str, tuple[int, str]]) -> str:
    def handle(req: BaseHTTPRequestHandler) -> None:
        code, body = pages.get(req.path, (404, "gone"))
        serve.reply(req, code, body)

    return f"http://site.test:{serve(handle).rsplit(':', 1)[1]}"


def links(*paths: str) -> str:
    return "<title>Site</title>" + "".join(
        f'<link rel="alternate" type="application/rss+xml" title="Feed {p}" href="{p}">' for p in paths
    )


def feed(link: str) -> str:
    return NEWS.replace("https://example.com/article", link)


def test_rediscovery_keeps_an_edited_source_and_writes_atomically(
    conn: sqlite3.Connection, serve, local_resolver: netguard.PublicResolver, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = {"/": (200, links("/broken", "/news")), "/news": (200, feed("https://example.com/n"))}
    base = site(serve, pages)
    first = registry.discover(conn, base + "/", "Shanghai", True, resolver=local_resolver)
    assert [c["state"] for c in first["candidates"]] == ["candidate", "enabled"]
    broken = first["candidates"][0]["id"]
    assert registry.get(conn, broken)["last_status"] == "failed"
    # No material yet, so the user may point the broken candidate at the right feed.
    pages["/fixed"] = (200, feed("https://example.com/f"))
    edited = registry.Source.model_validate(registry.get(conn, broken)["config"]).model_copy(
        update={"url": base + "/fixed", "name": "Fixed by hand"}
    )
    registry.save(conn, edited, "1", broken)
    # `broken`'s id must not be inserted again here (a primary-key failure, after `/extra` was written).
    pages["/"] = (200, links("/extra", "/broken", "/news"))
    pages["/extra"] = (200, feed("https://example.com/e"))
    again = registry.discover(conn, base + "/", "Shanghai", True, resolver=local_resolver)
    assert [(c["id"] == broken, c["state"], c["url"]) for c in again["candidates"]] == [
        (False, "enabled", base + "/extra"),
        (True, "existing", base + "/fixed"),
        (False, "existing", base + "/news"),
    ]
    assert again["failed"] == 0 and again["checked"] == 3
    kept = registry.get(conn, broken)
    assert kept["config"] == edited.to_json() and kept["revision"] == "2"
    assert kept["last_status"] == "ok" and kept["item_count"] == 1
    assert count(conn, "SELECT count(*) FROM source_definitions") == 3

    # A failure while writing leaves nothing of the run behind.
    pages["/"] = (200, links("/later", "/extra"))
    pages["/later"] = (200, feed("https://example.com/l"))
    before = conn.execute("SELECT * FROM source_definitions ORDER BY id").fetchall()
    fetches = count(conn, "SELECT count(*) FROM source_fetches")
    calls = 0
    real = registry_discover.record_rows

    def flaky(*args: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise sqlite3.OperationalError("disk I/O error")
        return real(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(registry_discover, "record_rows", flaky)
    with pytest.raises(sqlite3.OperationalError):
        registry.discover(conn, base + "/", "Shanghai", True, resolver=local_resolver)
    assert conn.execute("SELECT * FROM source_definitions ORDER BY id").fetchall() == before
    assert count(conn, "SELECT count(*) FROM source_fetches") == fetches
    assert not conn.in_transaction


def test_jobs_are_idempotent_and_crashed_workers_are_recovered(data: Path) -> None:
    conn = db.connect(data / "isbe.db")
    try:
        registry.save(conn, source("a"), None, None)
        operation = registry.Collect("a")
        fingerprint = db.sha256(jsonfmt.dumps(operation.to_json(), sort_keys=False))
        assert fingerprint == db.sha256('{"operation":"collect","source_id":"a"}')
        conn.execute(
            "INSERT INTO source_jobs(id,fingerprint,operation,state,created_at) VALUES ('retry',?1,'collect','running',?2)",
            (fingerprint, db.now()),
        )
        guard = locks.execution_lock(data)
        assert not registry.recover(data)
        assert registry.start(data, operation, "retry")["state"] == "running"
        assert status(lambda: registry.start(data, operation, "other")) == 409
        with pytest.raises(HttpError):
            registry.save(conn, source("b"), None, None)
        guard.release()
        assert registry.recover(data)
        assert registry.job(conn, "retry")["state"] == "interrupted"
        different = registry.Discover("https://example.com/", "Shanghai", True)
        assert status(lambda: registry.start(data, different, "retry")) == 409
        for bad in ["", "x" * 129, "a b", "é"]:
            assert status(lambda: registry.start(data, operation, bad)) == 400
        assert status(lambda: registry.job(conn, "missing")) == 404
    finally:
        conn.close()


def test_started_collection_runs_on_its_own_thread_and_releases_the_lock(
    data: Path, serve, local_sources: None
) -> None:
    port = serve(lambda req: serve.reply(req, 200, NEWS)).rsplit(":", 1)[1]
    conn = db.connect(data / "isbe.db")
    try:
        cfg = source("a").model_copy(update={"url": f"http://feed.test:{port}/feed", "kind": Kind.RSS})
        registry.save(conn, cfg, None, None)
        assert registry.start(data, registry.Collect("a"), "web-1")["state"] == "running"
        for _ in range(100):
            if registry.recover(data) and registry.job(conn, "web-1")["state"] != "running":
                break
            time.sleep(0.05)
        done = registry.job(conn, "web-1")
        assert done["state"] == "succeeded"
        assert done["stats"] == {"found": 1, "new": 1, "source_id": "a", "truncated": False, "unchanged": 0, "updated": 0}
        assert done["finished_at"] is not None and done["error"] is None
        assert [j["id"] for j in registry.jobs(conn)] == ["web-1"]
    finally:
        conn.close()
