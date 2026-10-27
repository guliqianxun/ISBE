"""The citations between a run's candidates (`isbe.question.citations`) on a small
made-up run and essaies: edges from the archive batch and the full lookups, oriented
and matched on any key, only between candidates; the full pass's order, limit and
skipped exclusions; OpenAlex out of quota; an essaies too old for full references;
a stage that runs again; the `citations` events against the contract; and the
expansion: picks ranked and capped, added as candidates and screened, once."""

import json
from contextlib import closing

import httpx
import pytest
import question_flow as qf
from question_schemas import errors

from isbe.core import db, http
from isbe.question import citations, expand, runs, screen, service, sets, sources


def paper(ident: str, cited: int = 0, doi: str | None = None) -> dict:
    keys = [f"arxiv:{ident}"] if sources.arxiv_key(ident) else [f"doi:{ident}"]
    if doi:
        keys.insert(0, f"doi:{doi}")
    return {"key": keys[0], "keys": keys, "title": f"Paper {ident}", "cited_by_count": cited}


# Four candidates; 2101.00004 is known by its DOI first, and the essaies works that
# cite it name only that DOI.
PAPERS = [
    paper("2101.00001", 50), paper("2101.00002", 40), paper("2101.00003", 30),
    paper("2101.00004", 10, doi="10.1000/four"),
]


@pytest.fixture
def conn():
    with closing(db.connect(":memory:")) as c:
        yield c


def make_run(conn, papers=PAPERS) -> str:
    run_id = runs.create(conn, "Which graph networks forecast weather?", "en", run_id="run-cite")
    store = sets.Store(conn, run_id)
    members = [{"paper": p, "provenance": [{"via": "search", "query_id": "q1", "rank": n}]}
               for n, p in enumerate(papers, 1)]
    store.add(sets.PaperSet("s1.q1", "query", "graph weather", members))
    store.run("union", ["s1.q1"], "s1.union", "candidates", "All query results", {"plan_version": 1})
    store.run("dedup", ["s1.union"], "s1.candidates", "candidates", "Candidates after deduplication")
    return run_id


def source(fake: qf.Essaies, features=None) -> sources.Essaies:
    return sources.Essaies(http.Http(5, transport=httpx.MockTransport(fake)), "http://essaies.test",
                           features=features, interval=0, retries=0, sleep=lambda _: None)


def graph() -> qf.Essaies:
    fake = qf.Essaies()
    # Archive: 1 cites 2 (seen from both ends), 3 cites a paper outside the run.
    fake.archive = {"2101.00001": (["2101.00002"], []), "2101.00002": ([], ["2101.00001"]),
                    "2101.00003": (["2201.09999"], [])}
    # Full: 3 is cited by 1 and by 4 (as its DOI); 4 cites itself, which is no edge.
    fake.full = {"2101.00003": ([], ["2101.00001", "10.1000/four"]), "2101.00004": (["2101.00004"], [])}
    return fake


def test_edges_join_both_scopes_between_candidates_only(conn):
    run_id = make_run(conn)
    assert citations.edges(conn, run_id) == {"citations": [], "fetched_at": None}
    fake = graph()
    result = citations.run(conn, source(fake), run_id, full_limit=10)
    got = citations.edges(conn, run_id)
    assert got["citations"] == [
        ["arxiv:2101.00001", "arxiv:2101.00002"], ["arxiv:2101.00001", "arxiv:2101.00003"],
        ["doi:10.1000/four", "arxiv:2101.00003"],
    ]
    assert got["fetched_at"] is not None
    assert (result.edges, result.looked_up, result.errors) == (3, 8, [])
    evs = runs.events(conn, run_id)
    assert [m for e in evs for m in errors("question_event", e)] == []
    assert [(e["type"], e["data"]["scope"]) for e in evs[1:]] == [("citations", "archive"), ("citations", "all")]
    assert evs[-1]["data"] == {"scope": "all", "looked_up": 8, "edges": 3}


def test_the_full_pass_asks_kept_papers_most_cited_first_up_to_the_limit(conn):
    run_id = make_run(conn)
    screen.override(conn, run_id, "arxiv:2101.00002", "exclude", "tester")
    fake = graph()
    citations.run(conn, source(fake), run_id, full_limit=2)
    assert sorted(fake.looked_up) == ["2101.00001", "2101.00003"]


