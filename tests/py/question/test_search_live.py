"""The search stage against a real essaies for every sample question, with the
plan Strata made for it (model.strata.json) and the hand-written plan.json.
Skipped unless essaies is given:

    ISBE_LIVE_ESSAIES_URL=http://<essaies> uv run pytest -m live tests/py/question/test_search_live.py -s

Prints, per sample and plan, the papers before and after deduplication, the
queries run, failed and skipped, and the must-find papers among the candidates."""

import copy
import os

import pytest
import replay
from question_schemas import samples

from isbe.core import db, http, jsonfmt
from isbe.question import runs, search, sets, sources

URL = os.environ.get("ISBE_LIVE_ESSAIES_URL")
IDS = [q["id"] for q in samples()]

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not URL, reason="set ISBE_LIVE_ESSAIES_URL to search a real essaies"),
]


def plans(s: replay.Sample) -> dict[str, dict]:
    out = {"plan.json": copy.deepcopy(s.plan)}
    strata = s.strata_plan()
    if strata is not None:
        out["strata"] = strata
    return out


@pytest.mark.parametrize("sample_id", IDS)
def test_search_finds_the_must_find_papers(sample_id):
    s = replay.sample(sample_id)
    source = sources.Essaies(http.Http(sources.TIMEOUT), URL)
    for name, plan in plans(s).items():
        conn = db.connect(":memory:")
        run_id = runs.create(conn, plan["question"], plan["lang"])
        plan = {**plan, "run_id": run_id, "version": 1, "parent_version": None}
        conn.execute(
            "INSERT INTO question_plans (run_id, version, source, plan, created_at) VALUES (?, 1, ?, ?, ?)",
            (run_id, plan["source"], jsonfmt.dumps(plan, sort_keys=False), plan["created_at"]),
        )
        result = search.run(conn, source, run_id)
        store = sets.Store(conn, run_id)
        union = store.get(f"s{result.search}.union").members
        candidates = store.get(result.candidates).members
        held = {k for m in candidates for k in m["paper"]["keys"]}
        found = [p for p in s.question["must_find"] if held & set(p["keys"])]
        print(
            f"\n{sample_id} [{name}] queries run {result.queries_run} failed {result.queries_failed} "
            f"skipped {result.queries_skipped}; hits {result.hits}, before dedup {len(union)}, "
            f"after {len(candidates)}; must-find {len(found)}/{len(s.question['must_find'])}; "
            f"{result.ms / 1000:.1f} s"
        )
        for paper in s.question["must_find"]:
            if paper not in found:
                print(f"  missed: {paper['title']}")
        assert candidates
        assert store.replay()[result.candidates] == candidates
