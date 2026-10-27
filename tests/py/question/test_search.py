"""The search stage (`isbe.question.search`) on the recorded essaies replies of
the sample questions: priority order, hits and dedup events, failure isolation,
the stored queries and sets, the contracts and key-paper recall, on essaies 0.5
(capabilities, paging, filters, abstracts, batch lookup) and on an earlier
essaies (`OLD`: plain searches of 100)."""

import copy

import httpx
import pytest
import replay
from question_schemas import errors, samples

from isbe.core import db, http, jsonfmt
from isbe.question import runs, search, sets, sources

IDS = [q["id"] for q in samples()]
# What an essaies without /api/openalex/capabilities offers.
OLD = ()

# Must-find papers among the candidates of every query of each plan, as recorded
# from essaies 0.5 on 2026-10-23 (100 results a query, the plan's years). Strata's
# composed plans (whose broad queries ask for 200) were recorded on 2026-10-26
# (`replay.py --missing`) once essaies could reach OpenAlex again.
#
# "strata-arxiv" is Strata's plan written for essaies' arXiv index, recorded on
# 2026-10-23 (essaies.arxiv.json). The index holds 6, 10, 6 and 0 of those
# samples' must-find papers; the green-space papers are journal articles it does
# not have, so that case is skipped rather than pinned at 0.
RECALL = {
    "radar-nowcasting-dl": {"plan.json": 8, "strata": 8, "strata-arxiv": 5},
    "ml-medium-range-weather": {"plan.json": 9, "strata": 8, "strata-arxiv": 9},
    "diffusion-image-generation": {"plan.json": 7, "strata": 4, "strata-arxiv": 4},
    "neural-pde-solvers": {"plan.json": 5, "strata": 5, "strata-arxiv": 0},
    "green-space-mental-health": {"plan.json": 5, "strata": 1, "strata-arxiv": None},
}


def setup(plan: dict, transport: httpx.BaseTransport, features=None):
    """A database with a run of `plan` saved as its version 1, and essaies on
    `transport` (with `features`, an essaies that offers those)."""
    conn = db.connect(":memory:")
    run_id = runs.create(conn, plan["question"], plan["lang"])
    saved = {**plan, "run_id": run_id, "version": 1, "parent_version": None}
    conn.execute(
        "INSERT INTO question_plans (run_id, version, parent_version, source, plan, created_at) "
        "VALUES (?, 1, NULL, ?, ?, ?)",
        (run_id, saved["source"], jsonfmt.dumps(saved, sort_keys=False), saved["created_at"]),
    )
    source = sources.Essaies(
        http.Http(5, transport=transport), "http://essaies.test", features=features, interval=0, sleep=lambda _: None
    )
    return conn, run_id, source


def sample_setup(sample_id: str, features=None, plan: str = "plan.json", **changes):
    s = replay.sample(sample_id)
    chosen = s.strata_plan() if plan == "strata" else copy.deepcopy(s.plan)
    return (s, *setup(chosen | changes, s.essaies.transport(), features))


def recorded_search(s: replay.Sample, query: str) -> dict:
    """The recorded essaies 0.5 search for `query` (the first page)."""
    return next(
        i["response"] for i in s.essaies.interactions
        if i["request"]["path"] == "/api/openalex/search"
        and dict(map(tuple, i["request"]["query"])).get("q") == query
        and dict(map(tuple, i["request"]["query"])).get("cursor") == "*"
    )


def found(question: dict, members: list[dict]) -> list[str]:
    held = {k for m in members for k in m["paper"]["keys"]}
    return [p["title"] for p in question["must_find"] if held & set(p["keys"])]