def test_openalex_out_of_quota_ends_the_pass_and_the_next_run_picks_up(conn, monkeypatch):
    monkeypatch.setattr(citations, "WORKERS", 1)
    run_id = make_run(conn)
    fake = graph()
    fake.quota = True
    result = citations.run(conn, source(fake), run_id)
    assert result.quota and fake.looked_up == ["2101.00001"]
    assert result.errors == [
        "OpenAlex is out of quota or rate-limited: full citations fetched for 0 of 4 kept papers; "
        "the rest are looked up when the stage runs again"
    ]
    assert len(citations.edges(conn, run_id)["citations"]) == 1  # the archive's
    fake.quota, fake.looked_up = False, []
    again = citations.run(conn, source(fake), run_id)
    assert fake.looked_up == ["2101.00001", "2101.00002", "2101.00003", "2101.00004"]
    assert again.edges == 3 and again.looked_up == 4  # the archive was not asked again


EIGHT = [paper(f"2101.0001{n}", 100 - n) for n in range(8)]


def test_failures_in_a_row_end_the_full_pass_instead_of_waiting_out_every_paper(conn, monkeypatch):
    monkeypatch.setattr(citations, "WORKERS", 1)
    run_id = make_run(conn, EIGHT)
    fake = qf.Essaies()
    fake.failing = {p["key"][6:]: 504 for p in EIGHT}
    result = citations.run(conn, source(fake), run_id)
    assert fake.looked_up == ["2101.00010", "2101.00011", "2101.00012"]
    assert result.failed == 3 and len(result.errors) == 4
    assert result.errors[-1].startswith("essaies failed 3 attempts in a row; the last: essaies answered HTTP 504")
    assert result.errors[-1].endswith("full citations fetched for 0 of 8 kept papers; "
                                      "the rest are looked up when the stage runs again")
    evs = [e["data"] for e in runs.events(conn, run_id) if e["type"] == "error"]
    assert [e.get("key") for e in evs] == ["arxiv:2101.00010", "arxiv:2101.00011", "arxiv:2101.00012", None]
    fake.failing, fake.looked_up = {}, []
    citations.run(conn, source(fake), run_id)
    assert len(fake.looked_up) == 8  # none was saved as looked up


def test_a_whole_archive_batch_failing_stops_the_stage(conn):
    papers = [paper(f"2101.{n:05d}") for n in range(1, 251)]
    run_id = make_run(conn, papers)
    fake = qf.Essaies()

    def refuse(n: int) -> None:
        raise httpx.ConnectError("refused")

    fake.on_batch = refuse
    result = citations.run(conn, source(fake), run_id)
    assert fake.batches == 1 and fake.looked_up == [] and result.down
    # One problem, with the reason, not one per batch.
    assert result.errors == [
        "essaies is not answering citation lookups (essaies could not be reached: refused), so 250 papers were "
        "not looked up and the full lookups were skipped; they are looked up when the stage runs again",
    ]
    problems = [e["data"] for e in runs.events(conn, run_id) if e["type"] == "error"]
    assert [p["code"] for p in problems] == ["essaies_unreachable"]
    fake.on_batch = lambda n: None
    again = citations.run(conn, source(fake), run_id)
    # The three batches left (the first is asked again), then the full lookups.
    assert fake.batches == 4 and again.looked_up == 250 + 250 and len(fake.looked_up) == 250


def test_a_failure_between_answers_does_not_end_the_full_pass(conn, monkeypatch):
    monkeypatch.setattr(citations, "WORKERS", 1)
    run_id = make_run(conn, EIGHT)
    fake = qf.Essaies()
    fake.failing = {"2101.00010": 502, "2101.00012": 504, "2101.00014": 500}
    result = citations.run(conn, source(fake), run_id)
    assert len(fake.looked_up) == 8 and result.failed == 3 and result.looked_up == 8 + 5


