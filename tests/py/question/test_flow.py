"""A question run through its steps (`isbe.question.flow`, `queue`, `service`) on a
sample's recorded plan reply and essaies searches with a scripted screening model:
the events of a whole run against the contract, the plan review, editing the plan,
pause and resume in the middle of a stage, cancel, a dead worker's run, a rerun
with its overrides, a failed search, the CLI and the queue's worker thread."""

import dataclasses
import time
from contextlib import closing
from pathlib import Path

import pytest
import question_flow as qf
from question_schemas import errors
from test_search import found

from isbe import cli
from isbe.core import db
from isbe.core.errors import HttpError
from isbe.generation import Manager
from isbe.question import (
    citations,
    expand,
    flow,
    graph,
    plan,
    queue,
    runs,
    screen,
    service,
)


def connect(root: Path):
    return closing(db.connect(root / "isbe.db"))


def ask(root: Path, **body) -> str:
    with connect(root) as conn:
        run, created = service.ask(root, conn, "local", {"question": question(), "lang": "en", "engine": "openalex", **body})
    assert created
    return run["run_id"]


def question() -> str:
    return qf.replay.sample(qf.SAMPLE).question["question"]


def events(root: Path, run_id: str) -> list[dict]:
    with connect(root) as conn:
        return runs.events(conn, run_id)


def summary(root: Path, run_id: str) -> dict:
    with connect(root) as conn:
        return service.summary(root, conn, run_id)


def outline(evs: list[dict]) -> list[str]:
    """The events that drive the page's states, without hits and verdicts."""
    out = []
    for e in evs:
        d = e["data"]
        match e["type"]:
            case "state":
                out.append(f"state:{d['state']}")
            case "stage":
                out.append(f"{d['stage']}:{d['state']}")
            case "done":
                out.append(f"done:{d['status']}")
            case "plan":
                out.append("plan")
            case "error":
                out.append("error")
    return out


def test_a_run_goes_from_question_to_done_and_every_event_fits_the_contract(data):
    svc = qf.services()
    run_id = ask(data, review=False)
    assert summary(data, run_id)["status"] == "queued"
    assert qf.drain(data, svc) == 2  # the plan, then search to the end
    evs = events(data, run_id)
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
    assert [m for e in evs for m in errors("question_event", e)] == []
    assert outline(evs) == [
        "state:queued", "state:running", "plan:begin", "plan", "plan:end", "search:begin", "search:end",
        "screen:begin", "screen:end", "expand:begin", "expand:end", "graph:begin", "graph:end", "state:done", "done:completed",
    ]
    kinds = {e["type"] for e in evs}
    assert {"hit", "verdict"} <= kinds
    built = [e["data"] for e in evs if e["type"] == "graph"]
    with connect(data) as conn:
        doc = graph.get(conn, run_id)
    assert built == [graph.summary(doc)] and built[0]["nodes"] > 0
    run = summary(data, run_id)
    counts = run["counts"]
    assert run["status"] == "done" and run["stage"] is None and not run["degraded"]
    assert counts["candidates"] == len({e["data"]["paper"]["key"] for e in evs if e["type"] == "hit"}) - sum(
        len(e["data"]["merged"]) for e in evs if e["type"] == "dedup")
    assert counts["included"] + counts["excluded"] == counts["candidates"] > 400
    assert counts["included"] > 0 and counts["excluded"] > 0
    assert queue.get(data, run_id).state == "finished"
    with connect(data) as conn:
        papers = service.papers(conn, run_id)
        csv = service.export_csv(conn, run_id)
    assert len(papers) == counts["candidates"] and all(p["decided_by"] == "model" for p in papers)
    assert csv.splitlines()[0].startswith("key,title,year") and len(csv.splitlines()) == len(papers) + 1


def test_the_plan_review_waits_for_the_countdown_a_hold_or_start_now(data):
    svc = qf.services(countdown=60)
    run_id = ask(data)
    assert qf.drain(data, svc) == 1  # the plan; the search waits
    run = summary(data, run_id)
    assert run["status"] == "running" and run["stage"] == "plan"
    assert run["review"]["active"] and run["review"]["ends_at"] > db.now()
    assert not queue.pending(data)
    queue.hold(data, run_id)
    assert summary(data, run_id)["review"] == {"on": True, "active": True, "ends_at": None}
    queue.start(data, run_id)
    assert qf.drain(data, svc) == 1
    assert summary(data, run_id)["status"] == "done"
    with pytest.raises(HttpError) as late:
        queue.start(data, run_id)
    assert late.value.status == 409


