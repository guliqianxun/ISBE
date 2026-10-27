"""Paper records stored once (`question.papers`, migration 016): a search stores each
distinct record one time however many hits, sets and events reach it, and a
database written before 016 reads back the same after migrating."""

import json
import sqlite3

import pytest
from test_search import sample_setup

from isbe.core import db, paperrefs
from isbe.question import papers, runs, search, service, sets
from isbe.web import question_api

RUN = "run-20261024-0000aaaa"
A = {"key": "arxiv:2401.00001", "keys": ["arxiv:2401.00001"], "title": "A", "abstract": "Long text " * 50,
     "year": 2024, "cited_by_count": 3}
# The same paper after a fold filled a gap: another record.
A_FOLDED = {**A, "keys": ["doi:10.1/a", "arxiv:2401.00001"], "key": "doi:10.1/a", "venue": "J"}
B = {"key": "arxiv:2401.00002", "keys": ["arxiv:2401.00002"], "title": "B", "abstract": None, "year": 2023}
# Reached only by citation expansion: in an event, no hit or set.
C = {"key": "doi:10.1/c", "keys": ["doi:10.1/c"], "title": "C", "abstract": None, "year": 2020}


def test_a_search_stores_each_record_once() -> None:
    _, conn, run_id, source = sample_setup("green-space-mental-health")
    search.run(conn, source, run_id, enough=10**6)
    hits = conn.execute("SELECT count(*) FROM question_hits").fetchone()[0]
    stored = conn.execute("SELECT count(*) FROM question_papers").fetchone()[0]
    keys = conn.execute("SELECT count(DISTINCT key) FROM question_hits").fetchone()[0]
    assert hits > stored >= keys
    # Nothing but refs elsewhere: no abstract outside question_papers.
    for table, column in [("paper_sets", "doc"), ("question_events", "data")]:
        assert conn.execute(f"SELECT count(*) FROM {table} WHERE {column} LIKE '%\"abstract\"%'").fetchone()[0] == 0
    # A second search of the same run adds hits and sets but no records.
    search.run(conn, source, run_id, enough=10**6)
    assert conn.execute("SELECT count(*) FROM question_hits").fetchone()[0] == 2 * hits
    assert conn.execute("SELECT count(*) FROM question_papers").fetchone()[0] == stored
    assert all(m["paper"]["title"] for m in sets.Store(conn, run_id).get("s2.candidates").members)


def test_deleting_a_run_keeps_the_records_another_run_uses_by_hit_set_or_event() -> None:
    _, conn, run_id, source = sample_setup("green-space-mental-health")
    search.run(conn, source, run_id, enough=10**6)
    mine = [r for (r,) in conn.execute("SELECT DISTINCT paper_ref FROM question_hits ORDER BY paper_ref")]
    by_hit, by_set, by_event = (papers.get(conn, [r])[r] for r in mine[:3])
    other = runs.create(conn, "q", "en")
    # Each record kept alive by one kind of reference only.
    papers.put(conn, other, [by_hit])
    conn.execute(
        "INSERT INTO question_queries (run_id, search, query_id, plan_version, query, status, hits, ms, created_at) "
        "VALUES (?, 1, 'q1', 1, 'x', 200, 1, 5, ?)", (other, db.now()))
    conn.execute("INSERT INTO question_hits VALUES (?, 1, 'q1', 1, ?, 1, ?)", (other, by_hit["key"], mine[0]))
    sets.Store(conn, other).add(sets.PaperSet("s1.kept", "query", "kept", [{"paper": by_set, "provenance": []}]))
    runs.emit(conn, other, [("hit", {"query_id": "q1", "rank": 1, "paper": by_event, "new": True})])
    runs.delete(conn, run_id)
    assert sorted(r for (r,) in conn.execute("SELECT ref FROM question_papers")) == mine[:3]
    assert sets.Store(conn, other).get("s1.kept").members[0]["paper"] == by_set
    assert runs.events(conn, other)[-1]["data"]["paper"] == by_event
    runs.delete(conn, other)
    assert conn.execute("SELECT count(*) FROM question_papers").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM question_paper_uses").fetchone()[0] == 0