def test_an_unreachable_essaies_ends_the_full_pass_at_once(conn):
    run_id = make_run(conn, EIGHT)
    fake = qf.Essaies()
    fake.down = True
    result = citations.run(conn, source(fake), run_id)
    # Once one could not connect, the client stops asking: at most the lookups already on their way.
    assert 1 <= len(fake.looked_up) <= citations.WORKERS
    assert result.errors[-1].startswith("essaies could not be reached: connection refused: full citations fetched for 0")


def test_lookups_go_several_at_a_time_and_stop_with_the_quota(conn):
    run_id = make_run(conn, EIGHT)
    fake = qf.Essaies()
    fake.quota = True
    result = citations.run(conn, source(fake), run_id)
    # Three failures in a row and the client stops asking, so not every worker reaches essaies.
    assert 3 <= len(fake.looked_up) <= citations.WORKERS == 4 and result.quota


def test_a_full_limit_of_none_asks_the_archive_only(conn):
    run_id = make_run(conn)
    fake = graph()
    result = citations.run(conn, source(fake, features=["batch_lookup"]), run_id, full_limit=0)
    assert fake.looked_up == [] and result.errors == [] and result.edges == 1


def test_an_essaies_without_full_references_is_asked_to_upgrade(conn):
    run_id = make_run(conn)
    fake = graph()
    result = citations.run(conn, source(fake, features=["batch_lookup"]), run_id)
    assert result.errors == [citations.UPGRADE] and fake.looked_up == []
    error = next(e for e in runs.events(conn, run_id) if e["type"] == "error")
    assert error["data"] == {"stage": "expand", "message": citations.UPGRADE, "fatal": False, "code": "essaies_old"}
    assert citations.edges(conn, run_id)["citations"] == [["arxiv:2101.00001", "arxiv:2101.00002"]]


def test_a_backfill_adds_no_events(conn):
    run_id = make_run(conn)
    before = len(runs.events(conn, run_id))
    result = citations.run(conn, source(graph()), run_id, events=False)
    assert len(runs.events(conn, run_id)) == before and result.edges == 3


def test_a_paper_without_an_arxiv_id_or_doi_is_not_looked_up():
    assert citations.lookup_id({"key": "openalex:W1", "keys": ["openalex:W1"]}) is None
    assert citations.lookup_id({"key": "openalex:W1", "keys": ["openalex:W1", "doi:10.1/x", "arxiv:2101.00001"]}) == (
        "2101.00001")


# --- expansion ---------------------------------------------------------------------------


def with_plan(conn, run_id: str) -> None:
    plan = {**qf.replay.sample(qf.SAMPLE).plan, "run_id": run_id, "version": 1, "parent_version": None}
    conn.execute(
        "INSERT INTO question_plans (run_id, version, source, plan, created_at) VALUES (?, 1, ?, ?, ?)",
        (run_id, plan["source"], json.dumps(plan), plan["created_at"]),
    )


def expansion_graph() -> qf.Essaies:
    fake = qf.Essaies()
    # Both included papers cite 2201.00010; 2 also cites 2201.00011; the excluded 3 is
    # cited by 2201.00012, which is no pick; 4's reference is a candidate already.
    fake.archive = {"2101.00001": (["2201.00010"], []), "2101.00002": (["2201.00010", "2201.00011"], []),
                    "2101.00003": ([], ["2201.00012"]), "2101.00004": (["2101.00001"], [])}
    return fake


def included(conn, run_id: str) -> None:
    for key in ("arxiv:2101.00001", "arxiv:2101.00002"):
        screen.override(conn, run_id, key, "include", "tester")
    for key in ("arxiv:2101.00003", "doi:10.1000/four"):
        screen.override(conn, run_id, key, "exclude", "tester")


def test_picks_rank_by_seeds_then_citations_within_the_caps(conn):
    run_id = make_run(conn)
    included(conn, run_id)
    citations.run(conn, source(expansion_graph()), run_id, full_limit=0)
    picks = expand.pick(conn, run_id)
    assert [m["paper"]["key"] for m in picks] == ["arxiv:2201.00010", "arxiv:2201.00011"]
    assert picks[0]["provenance"] == [{"via": "cites", "from_key": "arxiv:2101.00001"},
                                      {"via": "cites", "from_key": "arxiv:2101.00002"}]
    assert [m["paper"]["key"] for m in expand.pick(conn, run_id, total=1)] == ["arxiv:2201.00010"]
    # Each seed adds one: 1 adds 2201.00010, 2 then adds 2201.00011.
    assert len(expand.pick(conn, run_id, per_paper=1)) == 2