def test_a_reviewed_plan_waits_for_a_person_without_a_timer(data):
    svc = qf.services()  # the product's services: no countdown
    run_id = ask(data)
    assert qf.drain(data, svc) == 1
    time.sleep(0.3)
    assert qf.drain(data, svc) == 0  # nothing starts by itself
    assert summary(data, run_id)["review"] == {"on": True, "active": True, "ends_at": None}
    queue.start(data, run_id)
    qf.drain(data, svc)
    assert summary(data, run_id)["status"] == "done"


def test_a_countdown_that_ends_starts_the_search(data):
    svc = qf.services(countdown=0.2)
    run_id = ask(data)
    qf.drain(data, svc)
    time.sleep(0.3)
    qf.drain(data, svc)
    assert summary(data, run_id)["status"] == "done"


def test_run_edited_plan_searches_with_the_new_version(data):
    svc = qf.services(countdown=60)
    run_id = ask(data)
    qf.drain(data, svc)
    with connect(data) as conn:
        plan = service.edit_plan(data, conn, run_id, {"changes": {"rationale": "edited by hand"}})
    assert plan["version"] == 2 and plan["source"] == "human" and plan["rationale"] == "edited by hand"
    qf.drain(data, svc)
    with connect(data) as conn:
        versions = {r[0] for r in conn.execute("SELECT plan_version FROM question_queries WHERE run_id = ?", (run_id,))}
        with pytest.raises(HttpError) as late:
            service.edit_plan(data, conn, run_id, {"changes": {"year_from": 2020}})
    assert versions == {2} and late.value.status == 409
    assert [e["data"]["plan"]["version"] for e in events(data, run_id) if e["type"] == "plan"] == [1, 2]


def test_pause_in_the_middle_of_the_search_and_resume_where_it_stopped(data):
    essaies = qf.Essaies()
    svc = qf.services(essaies=essaies)
    run_id = ask(data, review=False)

    def pause_at_second(query: str) -> None:
        if len(essaies.searched) == 2:
            with connect(data) as conn:
                queue.pause(data, conn, run_id)
            assert summary(data, run_id)["pausing"]

    essaies.before = pause_at_second
    qf.drain(data, svc)
    run = summary(data, run_id)
    assert run["status"] == "paused" and run["stage"] == "search" and not run["pausing"]
    asked = list(essaies.searched)
    essaies.before = lambda q: None
    with connect(data) as conn:
        queue.resume(data, conn, run_id)
    qf.drain(data, svc)
    assert summary(data, run_id)["status"] == "done"
    assert len(essaies.searched) == len(set(essaies.searched)) == 5 and essaies.searched[:2] == asked
    evs = events(data, run_id)
    assert outline(evs).count("search:begin") == 1
    assert "state:paused" in outline(evs) and [m for e in evs for m in errors("question_event", e)] == []


def test_pause_in_the_middle_of_the_screening_and_resume_without_judging_twice(data):
    model = qf.Model()
    svc = qf.services(model, screen={"batch_papers": 50})
    run_id = ask(data, review=False)

    def pause_at_third(n: int) -> None:
        if n == 3:
            with connect(data) as conn:
                queue.pause(data, conn, run_id)

    model.on_screen = pause_at_third
    qf.drain(data, svc)
    run = summary(data, run_id)
    assert run["status"] == "paused" and run["stage"] == "screen"
    assert model.calls == 3
    with connect(data) as conn:
        queue.resume(data, conn, run_id)
    qf.drain(data, svc)
    run = summary(data, run_id)
    assert run["status"] == "done" and not run["degraded"]
    assert len(model.screened) == run["counts"]["candidates"]
    with connect(data) as conn:
        batches = [r[0] for r in conn.execute("SELECT DISTINCT batch_id FROM screen_verdicts WHERE run_id = ?", (run_id,))]
    assert len(batches) == len(set(batches)) == model.calls
    assert outline(events(data, run_id)).count("screen:begin") == 1


class CitesTwo(dict):
    """An archive where every paper cites the same two papers outside the run."""

    def __contains__(self, ident: object) -> bool:
        return True

    def __getitem__(self, ident: str) -> tuple[list[str], list[str]]:
        return ["2601.00001", "2601.00002"], []