def test_a_read_sees_one_state_while_its_run_is_deleted(tmp_path, monkeypatch) -> None:
    # Another connection deletes the run between a read's rows and their records:
    # the read still gets both (one snapshot), never rows whose records are gone.
    _, built, run_id, source = sample_setup("green-space-mental-health")
    search.run(built, source, run_id, enough=10**6)
    path = tmp_path / "isbe.db"
    with sqlite3.connect(path) as target:
        built.backup(target)
    target.close()
    reader, deleter = db.connect(path), db.connect(path)
    want = runs.events(reader, run_id)
    assert any("paper" in e["data"] for e in want)
    real_get, deleted = papers.get, []

    def get_after_a_delete(conn, refs):
        if not deleted:
            runs.delete(deleter, run_id)
            deleted.append(run_id)
        return real_get(conn, refs)

    monkeypatch.setattr(papers, "get", get_after_a_delete)
    assert runs.events(reader, run_id) == want
    assert deleted and runs.events(reader, run_id) == []  # the next read sees it gone


def test_the_field_of_a_run_still_searching() -> None:
    # Paused after its first query: hits, no candidate set, nothing screened.
    _, conn, run_id, source = sample_setup("green-space-mental-health")
    asked = []

    def check() -> None:
        if asked:
            raise RuntimeError("paused")
        asked.append(1)

    with pytest.raises(RuntimeError):
        search.run(conn, source, run_id, enough=10**6, check=check)
    assert service.candidates_set(conn, run_id) is None
    field = question_api._field(conn, run_id)
    keys = conn.execute("SELECT DISTINCT key FROM question_hits WHERE run_id = ?", (run_id,)).fetchall()
    assert len(field["papers"]) == len(keys) > 0
    assert all(p["decision"] is None and p["queries"] for p in field["papers"])
    assert field["citations"] == [] and field["fetched_at"] is None


def test_slim_and_full_round_trip_in_place() -> None:
    conn = db_at(len(db._migrations()))
    items = [{"query_id": "q1", "rank": 1, "paper": A, "new": True}, {"state": "done"}]
    run_id = runs.create(conn, "q", "en")
    slim = papers.slim(conn, run_id, items)
    assert list(slim[0]) == ["query_id", "rank", "paper_ref", "new"] and slim[1] == {"state": "done"}
    back = papers.full(conn, slim)
    assert back == items and list(back[0]) == list(items[0])
    back[0]["paper"]["title"] = "changed"
    assert papers.full(conn, slim)[0]["paper"]["title"] == "A"


def test_a_database_from_before_016_reads_the_same_after_it() -> None:
    conn = db_at(15)
    legacy(conn)
    before = {
        "hits": conn.execute("SELECT paper FROM question_hits ORDER BY rowid").fetchall(),
        "set": json.loads(conn.execute("SELECT doc FROM paper_sets").fetchone()[0]),
        "events": [json.loads(d) for (d,) in conn.execute("SELECT data FROM question_events ORDER BY seq")],
    }
    db._migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db._migrations())
    # A, A_FOLDED, B and C, once each, each used by the run (017).
    assert conn.execute("SELECT count(*) FROM question_papers").fetchone()[0] == 4
    assert {r for (r,) in conn.execute("SELECT ref FROM question_paper_uses WHERE run_id = ?", (RUN,))} == {
        papers.ref(p) for p in (A, A_FOLDED, B, C)
    }
    assert sets.Store(conn, RUN).get("s1.candidates").doc(RUN) == before["set"]
    assert [e["data"] for e in runs.events(conn, RUN)] == before["events"]
    assert search.response(conn, RUN, 1, "q1") == '{"results":[1,2]}'
    assert conn.execute("SELECT response FROM question_queries").fetchone()[0] is None
    hits = conn.execute("SELECT paper_ref FROM question_hits ORDER BY rowid").fetchall()
    assert [papers.get(conn, [r])[r] for (r,) in hits] == [json.loads(p) for (p,) in before["hits"]]
    listed = service.papers(conn, RUN)
    assert [m["paper"] for m in listed] == [A_FOLDED, B]
    # New writes after the migration share the migrated rows.
    runs.emit(conn, RUN, [("hit", {"query_id": "q1", "rank": 3, "paper": B, "new": False})])
    assert conn.execute("SELECT count(*) FROM question_papers").fetchone()[0] == 4
    runs.delete(conn, RUN)
    assert conn.execute("SELECT count(*) FROM question_papers").fetchone()[0] == 0