@pytest.mark.parametrize("plan", ["plan.json", "strata"])
@pytest.mark.parametrize("sample_id", IDS)
def test_recorded_searches_give_the_candidate_set(sample_id, plan):
    if plan == "strata" and not replay.sample(sample_id).strata_recorded():
        pytest.skip("the Strata plan's searches are not recorded yet")
    s, conn, run_id, source = sample_setup(sample_id, plan=plan)
    result = search.run(conn, source, run_id, enough=10**6)
    asked = s.strata_plan() if plan == "strata" else s.plan
    assert result.queries_failed == [] and result.queries_skipped == []
    assert result.queries_run == [q["id"] for q in sorted(asked["queries"], key=lambda q: q["priority"])]
    store = sets.Store(conn, run_id)
    candidates = store.get(result.candidates)
    assert errors("paper_set", candidates.doc(run_id)) == []
    held = [k for m in candidates.members for k in m["paper"]["keys"]]
    assert len(held) == len(set(held)), "members share a key"
    assert len(found(s.question, candidates.members)) == RECALL[sample_id][plan]
    # essaies 0.5 sends abstracts and keywords with the search.
    assert sum(1 for m in candidates.members if m["paper"]["abstract"]) > len(candidates.members) / 2
    assert any(m["paper"]["keywords"] for m in candidates.members)
    assert store.replay()[result.candidates] == candidates.members
    for op in store.ops():
        assert errors("set_op", op) == []


@pytest.mark.parametrize("sample_id", IDS)
def test_the_arxiv_index_gives_the_candidate_set(sample_id):
    s = replay.sample(sample_id)
    if RECALL[sample_id]["strata-arxiv"] is None:
        pytest.skip("journal papers: essaies' arXiv index does not hold them")
    plan = s.strata_plan("arxiv")
    assert plan["engine"] == "arxiv" and errors("question_plan", plan) == []
    conn, run_id, _ = setup(plan, s.arxiv.transport())
    source = sources.Essaies(
        http.Http(5, transport=s.arxiv.transport()), "http://essaies.test", engine="arxiv", interval=0
    )
    result = search.run(conn, source, run_id, enough=10**6)
    assert result.queries_failed == [] and result.queries_skipped == []
    assert result.queries_run == [q["id"] for q in sorted(plan["queries"], key=lambda q: q["priority"])]
    store = sets.Store(conn, run_id)
    candidates = store.get(result.candidates)
    assert errors("paper_set", candidates.doc(run_id)) == []
    held = [k for m in candidates.members for k in m["paper"]["keys"]]
    assert len(held) == len(set(held)), "members share a key"
    assert all(m["paper"]["key"].startswith("arxiv:") and m["paper"]["abstract"] for m in candidates.members)
    assert len(found(s.question, candidates.members)) == RECALL[sample_id]["strata-arxiv"]
    assert store.replay()[result.candidates] == candidates.members


def test_an_arxiv_search_is_one_request_within_the_years():
    asked = []

    def essaies(request: httpx.Request) -> httpx.Response:
        asked.append((request.url.path, dict(request.url.params)))
        item = {"id": "2104.00954", "title": "Skilful nowcasting", "year": 2021, "abstract": "Radar.", "has_pdf": 1}
        return httpx.Response(200, json={"items": [item, "junk"]})

    source = sources.Essaies(
        http.Http(5, transport=httpx.MockTransport(essaies)), "http://essaies.test", engine="arxiv", interval=0
    )
    found = source.search('nowcasting AND ("U-Net" OR radar)', limit=5000, year_from=2018)
    assert asked == [("/api/search", {"q": 'nowcasting AND ("U-Net" OR radar)', "limit": "2000", "year_from": "2018"})]
    assert found.filtered and [w["id"] for w in found.works] == ["2104.00954"]
    record = sources.paper(found.works[0])
    assert record["key"] == "arxiv:2104.00954" and record["in_archive"] is True
    assert record["url"] == "https://arxiv.org/abs/2104.00954"
    with pytest.raises(ValueError):
        sources.Essaies(http.Http(5), "http://essaies.test", engine="bing")