def test_the_expand_stage_adds_one_hop_and_screens_it_unless_turned_off(data):
    essaies = qf.Essaies()
    essaies.archive = CitesTwo()
    model = qf.Model(keep=r"neural|network|learning|operator|physics|2601\.00001")
    svc = qf.services(model, essaies)
    run_id = ask(data, review=False)
    qf.drain(data, svc)
    evs = events(data, run_id)
    assert [m for e in evs for m in errors("question_event", e)] == []
    assert outline(evs)[-6:] == ["expand:begin", "expand:end", "graph:begin", "graph:end", "state:done", "done:completed"]
    added = [e["data"] for e in evs if e["type"] == "expand"]
    assert [a["paper"]["key"] for a in added] == ["arxiv:2601.00001", "arxiv:2601.00002"]
    assert all(a["via"] == "cites" and a["new"] for a in added)
    with connect(data) as conn:
        papers = {p["paper"]["key"]: p for p in service.papers(conn, run_id)}
        cited = citations.edges(conn, run_id)["citations"]
    assert papers["arxiv:2601.00001"]["decision"] == "include"
    assert papers["arxiv:2601.00002"]["decision"] == "exclude"
    assert model.screened[-2:] == ["Paper 2601.00001", "Paper 2601.00002"]
    assert summary(data, run_id)["counts"]["candidates"] == len(papers)
    # Every candidate essaies can look up cites both picks: an edge to each.
    looked_up = [k for k, p in papers.items() if citations.lookup_id(p["paper"]) and not k.startswith("arxiv:2601.")]
    assert len(cited) == 2 * len(looked_up) > 800
    assert [e["type"] for e in evs][-7:-3] == ["citations", "stage", "stage", "graph"]  # the edges to the picks

    off = ask(data, review=False, expand=False)
    qf.drain(data, svc)
    assert not [e for e in events(data, off) if e["type"] == "expand"]
    assert summary(data, off)["options"]["expand"] is False


def test_a_graph_that_cannot_be_built_ends_the_run_partial_with_its_papers(data, monkeypatch, caplog):
    def broken(conn, run_id):
        raise KeyError("no paper record under abc")

    monkeypatch.setattr(graph, "build", broken)
    run_id = ask(data, review=False, expand=False)
    qf.drain(data, qf.services())
    evs = events(data, run_id)
    assert [m for e in evs for m in errors("question_event", e)] == []
    assert outline(evs)[-6:] == ["expand:end", "graph:begin", "error", "graph:end", "state:done", "done:partial"]
    failed = next(e["data"] for e in evs if e["type"] == "error")
    assert (failed["stage"], failed["code"], failed["fatal"]) == ("graph", "stage_failed", False)
    assert "isbe question-graph" in failed["message"] and "no paper record" in failed["message"]
    # The trace is logged: there is no upstream to blame.
    assert any(r.exc_info and "graph could not be built" in r.getMessage() for r in caplog.records)
    run = summary(data, run_id)
    assert run["status"] == "done" and run["counts"]["included"] > 0
    assert run["problem"]["stage"] == "graph"
    with connect(data) as conn:
        assert graph.get(conn, run_id) is None


def test_the_expand_stage_on_recorded_citations(data):
    """essaies' real answers for the sample's candidates (`replay.py --citations`),
    every one kept: the archive batches and the full lookups of the 20 most cited."""
    essaies = qf.Essaies(recorded_citations=True)
    svc = qf.services(qf.Model(keep=qf.KEEP_ALL), essaies)
    run_id = ask(data, review=False, citations_full=20)
    qf.drain(data, svc)
    evs = events(data, run_id)
    assert [m for e in evs for m in errors("question_event", e)] == []
    run = summary(data, run_id)
    assert run["status"] == "done" and not run["degraded"]
    assert len(essaies.looked_up) == 20
    added = [e for e in evs if e["type"] == "expand"]
    with connect(data) as conn:
        cited = citations.edges(conn, run_id)["citations"]
        papers = service.papers(conn, run_id)
    expanded = [p for p in papers if all(f["via"] != "search" for f in p["provenance"])]
    assert len(added) == len(expanded) == expand.TOTAL  # the sample's 20 lookups give more than 200
    assert len(cited) == 909  # 5 archive batches and 20 full lookups (16 found), recorded 2026-10-26
    assert {f["via"] for p in expanded for f in p["provenance"]} == {"cites", "cited_by"}
    # Hits count what the search found; the 200 added papers are candidates, not hits.
    assert (run["counts"]["hits"], run["counts"]["candidates"]) == (500, 692)


# The must-find papers one hop of citations adds to the hand-written plan's search,
# from each sample's recorded lookups (`replay.py --citations`, 2026-10-26): every
# search result kept (`KEEP_ALL`), so every one is a seed and the full lookups are
# of the 20 most cited; 25 of those 100 lookups found no work in essaies' mirror.
# The search finds 34 of the 44; the expansion picks 9 more. An upper bound: a
# model's screening keeps fewer seeds.
EXPANSION_FINDS = {
    "radar-nowcasting-dl": (8, ["MetNet: A Neural Weather Model for Precipitation Forecasting"]),
    "ml-medium-range-weather": (9, ["A Foundation Model for the Earth System"]),
    "diffusion-image-generation": (7, [
        "Deep Unsupervised Learning using Nonequilibrium Thermodynamics",
        "Denoising Diffusion Implicit Models",
        "Scalable Diffusion Models with Transformers",
    ]),
    "neural-pde-solvers": (5, [
        "Physics-informed machine learning",
        "Neural Operator: Learning Maps Between Function Spaces",
        "Understanding and mitigating gradient pathologies in physics-informed neural networks",
    ]),
    "green-space-mental-health": (5, ["View Through a Window May Influence Recovery from Surgery"]),
}


