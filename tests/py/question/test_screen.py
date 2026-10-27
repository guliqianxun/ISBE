"""The screening stage (`isbe.question.screen`) on the sample questions' recorded
candidates: batch sizing, reply checking, narrow-tier papers first, splitting and
retrying bad batches, failures left unscreened, the verdict cache, overrides and
their revocation, the screened set's replay, the contracts, and Strata's recorded
verdicts replayed offline (`model.screen.json`)."""

import json
import re

import httpx
import pytest
import replay
from question_schemas import errors

from isbe.core import http
from isbe.core.errors import HttpError
from isbe.core.llm import Llm
from isbe.question import plan as plans
from isbe.question import runs, screen, sets

PAPER = re.compile(r"^\[(\d+)\] (.*)$", re.M)

# Strata's recorded screening (2026-10-23, plan.json criteria): candidates, included,
# and the must-find papers among the candidates and kept by the screening.
RECORDED = {
    "radar-nowcasting-dl": {"candidates": 959, "included": 464, "must_find": 8, "kept": 8},
    "ml-medium-range-weather": {"candidates": 948, "included": 326, "must_find": 10, "kept": 10},
    # The lost one is DDPM: its OpenAlex record carries another paper's abstract.
    "diffusion-image-generation": {"candidates": 1038, "included": 672, "must_find": 9, "kept": 8},
    "neural-pde-solvers": {"candidates": 953, "included": 921, "must_find": 7, "kept": 7},
}


def model(handler) -> Llm:
    env = {
        "ISBE_LLM_PROVIDER": "openai",
        "ISBE_LLM_BASE_URL": "http://127.0.0.1:9/v1",  # never reached: the transport answers
        "ISBE_LLM_NO_AUTH": "true",
        "ISBE_LLM_MODEL_FAST": "fake",
    }
    transport = handler if isinstance(handler, httpx.BaseTransport) else httpx.MockTransport(handler)
    llm = Llm.from_vars(env.get, client=http.Http(5, transport=transport))
    llm._sleep = lambda _: None
    return llm


class Judge:
    """A scripted model: includes the papers whose title matches `keep`, and counts calls."""

    def __init__(self, keep: str = r"neural|network|learning|deep|diffusion|forecast", reply=None) -> None:
        self.keep = re.compile(keep, re.I)
        self.reply = reply  # (user, verdicts) -> reply text, to break replies
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        user = json.loads(request.content)["messages"][1]["content"]
        self.calls.append(user)
        verdicts = [
            {"n": int(n), "decision": "include" if self.keep.search(title) else "exclude",
             "score": 0.8 if self.keep.search(title) else 0.1, "reason": "scripted"}
            for n, title in PAPER.findall(user)
        ]
        text = self.reply(user, verdicts) if self.reply else json.dumps({"verdicts": verdicts})
        return replay._completion(text, "stop", user)


@pytest.fixture
def pde():
    s = replay.sample("neural-pde-solvers")
    conn, run_id, candidates = replay.screening_run(s)
    return conn, run_id, sets.Store(conn, run_id).get(candidates).members


def paper(n: int, abstract: str = "words " * 50) -> dict:
    return {"key": f"doi:10.1/{n}", "keys": [f"doi:10.1/{n}"], "title": f"Paper {n}", "abstract": abstract}


# --- pieces ----------------------------------------------------------------------------


def test_batches_stop_at_the_paper_count_or_the_token_budget() -> None:
    papers = [paper(n) for n in range(45)]
    assert [len(b) for b in screen.batches(papers, 20, 10**6)] == [20, 20, 5]
    one = screen.estimate(screen.paper_text(1, papers[0]))
    assert [len(b) for b in screen.batches(papers[:10], 20, one * 3)] == [3, 3, 3, 1]
    long = [paper(1, "x" * 50000), paper(2), paper(3)]
    assert [len(b) for b in screen.batches(long, 20, one * 3)] == [1, 2]