@pytest.mark.parametrize("sample_id", IDS)
def test_an_old_essaies_gives_the_candidate_set(sample_id):
    # Every query, no year limit: the candidates hold what the raw searches found.
    s, conn, run_id, source = sample_setup(sample_id, OLD, year_from=None, year_to=None)
    result = search.run(conn, source, run_id, enough=10**6)
    assert result.queries_failed == [] and result.queries_skipped == []
    assert result.queries_run == [q["id"] for q in sorted(s.plan["queries"], key=lambda q: q["priority"])]
    store = sets.Store(conn, run_id)
    candidates = store.get(result.candidates)
    doc = candidates.doc(run_id)
    assert errors("paper_set", doc) == []
    held = [k for m in candidates.members for k in m["paper"]["keys"]]
    assert len(held) == len(set(held)), "members share a key"
    assert result.candidates_size == len(candidates.members) < result.hits
    from testkit.test_recorded_samples import FOUND

    assert len(found(s.question, candidates.members)) == FOUND[sample_id]
    # The stored operations rebuild the candidate set exactly.
    assert store.replay()[result.candidates] == candidates.members
    for op in store.ops():
        assert errors("set_op", op) == []


def test_events_tell_the_whole_search():
    # Search results always carry their OpenAlex work; an archive-only record (as
    # citation lookups return them) is what dedup folds into one.
    s = replay.sample("ml-medium-range-weather")
    plan = copy.deepcopy(s.plan)
    plan["queries"].append({"id": "q6", "query": "FourCastNet archive", "layer": "emerging", "priority": 6})
    archive = {"items": [{"id": "2202.11214", "title": "FourCastNet (archive record)", "year": 2022, "has_pdf": 1}]}

    def essaies(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("q") == "FourCastNet archive":
            return httpx.Response(200, json=archive)
        return s.essaies(request)

    conn, run_id, source = setup(plan, httpx.MockTransport(essaies), OLD)
    result = search.run(conn, source, run_id, enough=10**6)
    stream = runs.events(conn, run_id)
    for event in stream:
        assert errors("question_event", event) == [], event["type"]
    assert [e["seq"] for e in stream] == list(range(1, len(stream) + 1))
    assert stream[0]["data"] == {"state": "queued"}
    assert stream[1]["data"] == {"stage": "search", "state": "begin"}
    assert stream[-1]["data"] == {"stage": "search", "state": "end"}
    hits = [e["data"] for e in stream if e["type"] == "hit"]
    # One star per distinct key; a repeat hit brightens it.
    new = [h["paper"]["key"] for h in hits if h["new"]]
    assert len(new) == len(set(new)) == conn.execute(
        "SELECT count(DISTINCT key) FROM question_hits WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    assert any(not h["new"] for h in hits)
    assert [h["query_id"] for h in hits] == sorted((h["query_id"] for h in hits), key=lambda q: int(q[1:]))
    # The archive record's star merges into the OpenAlex one.
    dedups = [e["data"] for e in stream if e["type"] == "dedup"]
    assert dedups == [{"kept": "openalex:W4225906496", "merged": ["arxiv:2202.11214"]}]
    assert result.merged == 1
    candidates = sets.Store(conn, run_id).get(result.candidates).members
    fourcastnet = next(m for m in candidates if m["paper"]["key"] == "openalex:W4225906496")
    assert "arxiv:2202.11214" in fourcastnet["paper"]["keys"]
    assert {p["query_id"] for p in fourcastnet["provenance"]} >= {"q6"}


def test_years_of_the_plan_leave_older_papers_out():
    # essaies 0.5 filters them itself; an old essaies returns them and ISBE drops them.
    s, conn, run_id, source = sample_setup("ml-medium-range-weather")  # year_from 2018
    result = search.run(conn, source, run_id, enough=10**6)
    assert all("year_from=2018" in key for key in s.essaies.served if "/search?" in key)
    assert result.out_of_years == 0
    _, old_conn, old_run, old_source = sample_setup("ml-medium-range-weather", OLD)
    old = search.run(old_conn, old_source, old_run, enough=10**6)
    assert old.out_of_years > 0
    for c, run, r in ((conn, run_id, result), (old_conn, old_run, old)):
        members = sets.Store(c, run).get(r.candidates).members
        assert all(m["paper"]["year"] is None or m["paper"]["year"] >= 2018 for m in members)


def test_lower_priorities_run_only_when_there_are_too_few_papers():
    s, conn, run_id, source = sample_setup("radar-nowcasting-dl")
    result = search.run(conn, source, run_id, enough=150)
    assert result.queries_run == ["q1", "q2"]
    assert result.queries_skipped == ["q3", "q4", "q5"]
    assert [k.split("?")[0] for k in s.essaies.served] == [
        "GET /api/openalex/capabilities", "GET /api/openalex/search", "GET /api/openalex/search",
    ]


def test_a_failed_query_does_not_stop_the_others():
    # The question as asked is a recorded 502 (OpenAlex rejects the "?").
    s = replay.sample("neural-pde-solvers")
    plan = copy.deepcopy(s.plan)
    plan["queries"].insert(0, {"id": "asked", "query": plan["question"], "layer": "original", "priority": 1})
    conn, run_id, source = setup(plan, s.essaies.transport(), OLD)
    result = search.run(conn, source, run_id, enough=10**6)
    assert result.queries_failed == ["asked"]
    assert len(result.query_sets) == len(s.plan["queries"])
    error = next(e for e in runs.events(conn, run_id) if e["type"] == "error")
    assert error["data"]["fatal"] is False and error["data"]["query_id"] == "asked"
    assert "HTTP Error 400" in error["data"]["message"]
    row = conn.execute(
        "SELECT status, error, response, hits FROM question_queries WHERE run_id = ? AND query_id = 'asked'", (run_id,)
    ).fetchone()
    assert row[0] == 502 and "HTTP 502" in row[1] and row[2] is None and row[3] == 0
    assert runs.get(conn, run_id)["status"] == "queued"


def test_every_query_failing_fails_the_run():
    s = replay.sample("neural-pde-solvers")
    plan = copy.deepcopy(s.plan)
    plan["queries"] = [{"id": "q1", "query": plan["question"], "layer": "original", "priority": 1}]
    conn, run_id, source = setup(plan, s.essaies.transport(), OLD)
    with pytest.raises(search.SearchFailed):
        search.run(conn, source, run_id)
    stream = runs.events(conn, run_id)
    assert [e["type"] for e in stream[-4:]] == ["error", "error", "state", "done"]
    assert stream[-3]["data"]["fatal"] is True
    assert stream[-1]["data"] == {"status": "failed"}
    assert runs.get(conn, run_id)["status"] == "failed"
    for event in stream:
        assert errors("question_event", event) == []


def test_raw_replies_and_hits_are_stored():
    s, conn, run_id, source = sample_setup("green-space-mental-health")
    result = search.run(conn, source, run_id, enough=10**6)
    rows = conn.execute(
        "SELECT query_id, query, status, response, hits FROM question_queries WHERE run_id = ? ORDER BY query_id",
        (run_id,),
    ).fetchall()
    assert len(rows) == len(s.plan["queries"])
    for query_id, query, status, plain, hits in rows:
        recorded = recorded_search(s, query)
        # Stored compressed (migration 016), read back as it came.
        assert plain is None
        assert status == 200 and jsonfmt.loads(search.response(conn, run_id, 1, query_id)) == recorded["json"]
        stored = conn.execute(
            "SELECT count(*) FROM question_hits WHERE run_id = ? AND query_id = ?", (run_id, query_id)
        ).fetchone()[0]
        assert stored == hits == len(sets.Store(conn, run_id).get(f"s1.{query_id}").members)
    assert result.search == 1


def test_a_second_search_numbers_its_rows_and_sets():
    _, conn, run_id, source = sample_setup("neural-pde-solvers")
    first = search.run(conn, source, run_id)
    second = search.run(conn, source, run_id)
    assert (first.search, second.search) == (1, 2)
    assert (first.candidates, second.candidates) == ("s1.candidates", "s2.candidates")
    store = sets.Store(conn, run_id)
    assert store.get("s1.candidates").members == store.get("s2.candidates").members
    assert store.replay()["s2.candidates"] == store.get("s2.candidates").members


def test_search_needs_a_run_and_its_plan():
    from isbe.core.errors import HttpError

    conn = db.connect(":memory:")
    source = sources.Essaies(http.Http(5, transport=httpx.MockTransport(lambda r: httpx.Response(500))), "http://x")
    with pytest.raises(HttpError) as missing:
        search.run(conn, source, "run-none")
    assert missing.value.status == 404
    run_id = runs.create(conn, "Q", "en")
    with pytest.raises(HttpError, match="No plan"):
        search.run(conn, source, run_id)