@pytest.mark.parametrize("sample_id", list(EXPANSION_FINDS))
def test_the_expansion_finds_must_find_papers_the_search_missed(data, sample_id):
    s = qf.replay.sample(sample_id)
    svc = qf.services(qf.Model(sample_id, keep=qf.KEEP_ALL), qf.Essaies(sample_id, recorded_citations=True))
    with connect(data) as conn:
        run, _ = service.ask(data, conn, "local", {"question": s.question["question"], "lang": s.question["lang"],
                                                   "engine": "openalex", "review": False, "citations_full": 20})
    qf.drain(data, svc)
    with connect(data) as conn:
        papers = service.papers(conn, run["run_id"])
    searched = [p for p in papers if any(f["via"] == "search" for f in p["provenance"])]
    expanded = [p for p in papers if all(f["via"] != "search" for f in p["provenance"])]
    assert (len(found(s.question, searched)), found(s.question, expanded)) == EXPANSION_FINDS[sample_id]


def test_the_expand_options_set_the_caps_and_the_full_pass(data):
    essaies = qf.Essaies()
    essaies.archive = CitesTwo()
    svc = qf.services(essaies=essaies)
    run_id = ask(data, review=False, expand_total=1, citations_full=0)
    qf.drain(data, svc)
    assert [e["data"]["paper"]["key"] for e in events(data, run_id) if e["type"] == "expand"] == ["arxiv:2601.00001"]
    assert essaies.looked_up == []  # no full lookups
    options = summary(data, run_id)["options"]
    assert (options["expand_total"], options["citations_full"], options["expand_per_paper"]) == (1, 0, 20)
    with connect(data) as conn:
        for bad in ({"expand_total": -1}, {"expand_per_paper": 101}, {"citations_full": "all"}, {"expand": 1}):
            with pytest.raises(HttpError) as refused:
                service.ask(data, conn, "local", {"question": question(), "lang": "en", **bad})
            assert refused.value.status == 400


def screening_calls(data: Path) -> int:
    """How many screening calls the sample's run makes before the expansion's."""
    for sub in ("topics", "memory", "reports"):
        (data / sub).mkdir(parents=True)
    model = qf.Model()
    run_id = ask(data, review=False, expand=False)
    qf.drain(data, qf.services(model))
    assert summary(data, run_id)["status"] == "done"
    return model.calls