def test_a_paper_shows_its_title_year_venue_and_a_clipped_abstract() -> None:
    text = screen.paper_text(3, {"title": "A  title", "year": 2021, "venue": "JCP", "abstract": "y" * 4000})
    head, meta, abstract = text.split("\n")
    assert head == "[3] A title" and meta == "2021 · JCP"
    assert len(abstract) == len("Abstract: ") + screen.ABSTRACT_CHARS and abstract.endswith("…")
    assert screen.paper_text(1, {"title": "T"}).endswith("Abstract: (none)")


def test_a_reply_is_checked_entry_by_entry() -> None:
    reply = json.dumps({"verdicts": [
        {"n": 1, "decision": "include", "score": 0.9, "reason": "  on   topic "},
        {"n": 1, "decision": "exclude", "score": 0.1, "reason": "a repeat"},
        {"n": "[2]", "decision": "Exclude", "score": 7, "reason": "x" * 400},
        {"n": 3, "decision": "maybe", "score": 0.5, "reason": "?"},
        {"n": 9, "decision": "include", "score": 0.5, "reason": "out of range"},
        {"n": True, "decision": "include"},
    ]})
    found = screen.parse(reply, 3)
    assert found[0] == ("include", 0.9, "on topic")
    assert found[1][:2] == ("exclude", None) and len(found[1][2]) == screen.REASON_CHARS
    assert set(found) == {0, 1}
    fenced = '<think>hmm</think>\n```json\n{"verdicts": [{"n": 1, "decision": "exclude", "score": 0}]}\n```'
    assert screen.parse(fenced, 1) == {0: ("exclude", 0.0, "")}
    assert screen.parse('[{"n": 1, "decision": "include"}]', 1) == {0: ("include", None, "")}
    assert screen.parse('{"verdicts": [{"n": 1, "decision": "inc', 1) == {}
    assert screen.parse("no json", 1) == {}


def test_papers_the_narrow_queries_found_come_first(pde) -> None:
    conn, run_id, members = pde
    plan = plans.get(conn, run_id)
    priorities = {q["id"]: q["priority"] for q in plan["queries"]}
    ordered = screen.order(members, priorities)
    best = [min(priorities[p["query_id"]] for p in m["provenance"]) for m in ordered]
    assert best == sorted(best) and best[0] == 1
    first = [m for m in ordered if best[ordered.index(m)] == 1]
    ranks = [min(p["rank"] for p in m["provenance"] if priorities[p["query_id"]] == 1) for m in first]
    assert ranks == sorted(ranks)


# --- a run -----------------------------------------------------------------------------


def test_every_candidate_gets_a_verdict_and_the_excluded_leave_the_screened_set(pde) -> None:
    conn, run_id, members = pde
    judge = Judge()
    result = screen.run(conn, model(judge), run_id)
    assert result.included + result.excluded == len(members) and result.unscreened == 0
    assert result.calls == result.batches == len(judge.calls)
    assert all(len(PAPER.findall(c)) <= screen.BATCH_PAPERS for c in judge.calls)
    assert not result.partial and result.input_tokens > 0 and result.output_tokens > 0

    found = screen.decisions(conn, run_id)
    assert {m["paper"]["key"] for m in members} == set(found)
    for d in found.values():
        assert errors("screen_verdict", d["verdict"]) == []
    store = sets.Store(conn, run_id)
    screened = store.get(result.screened)
    kept = {k for k, d in found.items() if d["decision"] == "include"}
    assert {m["paper"]["key"] for m in screened.members} == kept
    assert errors("paper_set", screened.doc(run_id)) == []
    op = store.ops()[-1]
    assert op["op"] == "screen" and op["params"] == {"plan_version": 1, "screen": 1}
    assert errors("set_op", op) == []

    events = runs.events(conn, run_id)
    for event in events:
        assert errors("question_event", event) == []
    kinds = [e["type"] for e in events]
    begin, end = kinds.index("stage", 1), len(kinds) - 1
    assert events[begin]["data"] == {"stage": "screen", "state": "begin"}
    assert events[end]["data"] == {"stage": "screen", "state": "end"}
    assert kinds.count("verdict") == len(members)
    # The narrow tier's papers are decided first.
    plan = plans.get(conn, run_id)
    narrow = {q["id"] for q in plan["queries"] if q["priority"] == 1}
    by_key = {m["paper"]["key"]: m for m in members}
    tiers = [any(p["query_id"] in narrow for p in by_key[k]["provenance"]) for k in result.order]
    assert tiers == sorted(tiers, reverse=True)