def test_017_removes_the_records_no_run_uses() -> None:
    # Left by a run deleted before 017, whose delete removed no records.
    conn = db_at(16)
    legacy_at_16 = {"key": "x:1", "title": "orphan"}
    conn.execute("INSERT INTO question_papers VALUES (?, ?)", (papers.ref(legacy_at_16), json.dumps(legacy_at_16)))
    db._migrate(conn)
    assert conn.execute("SELECT count(*) FROM question_papers").fetchone()[0] == 0


def test_a_write_inside_a_read_snapshot_fails_rather_than_being_lost() -> None:
    conn = db_at(len(db._migrations()))
    run_id = runs.create(conn, "q", "en")
    with pytest.raises(sqlite3.OperationalError, match="readonly"), db.snapshot(conn):
        runs.set_status(conn, run_id, "failed")
    assert runs.get(conn, run_id)["status"] == "queued"
    runs.set_status(conn, run_id, "failed")  # writable again after it
    assert runs.get(conn, run_id)["status"] == "failed"


def db_at(version: int) -> sqlite3.Connection:
    """An in-memory database with the first `version` migrations applied."""
    conn = sqlite3.connect(":memory:", autocommit=True)
    conn.execute("PRAGMA foreign_keys=ON")
    if version == len(db._migrations()):
        db._migrate(conn)
        return conn
    paperrefs.register(conn)
    for sql in db._migrations()[:version]:
        conn.executescript(sql)
    conn.execute(f"PRAGMA user_version = {version}")
    return conn


def legacy(conn: sqlite3.Connection) -> None:
    """One run as code before 016 stored it: full records everywhere."""
    dump = lambda v: json.dumps(v, separators=(",", ":"), ensure_ascii=False)  # noqa: E731
    at = "2026-10-24T00:00:00.000Z"
    conn.execute("INSERT INTO question_runs VALUES (?, 'q', 'en', 'done', ?, ?)", (RUN, at, at))
    for q in ("q1", "q2"):
        conn.execute(
            "INSERT INTO question_queries (run_id, search, query_id, plan_version, query, status, response, hits, ms, "
            "created_at) VALUES (?, 1, ?, 1, 'x', 200, ?, 1, 5, ?)",
            (RUN, q, '{"results":[1,2]}' if q == "q1" else None, at),
        )
    hits = [("q1", 1, A), ("q1", 2, B), ("q2", 1, A)]
    for q, rank, p in hits:
        conn.execute("INSERT INTO question_hits VALUES (?, 1, ?, ?, ?, 1, ?)", (RUN, q, rank, p["key"], dump(p)))
    members = [
        {"paper": A_FOLDED, "provenance": [{"via": "search", "query_id": "q1", "rank": 1}]},
        {"paper": B, "provenance": [{"via": "search", "query_id": "q1", "rank": 2}]},
    ]
    doc = {"schema": "paper_set@1", "run_id": RUN, "set_id": "s1.candidates", "kind": "candidates",
           "label": "Candidates", "made_by": "op-1", "created_at": at, "members": members}
    conn.execute("INSERT INTO paper_sets VALUES (?, 's1.candidates', 'candidates', 'op-1', 2, ?, ?)",
                 (RUN, dump(doc), at))
    events = [("state", {"state": "running"})] + [
        ("hit", {"query_id": q, "rank": r, "paper": p, "new": True}) for q, r, p in hits
    ] + [("expand", {"via": "cites", "paper": B, "new": True}), ("expand", {"via": "cites", "paper": C, "new": True})]
    for seq, (kind, data) in enumerate(events, 1):
        conn.execute("INSERT INTO question_events VALUES (?, ?, ?, ?, ?)", (RUN, seq, at, kind, dump(data)))