@pytest.mark.parametrize("where", ["lookups", "expansion screening"])
def test_pause_in_the_middle_of_the_expand_stage_and_resume_where_it_stopped(data, tmp_path, where):
    essaies = qf.Essaies()
    essaies.archive = CitesTwo()
    model = qf.Model()
    # One pick per screening call, so a pause can fall between the two.
    svc = qf.services(model, essaies, expand={"screening": {"batch_papers": 1}})
    calls = screening_calls(tmp_path / "count") if where != "lookups" else 0
    run_id = ask(data, review=False)

    def pause() -> None:
        with connect(data) as conn:
            queue.pause(data, conn, run_id)

    if where == "lookups":
        essaies.on_batch = lambda n: pause() if n == 1 else None
    else:
        model.on_screen = lambda n: pause() if n == calls + 1 else None
    qf.drain(data, svc)
    run = summary(data, run_id)
    assert run["status"] == "paused" and run["stage"] == "expand"
    batches = essaies.batches
    with connect(data) as conn:
        queue.resume(data, conn, run_id)
    qf.drain(data, svc)
    evs = events(data, run_id)
    assert summary(data, run_id)["status"] == "done"
    assert outline(evs).count("expand:begin") == 1 and outline(evs).count("expand:end") == 1
    assert [e["data"]["paper"]["key"] for e in evs if e["type"] == "expand"] == ["arxiv:2601.00001", "arxiv:2601.00002"]
    with connect(data) as conn:
        decided = screen.decisions(conn, run_id)
        papers = service.papers(conn, run_id)
    assert {decided[k]["decision"] for k in ("arxiv:2601.00001", "arxiv:2601.00002")} == {"exclude"}
    if where == "lookups":
        # One batch was answered before the pause; it is not asked again.
        searched = [p for p in papers if citations.lookup_id(p["paper"]) and not p["paper"]["key"].startswith("arxiv:2601.")]
        assert batches == 1 and essaies.batches == -(-len(searched) // 100)
    assert len(model.screened) == len(papers)  # nobody judged twice


def test_cancel_in_the_expand_stage_keeps_the_citations_found(data):
    essaies = qf.Essaies()
    essaies.archive = CitesTwo()
    svc = qf.services(essaies=essaies)
    run_id = ask(data, review=False)

    def cancel(n: int) -> None:
        if n == 2:
            with connect(data) as conn:
                queue.cancel(data, conn, run_id)

    essaies.on_batch = cancel
    qf.drain(data, svc)
    assert outline(events(data, run_id))[-2:] == ["state:cancelled", "done:cancelled"]
    with connect(data) as conn:
        stored = conn.execute("SELECT count(*) FROM question_citations WHERE run_id = ?", (run_id,)).fetchone()[0]
        assert stored == 200  # the two batches answered before the cancel took effect
        assert not [e for e in runs.events(conn, run_id) if e["type"] == "expand"]


def test_a_rerun_carries_the_overrides_of_papers_the_expansion_found(data):
    essaies = qf.Essaies()
    essaies.archive = CitesTwo()
    svc = qf.services(essaies=essaies)
    run_id = ask(data, review=False)
    qf.drain(data, svc)
    with connect(data) as conn:
        screen.override(conn, run_id, "arxiv:2601.00002", "include", "hank", "a key dataset paper")
        new = service.rerun(data, conn, "local", run_id, "hank", {"changes": {"rationale": "rerun"}})
    qf.drain(data, svc)
    with connect(data) as conn:
        decided = screen.decisions(conn, new["run_id"])
        overrides = screen.overrides(conn, new["run_id"])
    assert decided["arxiv:2601.00002"]["decision"] == "include" and decided["arxiv:2601.00002"]["source"] == "override"
    assert len(overrides) == 1


def test_cancel_in_the_middle_of_a_stage_keeps_what_was_found(data):
    essaies = qf.Essaies()
    svc = qf.services(essaies=essaies)
    run_id = ask(data, review=False)

    def cancel_at_third(query: str) -> None:
        if len(essaies.searched) == 3:
            with connect(data) as conn:
                queue.cancel(data, conn, run_id)

    essaies.before = cancel_at_third
    qf.drain(data, svc)
    evs = events(data, run_id)
    assert outline(evs)[-2:] == ["state:cancelled", "done:cancelled"]
    assert summary(data, run_id)["counts"]["hits"] > 0 and len(essaies.searched) == 3
    assert queue.get(data, run_id).state == "finished"
    with connect(data) as conn, pytest.raises(HttpError):
        queue.cancel(data, conn, run_id)


def test_cancel_and_pause_of_a_waiting_run_act_at_once(data):
    svc = qf.services(countdown=60)
    queued = ask(data)
    with connect(data) as conn:
        with pytest.raises(HttpError) as early:
            queue.pause(data, conn, queued)
        queue.cancel(data, conn, queued)
    assert early.value.status == 409
    assert outline(events(data, queued)) == ["state:queued", "state:cancelled", "done:cancelled"]
    reviewing = ask(data)
    qf.drain(data, svc)
    with connect(data) as conn:
        queue.pause(data, conn, reviewing)
        assert service.summary(data, conn, reviewing)["status"] == "paused"
        queue.resume(data, conn, reviewing)
    qf.drain(data, svc)
    assert summary(data, reviewing)["status"] == "done"


def test_a_step_a_dead_worker_left_running_is_paused_and_resumes(data):
    svc = qf.services()
    run_id = ask(data, review=False)
    qf.drain(data, svc)  # done; now leave a second run as a killed worker would
    second = ask(data, review=False)
    assert queue.claim(data).run_id == second
    assert queue.get(data, second).state == "running"
    assert flow.work_one(data, svc) is False  # nothing ready, but the stale step is found
    assert summary(data, second)["status"] == "paused"
    with connect(data) as conn:
        queue.resume(data, conn, second)
    qf.drain(data, svc)
    assert summary(data, second)["status"] == "done"
    assert summary(data, run_id)["status"] == "done"


def test_a_rerun_copies_the_plan_and_carries_the_overrides(data):
    svc = qf.services()
    run_id = ask(data, review=False)
    qf.drain(data, svc)
    with connect(data) as conn:
        papers = service.papers(conn, run_id)
        kept = next(p for p in papers if p["decision"] == "include")["paper"]["key"]
        screen.override(conn, run_id, kept, "exclude", "hank", "not about solvers")
        new = service.rerun(data, conn, "local", run_id, "hank", {"changes": {"rationale": "rerun"}})
    assert new["status"] == "running" and new["options"]["rerun_of"] == run_id
    qf.drain(data, svc)
    with connect(data) as conn:
        decided = screen.decisions(conn, new["run_id"])
        plan = service.summary(data, conn, new["run_id"])
    assert decided[kept]["decision"] == "exclude" and decided[kept]["source"] == "override"
    assert plan["status"] == "done" and plan["counts"]["overridden"] == 1
    assert outline(events(data, new["run_id"]))[:5] == [
        "state:queued", "state:running", "plan:begin", "plan", "plan:end"]


def test_a_search_where_every_query_fails_ends_the_run_failed(data):
    essaies = qf.Essaies()
    essaies.fail = True
    run_id = ask(data, review=False)
    qf.drain(data, qf.services(essaies=essaies))
    evs = events(data, run_id)
    assert outline(evs)[-3:] == ["error", "state:failed", "done:failed"]
    assert evs[-3]["data"]["fatal"] and queue.get(data, run_id).state == "finished"


def test_without_essaies_the_run_fails_with_a_fatal_error(data, monkeypatch):
    monkeypatch.setenv("ISBE_PAPER_SOURCE", "essaies")
    run_id = ask(data, review=False)
    svc = qf.services()
    svc.source = flow.Services().source  # ESSAIES_URL is not set
    qf.drain(data, svc)
    error = next(e for e in events(data, run_id) if e["type"] == "error")
    assert error["data"]["fatal"] and "ESSAIES_URL" in error["data"]["message"]
    assert error["data"]["code"] == "essaies_not_set" and error["data"]["detail"] == "ESSAIES_URL is not set"
    assert summary(data, run_id)["status"] == "failed"


def problems(evs: list[dict]) -> list[tuple[str, str | None, bool]]:
    return [(e["data"]["stage"], e["data"].get("code"), e["data"]["fatal"]) for e in evs if e["type"] == "error"]


def test_an_unreachable_essaies_fails_the_run_before_the_plan(data):
    essaies, model = qf.Essaies(), qf.Model()
    essaies.gone = True
    run_id = ask(data, review=False)
    started = time.monotonic()
    qf.drain(data, qf.services(model, essaies))
    assert time.monotonic() - started < 5
    evs = events(data, run_id)
    assert [m for e in evs for m in errors("question_event", e)] == []
    assert outline(evs) == ["state:queued", "state:running", "plan:begin", "error", "state:failed", "done:failed"]
    assert problems(evs) == [("plan", "essaies_unreachable", True)]
    # One request, and no model time spent.
    assert essaies.requests == 1 and model.requests == 0
    failure = summary(data, run_id)["failure"]
    assert failure["stage"] == "plan" and failure["code"] == "essaies_unreachable"
    assert failure["message"].startswith("essaies could not be reached: [Errno 113] No route to host. Check that essaies")
    assert failure["detail"] == "essaies could not be reached: [Errno 113] No route to host"


def test_essaies_going_mid_search_stops_the_search_at_once(data):
    essaies = qf.Essaies()
    asked = []

    def before(query: str) -> None:
        asked.append(query)
        if len(asked) == 2:
            essaies.gone = True

    essaies.before = before
    run_id = ask(data, review=False)
    qf.drain(data, qf.services(essaies=essaies))
    evs = events(data, run_id)
    # The first query's papers go on to screening; the rest were not asked.
    assert len(asked) == 2
    assert problems(evs)[:2] == [("search", "essaies_unreachable", False)] * 2
    stop = [e["data"]["message"] for e in evs if e["type"] == "error"][1]
    assert "more queries were not asked" in stop
    assert outline(evs)[-1] == "done:partial" and summary(data, run_id)["failure"] is None


def test_essaies_failing_every_query_stops_after_three(data):
    essaies = qf.Essaies()
    run_id = ask(data, review=False)
    svc = qf.services(essaies=essaies)
    assert flow.work_one(data, svc)  # the plan, with essaies up
    essaies.status = 503
    qf.drain(data, svc)
    evs = events(data, run_id)
    assert essaies.requests - 1 == 3  # three searches, then the client stops asking
    assert problems(evs) == [("search", "essaies_down", False)] * 3 + [("search", "essaies_down", True)]
    assert summary(data, run_id)["failure"]["code"] == "essaies_down"


def test_an_unreachable_model_fails_the_plan_with_the_reason(data):
    model = qf.Model()
    model.down = True
    run_id = ask(data, review=False)
    started = time.monotonic()
    svc = qf.services(model)
    llm = dataclasses.replace(model.llm(), retry_connect=False)  # as plans.load_model gives it
    svc.model = lambda: (llm, "")
    qf.drain(data, svc)
    assert time.monotonic() - started < 5
    assert model.requests == 1  # a refused connection is not retried for question work
    evs = events(data, run_id)
    assert problems(evs) == [("plan", "model_unreachable", True)]
    failure = summary(data, run_id)["failure"]
    assert failure["message"].startswith("The model could not be reached (")
    assert failure["detail"] == "The model could not be reached: ConnectError: [Errno 111] Connection refused"
    assert "ISBE_LLM_BASE_URL" in failure["message"]


def test_a_model_that_refuses_the_key_fails_the_plan_and_says_so(data):
    model = qf.Model()
    model.status = 401
    run_id = ask(data, review=False)
    svc = qf.services(model)
    llm = dataclasses.replace(model.llm(), waits=plan.QUESTION_WAITS, _sleep=lambda _: None)
    svc.model = lambda: (llm, "")
    qf.drain(data, svc)
    assert model.requests == 1  # a 401 is not retried
    evs = events(data, run_id)
    assert problems(evs) == [("plan", "model_rejected", True)]
    failure = summary(data, run_id)["failure"]
    assert "check the API key" in failure["message"]
    assert failure["detail"] == "The model refused the request: ProviderError: HTTP 401: model provider rejected the request"


def test_screening_stops_at_the_first_refused_call(data):
    model = qf.Model()
    run_id = ask(data, review=False)
    svc = qf.services(model)
    llm = dataclasses.replace(model.llm(), waits=plan.QUESTION_WAITS, _sleep=lambda _: None)
    svc.model = lambda: (llm, "")
    assert flow.work_one(data, svc)  # the plan, with a working key
    model.status, before = 403, model.requests
    qf.drain(data, svc)
    evs = events(data, run_id)
    screening = [p for p in problems(evs) if p[0] == "screen"]
    assert screening[0] == ("screen", "model_rejected", False) and len(set(screening)) == 1
    assert model.requests - before <= 2  # one refused call in screening, and one if expansion screens again
    assert outline(evs)[-1] == "done:partial"


def test_a_fallback_plan_is_a_problem_and_the_run_is_partial(data):
    run_id = ask(data, review=False)
    essaies = qf.Essaies()
    essaies.unrecorded = [qf.work("2101.00001"), qf.work("2101.00002")]
    svc = qf.services(essaies=essaies)
    svc.model = lambda: (None, "ISBE_LLM_PROVIDER is not set; set it to anthropic, deepseek or openai")
    qf.drain(data, svc)
    evs = events(data, run_id)
    assert [m for e in evs for m in errors("question_event", e)] == []
    details = [e["data"].get("detail") for e in evs if e["type"] == "error"]
    assert details == ["ISBE_LLM_PROVIDER is not set", "ISBE_LLM_PROVIDER is not set; 2 unscreened"]
    assert problems(evs) == [("plan", "model_not_configured", False), ("screen", "model_not_configured", False)]
    first = next(e for e in evs if e["type"] == "error")["data"]["message"]
    assert first.startswith("No model plan (ISBE_LLM_PROVIDER is not set;")
    assert outline(evs)[-1] == "done:partial" and summary(data, run_id)["degraded"]
    problem = summary(data, run_id)["problem"]
    assert problem["stage"] == "plan" and problem["code"] == "model_not_configured" and problem["message"] == first


def test_a_query_essaies_fails_on_its_side_names_essaies_not_the_plan(data):
    run_id = ask(data, review=False)
    essaies = qf.Essaies()
    svc = qf.services(essaies=essaies)
    svc.model = lambda: (None, "ISBE_LLM_PROVIDER is not set")  # one query: the question itself
    assert flow.work_one(data, svc)
    essaies.status = 503
    qf.drain(data, svc)
    evs = events(data, run_id)
    fatal = [e["data"] for e in evs if e["type"] == "error" and e["data"]["fatal"]]
    assert [(f["stage"], f["code"]) for f in fatal] == [("search", "essaies_down")]
    assert fatal[0]["message"].startswith("Every search query failed on essaies' side (the last: essaies answered HTTP 503")
    assert fatal[0]["detail"].startswith("essaies answered HTTP 503")


def test_a_plan_the_model_could_not_write_is_a_plan_fallback(data):
    model = qf.Model()
    model.plans = lambda request: qf.replay._completion("not a plan at all", "stop", "")
    essaies = qf.Essaies()
    essaies.unrecorded = [qf.work("2101.00001")]
    run_id = ask(data, review=False)
    qf.drain(data, qf.services(model, essaies))
    evs = events(data, run_id)
    assert ("plan", "plan_fallback", False) in problems(evs)
    assert outline(evs)[-1] == "done:partial"
    fallback = next(e["data"] for e in evs if e["type"] == "error" and e["data"]["code"] == "plan_fallback")
    assert fallback["detail"] and "question_plan call" not in fallback["detail"]


def test_ask_checks_its_fields_and_dedupes_by_request_id(data):
    with connect(data) as conn:
        for body, message in [
            ({"question": " "}, "Ask a question"),
            ({"question": "q", "lang": "fr"}, "zh or en"),
            ({"question": "q", "year_from": 2020, "year_to": 2010}, "starts after"),
            ({"question": "q", "engine": "google"}, "openalex or arxiv"),
            ({"question": "q", "color": "red"}, "Unknown fields: color"),
        ]:
            with pytest.raises(HttpError) as e:
                service.ask(data, conn, "local", body)
            assert message in e.value.message
        first, new = service.ask(data, conn, "local", {"question": "q", "request_id": "r-1"})
        again, repeat = service.ask(data, conn, "local", {"question": "other", "request_id": "r-1"})
    assert new and not repeat and again["run_id"] == first["run_id"]


def test_the_cli_runs_a_question_to_the_end(data, monkeypatch, capsys):
    svc = qf.services()
    monkeypatch.setattr(flow, "Services", lambda: svc)
    assert cli.main(["--data", str(data), "question", question(), "--lang", "en", "--engine", "openalex"]) == 0
    out = capsys.readouterr().out
    assert "plan v1 (model): 5 queries" in out and "screen end" in out
    assert out.strip().splitlines()[-1].startswith("question done {")


def test_the_queue_worker_thread_runs_question_runs(data):
    svc = qf.services()
    run_id = ask(data, review=False)
    manager = Manager.open(data, questions=svc, read_settings=lambda root: {})
    try:
        deadline = time.monotonic() + 60
        while summary(data, run_id)["status"] != "done":
            assert time.monotonic() < deadline, summary(data, run_id)
            time.sleep(0.2)
    finally:
        manager.close()


def test_the_cli_exits_non_zero_when_the_run_fails(data, monkeypatch, capsys):
    essaies = qf.Essaies()
    essaies.fail = True
    svc = qf.services(essaies=essaies)
    monkeypatch.setattr(flow, "Services", lambda: svc)
    assert cli.main(["--data", str(data), "question", question(), "--lang", "en", "--engine", "openalex"]) == 1
    captured = capsys.readouterr()
    assert "fatal error (search): Every search query failed (the last: essaies answered HTTP 400" in captured.out
    assert "failed" in captured.err


def test_runs_search_openalex_unless_asked_otherwise(data, monkeypatch):
    monkeypatch.setenv("ISBE_PAPER_SOURCE", "essaies")
    with connect(data) as conn:
        run, _ = service.ask(data, conn, "local", {"question": "q"})
        asked, _ = service.ask(data, conn, "local", {"question": "q", "engine": "arxiv"})
    assert (run["options"]["engine"], asked["options"]["engine"]) == ("openalex", "arxiv")


def test_the_arxiv_engine_needs_essaies(data, monkeypatch):
    # No ESSAIES_URL and no ISBE_PAPER_SOURCE: the OpenAlex API, which has no arXiv index.
    with connect(data) as conn, pytest.raises(HttpError, match="arXiv engine searches essaies"):
        service.ask(data, conn, "local", {"question": "q", "engine": "arxiv"})
    monkeypatch.setenv("ESSAIES_URL", "http://essaies.test")  # an existing deployment keeps essaies
    with connect(data) as conn:
        assert service.ask(data, conn, "local", {"question": "q", "engine": "arxiv"})[0]["options"]["engine"] == "arxiv"


def test_with_the_openalex_api_out_of_reach_the_run_fails_before_the_plan(data, monkeypatch):
    # The default paper source; a closed local port stands in for a blocked api.openalex.org.
    monkeypatch.setenv("OPENALEX_API_URL", "http://127.0.0.1:9")
    run_id = ask(data, review=False)
    svc = qf.services()
    svc.source = flow.Services().source
    qf.drain(data, svc)
    error = next(e for e in events(data, run_id) if e["type"] == "error")
    assert error["data"]["fatal"] and error["data"]["code"] == "openalex_unreachable"
    assert error["data"]["message"].startswith("OpenAlex could not be reached: ")
    assert "reaches the OpenAlex API (OPENALEX_API_URL" in error["data"]["message"]
    assert summary(data, run_id)["status"] == "failed"


def test_each_step_and_stage_is_logged_with_its_time(data, caplog):
    caplog.set_level("INFO", logger="isbe.question.flow")
    run_id = ask(data, review=False)
    qf.drain(data, qf.services())
    lines = [r.getMessage() for r in caplog.records if r.name == "isbe.question.flow"]
    assert lines[0] == f"question run {run_id}: plan step started"
    assert lines[1].startswith(f"question run {run_id}: the plan step took ")
    assert [line.split(": ", 1)[1].split(" ended")[0] for line in lines[3:6]] == ["search", "screen", "expand"]
    assert lines[-1].startswith(f"question run {run_id}: the search step, through the graph stage, took ")
    assert "candidates" in lines[-1] and "included" in lines[-1]