def test_the_criteria_go_with_every_batch_and_reasons_follow_the_question_language(pde) -> None:
    conn, run_id, _ = pde
    judge = Judge()
    screen.run(conn, model(judge), run_id)
    header = screen.criteria_text(plans.get(conn, run_id))
    assert all(c.startswith(header + "\n\nPapers:\n\n[1] ") for c in judge.calls)
    assert "Include a paper that:" in header and "Exclude a paper that:" in header
    assert "in English" in screen.system_prompt("en") and "中文" in screen.system_prompt("zh")


def test_a_second_screening_reuses_verdicts_until_the_criteria_change(pde) -> None:
    conn, run_id, members = pde
    screen.run(conn, model(Judge()), run_id)
    again = Judge()
    second = screen.run(conn, model(again), run_id)
    assert again.calls == [] and second.cached == len(members) and second.screen == 2
    assert second.screened == "s1.screened-2"
    queries = plans.get(conn, run_id)["queries"][: plans.MAX_QUERIES]
    plans.edit(conn, run_id, 1, {"queries": queries, "criteria": {"include": ["Neural PDE solvers only"], "exclude": []}})
    changed = Judge(keep="operator")
    third = screen.run(conn, model(changed), run_id)
    assert third.cached == 0 and third.plan_version == 2
    assert sum(1 for c in changed.calls for _ in PAPER.findall(c)) == len(members)
    assert all("Neural PDE solvers only" in c for c in changed.calls)


def test_a_broken_reply_is_split_and_retried_down_to_one_paper(pde) -> None:
    conn, run_id, members = pde
    target = members[0]["paper"]["title"]

    def reply(user: str, verdicts: list) -> str:
        if target.split()[0] in user and " ".join(target.split()) in user:
            return '{"verdicts": [{"n": 1, "decision": "inc'  # cut short
        return json.dumps({"verdicts": verdicts})

    judge = Judge(reply=reply)
    result = screen.run(conn, model(judge), run_id, batch_papers=8)
    assert result.unscreened == 1 and result.included + result.excluded == len(members) - 1
    # 8 → 4 → 2 → 1: three splits of the first batch, each half asked.
    first_batch_calls = [c for c in judge.calls if " ".join(target.split()) in c]
    assert [len(PAPER.findall(c)) for c in first_batch_calls] == [8, 4, 2, 1]
    found = screen.decisions(conn, run_id)[members[0]["paper"]["key"]]
    assert found["decision"] == "unscreened" and found["verdict"]["score"] is None
    assert found["verdict"]["batch_id"] == "c1.b1.1.1.1"
    errs = [e for e in runs.events(conn, run_id) if e["type"] == "error"]
    assert [e["data"]["batch_id"] for e in errs] == ["c1.b1.1.1.1"] and not errs[0]["data"]["fatal"]
    assert result.partial
    # Unscreened stays in the screened set: no recall lost.
    kept = {m["paper"]["key"] for m in sets.Store(conn, run_id).get(result.screened).members}
    assert members[0]["paper"]["key"] in kept