def test_the_picks_become_candidates_screened_once(conn):
    run_id = make_run(conn)
    with_plan(conn, run_id)
    included(conn, run_id)
    citations.run(conn, source(expansion_graph()), run_id, full_limit=0)
    model = qf.Model(keep=r"00010")
    llm = model.llm()
    result = expand.run(conn, llm, run_id)
    assert (result.added, result.seeds, result.screened) == (2, 2, True)
    papers = {p["paper"]["key"]: p for p in service.papers(conn, run_id)}
    assert len(papers) == 6
    assert papers["arxiv:2201.00010"]["decision"] == "include"
    assert papers["arxiv:2201.00011"]["decision"] == "exclude"
    assert papers["arxiv:2201.00011"]["provenance"] == [{"via": "cites", "from_key": "arxiv:2101.00002"}]
    assert model.screened == ["Paper 2201.00010", "Paper 2201.00011"]
    evs = runs.events(conn, run_id)
    assert [m for e in evs for m in errors("question_event", e)] == []
    assert [e["data"]["paper"]["key"] for e in evs if e["type"] == "expand"] == [
        "arxiv:2201.00010", "arxiv:2201.00011"]
    assert not [e for e in evs if e["type"] == "stage"]  # the flow frames the stage
    assert ["arxiv:2101.00002", "arxiv:2201.00011"] in citations.edges(conn, run_id)["citations"]
    assert service.counts(conn, run_id)["candidates"] == 6
    again = expand.run(conn, llm, run_id)  # a resumed stage: nothing picked or asked again
    assert again.added == 2 and model.calls == 1
    assert len(service.papers(conn, run_id)) == 6


def test_nothing_is_added_without_an_included_paper(conn):
    run_id = make_run(conn)
    citations.run(conn, source(expansion_graph()), run_id, full_limit=0)
    assert expand.run(conn, None, run_id) == expand.Result()


def test_an_expansion_interrupted_before_its_events_keeps_nothing_and_picks_again(conn, monkeypatch):
    run_id = make_run(conn)
    with_plan(conn, run_id)
    included(conn, run_id)
    citations.run(conn, source(expansion_graph()), run_id, full_limit=0)
    emit = runs.emit

    def interrupted(c, r, evs):
        evs = list(evs)
        if any(kind == "expand" for kind, _ in evs):
            raise KeyboardInterrupt  # Ctrl-C between the writes
        return emit(c, r, evs)

    monkeypatch.setattr(runs, "emit", interrupted)
    with pytest.raises(KeyboardInterrupt):
        expand.run(conn, None, run_id)
    monkeypatch.setattr(runs, "emit", emit)
    assert not sets.Store(conn, run_id).has("s1.expansion")
    assert service.candidates_set(conn, run_id) == "s1.candidates"
    assert expand.run(conn, None, run_id).added == 2
    assert service.counts(conn, run_id)["candidates"] == 6


def test_the_cli_backfills_a_run_s_citations_without_events(data, monkeypatch, capsys):
    from isbe import cli

    with closing(db.connect(data / "isbe.db")) as c:
        run_id = make_run(c)
        before = len(runs.events(c, run_id))
    fake = graph()
    monkeypatch.setattr(sources, "from_settings", lambda engine="openalex": source(fake))
    assert cli.main(["--data", str(data), "question-citations", run_id, "--full", "1"]) == 0
    out = capsys.readouterr()
    assert out.out.strip() == f'question-citations {{"edges":1,"looked_up":5,"run_id":"{run_id}"}}'
    assert fake.looked_up == ["2101.00001"]
    with closing(db.connect(data / "isbe.db")) as c:
        assert len(runs.events(c, run_id)) == before
    assert cli.main(["--data", str(data), "question-citations", "run-nope"]) == 1
    assert "No such question run" in capsys.readouterr().err