def test_papers_a_reply_leaves_out_are_asked_again(pde) -> None:
    conn, run_id, members = pde
    judge = Judge(reply=lambda user, verdicts: json.dumps({"verdicts": verdicts[: max(1, len(verdicts) // 2)]}))
    result = screen.run(conn, model(judge), run_id, batch_papers=8)
    assert result.unscreened == 0 and result.included + result.excluded == len(members)
    assert [len(PAPER.findall(c)) for c in judge.calls[:4]] == [8, 4, 2, 1]
    batch_ids = [d["verdict"]["batch_id"] for d in screen.decisions(conn, run_id).values()]
    assert [batch_ids.count(b) for b in ("c1.b1", "c1.b1.r", "c1.b1.r.r", "c1.b1.r.r.r")] == [4, 2, 1, 1]


def test_failed_calls_leave_papers_unscreened_and_stop_after_three_in_a_row(pde) -> None:
    conn, run_id, members = pde
    fake = replay.FakeModel().fault("server_error", times=None)
    result = screen.run(conn, model(fake.transport()), run_id)
    # Each failed call is retried twice by the client; screening stops calling after three.
    assert len(fake.calls) == 3 * 3 and result.calls == 3
    assert result.unscreened == len(members) and result.included == result.excluded == 0
    assert len(result.failed_batches) == result.batches and result.partial
    assert len(sets.Store(conn, run_id).get(result.screened).members) == len(members)
    reasons = {d["verdict"]["reason"] for d in screen.decisions(conn, run_id).values()}
    assert any("screening stopped (3 model calls failed in a row)" in r for r in reasons)
    assert any("call failed" in r for r in reasons)
    # One problem per failed call, then one for every batch left, not one per batch.
    problems = [e["data"] for e in runs.events(conn, run_id) if e["type"] == "error"]
    assert [p["code"] for p in problems] == ["model_failed"] * 4
    assert problems[-1]["message"].startswith("Screening stopped: 3 model calls failed in a row. ")
    # The reason is the provider's answer, without the call or the model it named.
    assert [p["detail"].split(";")[0] for p in problems] == ["HTTP 500: model provider failed the request"] * 4
    assert re.fullmatch(r".*; \d+ unscreened", problems[-1]["detail"])
    # A later screening asks again about the unscreened papers.
    again = Judge()
    second = screen.run(conn, model(again), run_id)
    assert second.cached == 0 and second.unscreened == 0 and again.calls


def test_one_failed_call_does_not_stop_the_others(pde) -> None:
    conn, run_id, members = pde
    judge = Judge()
    fake = replay.FakeModel().fault("timeout", times=1)

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            return fake(request)
        except replay.Unrecorded:
            return judge(request)

    result = screen.run(conn, model(handler), run_id)
    # A timeout is not retried by the client: the first batch fails, the rest are judged.
    assert result.failed_batches == ["c1.b1"] and result.calls == result.batches
    assert 0 < result.unscreened <= screen.BATCH_PAPERS
    assert result.included + result.excluded + result.unscreened == len(members)


def test_without_a_model_every_paper_is_unscreened(pde) -> None:
    conn, run_id, members = pde
    result = screen.run(conn, None, run_id, unavailable="ISBE_LLM_PROVIDER is not set")
    assert result.unscreened == len(members) and result.calls == 0
    doc = next(iter(screen.decisions(conn, run_id).values()))["verdict"]
    assert doc["model"] == "none" and "ISBE_LLM_PROVIDER" in doc["reason"]


def test_a_run_without_candidates_cannot_be_screened(pde) -> None:
    conn, _, _ = pde
    other = runs.create(conn, "q", "en", run_id="run-empty")
    conn.execute(
        "INSERT INTO question_plans (run_id, version, source, plan, created_at) SELECT ?, 1, source, plan, created_at "
        "FROM question_plans WHERE run_id = ?", (other, "run-screen-neural-pde-solvers"),
    )
    with pytest.raises(HttpError) as raised:
        screen.run(conn, model(Judge()), other)
    assert raised.value.status == 409


# --- overrides -------------------------------------------------------------------------


def test_an_override_wins_and_its_revocation_brings_the_verdict_back(pde) -> None:
    conn, run_id, members = pde
    screen.run(conn, model(Judge(keep="^$")), run_id)  # everything excluded
    key = members[0]["paper"]["key"]
    first = screen.override(conn, run_id, key, "include", "zhiheng", "Keep as a contrast case.")
    assert errors("screen_override", first) == [] and first["override_id"] == "o1"
    assert screen.decisions(conn, run_id)[key] | {"verdict": None} == {
        "decision": "include", "source": "override", "verdict": None, "override": first,
    }
    second = screen.override(conn, run_id, key, "exclude", "zhiheng")
    assert screen.decisions(conn, run_id)[key]["override"] == second
    undo = screen.revoke(conn, run_id, "o2", "zhiheng")
    assert undo["decision"] is None and undo["revokes"] == "o2" and errors("screen_override", undo) == []
    assert screen.decisions(conn, run_id)[key]["override"] == first
    screen.revoke(conn, run_id, "o1", "zhiheng")
    assert screen.decisions(conn, run_id)[key]["source"] == "model"
    assert screen.decisions(conn, run_id)[key]["decision"] == "exclude"
    with pytest.raises(HttpError) as twice:
        screen.revoke(conn, run_id, "o1", "zhiheng")
    assert twice.value.status == 409
    with pytest.raises(HttpError) as revoking_a_revoke:
        screen.revoke(conn, run_id, "o3", "zhiheng")
    assert revoking_a_revoke.value.status == 404
    events = [e for e in runs.events(conn, run_id) if e["type"] == "override"]
    assert [e["data"]["override"]["override_id"] for e in events] == ["o1", "o2", "o3", "o4"]
    assert all(errors("question_event", e) == [] for e in events)


def test_overrides_are_checked(pde) -> None:
    conn, run_id, members = pde
    key = members[0]["paper"]["key"]
    for args, status in [
        ((key, "maybe", "zhiheng"), 400),
        ((key, "include", "  "), 400),
        (("doi:10.0/not-here", "include", "zhiheng"), 404),
    ]:
        with pytest.raises(HttpError) as raised:
            screen.override(conn, run_id, *args)
        assert raised.value.status == status
    # A candidate can be decided before any screening; the screening then keeps it.
    screen.override(conn, run_id, key, "include", "zhiheng")
    result = screen.run(conn, model(Judge(keep="^$")), run_id)
    assert key in {m["paper"]["key"] for m in sets.Store(conn, run_id).get(result.screened).members}
    assert result.excluded == len(members)


def test_the_screened_set_replays_with_the_decisions_it_was_made_with(pde) -> None:
    conn, run_id, _ = pde
    result = screen.run(conn, model(Judge()), run_id)
    store = sets.Store(conn, run_id)
    before = [m["paper"]["key"] for m in store.get(result.screened).members]
    excluded = next(k for k, d in screen.decisions(conn, run_id).items() if d["decision"] == "exclude")
    screen.override(conn, run_id, excluded, "include", "zhiheng")
    replayed = screen.replay(conn, run_id)
    assert [m["paper"]["key"] for m in replayed[result.screened]] == before
    with pytest.raises(ValueError):
        store.replay()


# --- Strata, recorded ------------------------------------------------------------------


@pytest.mark.parametrize("sample_id", sorted(RECORDED))
def test_strata_s_recorded_screening_keeps_the_must_find_papers(sample_id: str) -> None:
    s = replay.sample(sample_id)
    conn, run_id, candidates = replay.screening_run(s)
    fake = s.model("model.screen.json")
    result = screen.run(conn, model(fake.transport()), run_id, candidates)
    found = screen.decisions(conn, run_id)
    papers = {k: m["paper"] for m in sets.Store(conn, run_id).get(candidates).members for k in m["paper"]["keys"]}
    there = list(dict.fromkeys(
        next(papers[k]["key"] for k in must["keys"] if k in papers)
        for must in s.question["must_find"] if any(k in papers for k in must["keys"])
    ))
    kept = [k for k in there if found[k]["decision"] != "exclude"]
    expected = RECORDED[sample_id]
    assert (len(found), result.included, len(there), len(kept)) == (
        expected["candidates"], expected["included"], expected["must_find"], expected["kept"],
    )
    assert result.unscreened == 0
