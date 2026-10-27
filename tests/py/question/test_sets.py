"""Paper sets and their operations (`isbe.question.sets`): union, intersection,
difference, identity dedup, the recorded `set_op@1` and its replay."""

import copy

import pytest
from question_schemas import errors

from isbe.core import db
from isbe.question import runs, sets


def member(*keys, title="A paper", query="q1", rank=1, **fields):
    paper = {"key": keys[0], "keys": list(keys), "title": title, **fields}
    return {"paper": paper, "provenance": [{"via": "search", "query_id": query, "rank": rank}]}


def keys(members):
    return [m["paper"]["key"] for m in members]


PANGU_PREPRINT = ("openalex:W4308505112", "doi:10.48550/arxiv.2211.02556", "arxiv:2211.02556")
PANGU_NATURE = ("openalex:W4383218913", "doi:10.1038/s41586-023-06185-3")


def test_union_joins_by_preferred_key_and_keeps_every_provenance():
    a = [member("openalex:W1", "doi:10.1000/a", query="q1", rank=3), member("arxiv:2001.00001", query="q1", rank=4)]
    b = [member("openalex:W1", "doi:10.1000/a", query="q2", rank=1), member("openalex:W2", query="q2", rank=2)]
    out = sets.union([a, b])
    assert keys(out) == ["openalex:W1", "arxiv:2001.00001", "openalex:W2"]
    assert out[0]["provenance"] == [
        {"via": "search", "query_id": "q1", "rank": 3}, {"via": "search", "query_id": "q2", "rank": 1},
    ]
    # Inputs are never changed.
    assert a[0]["provenance"] == [{"via": "search", "query_id": "q1", "rank": 3}]


def test_intersection_and_difference_match_on_any_key():
    a = [member("openalex:W1", "arxiv:2001.00001"), member("openalex:W2"), member("doi:10.1000/c")]
    b = [member("arxiv:2001.00001", query="q2"), member("doi:10.1000/c", query="q2")]
    c = [member("doi:10.1000/c", query="q3")]
    assert keys(sets.intersection([a, b])) == ["openalex:W1", "doi:10.1000/c"]
    assert keys(sets.intersection([a, b, c])) == ["doi:10.1000/c"]
    both = sets.intersection([a, b])[0]
    assert [p["query_id"] for p in both["provenance"]] == ["q1", "q2"]
    assert keys(sets.difference([a, b])) == ["openalex:W2"]
    assert keys(sets.difference([a, b, [member("openalex:W2")]])) == []


def test_dedup_folds_records_that_share_a_key_first_seen_wins():
    arxiv_only = member("arxiv:2202.11214", title="FourCastNet (archive)", query="q1", rank=2, year=2022)
    openalex = member("openalex:W4225906496", "doi:10.48550/arxiv.2202.11214", "arxiv:2202.11214",
                      title="FourCastNet", query="q2", rank=1, year=None, venue="arXiv")
    other = member("openalex:W9")
    out, folds = sets.dedup([arxiv_only, other, openalex])
    assert len(out) == 2
    kept = out[0]["paper"]
    assert kept["key"] == "openalex:W4225906496"
    assert kept["keys"] == ["openalex:W4225906496", "doi:10.48550/arxiv.2202.11214", "arxiv:2202.11214"]
    assert kept["title"] == "FourCastNet (archive)" and kept["year"] == 2022 and kept["venue"] == "arXiv"
    assert [p["query_id"] for p in out[0]["provenance"]] == ["q1", "q2"]
    # The dedup event names the stars as they were first seen.
    assert folds == [("openalex:W4225906496", ["arxiv:2202.11214"])]


def test_a_preprint_and_its_journal_version_stay_two_papers():
    # A journal work that also claims the preprint's arXiv id: the first holder keeps it.
    journal = member(*PANGU_NATURE, "arxiv:2211.02556", title="Accurate medium-range ...")
    preprint = member(*PANGU_PREPRINT, title="Pangu-Weather")
    out, folds = sets.dedup([journal, preprint, member("arxiv:2211.02556", query="q4")])
    assert keys(out) == ["openalex:W4383218913", "openalex:W4308505112"]
    assert out[1]["paper"]["keys"] == ["openalex:W4308505112", "doi:10.48550/arxiv.2211.02556"]
    # No two members share a key, as paper_set@1 requires.
    held = [k for m in out for k in m["paper"]["keys"]]
    assert len(held) == len(set(held))
    # The bare arXiv record joined the paper that holds the arXiv id.
    assert [p["query_id"] for p in out[0]["provenance"]] == ["q1", "q4"]
    assert folds == [("openalex:W4383218913", ["arxiv:2211.02556"])]


def test_dedup_of_exact_repeats_has_no_fold_to_report():
    out, folds = sets.dedup([member("openalex:W1", rank=1), member("openalex:W1", rank=5)])
    assert len(out) == 1 and folds == []
    assert len(out[0]["provenance"]) == 2


def test_apply_checks_its_inputs():
    with pytest.raises(ValueError, match="unknown"):
        sets.apply("limit", [[]])
    with pytest.raises(ValueError, match="two or more"):
        sets.apply("difference", [[]])
    with pytest.raises(ValueError, match="one input"):
        sets.apply("dedup", [[], []])
    with pytest.raises(ValueError, match="one input"):
        sets.apply("screen", [[], []])


@pytest.fixture
def store():
    conn = db.connect(":memory:")
    runs.create(conn, "Q?", "en", run_id="run-1")
    return sets.Store(conn, "run-1")


def test_recorded_operations_replay_to_the_same_sets(store):
    q1 = [member("openalex:W1", "doi:10.1000/a"), member("arxiv:2001.00001", rank=2)]
    q2 = [member("doi:10.1000/a", "arxiv:2001.00001", query="q2"), member("openalex:W3", query="q2", rank=2)]
    store.add(sets.PaperSet("s1.q1", "query", "q1", q1))
    store.add(sets.PaperSet("s1.q2", "query", "q2", q2))
    union, union_op, _ = store.run("union", ["s1.q1", "s1.q2"], "s1.union", "candidates", "All")
    candidates, dedup_op, folds = store.run("dedup", ["s1.union"], "s1.candidates", "candidates", "Deduplicated",
                                            {"order": ["openalex", "doi", "arxiv"]})
    only_q1, diff_op, _ = store.run("difference", ["s1.q1", "s1.q2"], "s1.only-q1", "manual", "Only q1")
    assert [len(union.members), len(candidates.members), len(only_q1.members)] == [4, 2, 0]
    assert dedup_op["stats"] == {"input_sizes": [4], "output_size": 2, "merged": 2}
    assert folds == [("openalex:W1", ["arxiv:2001.00001", "doi:10.1000/a"])]
    for op in (union_op, dedup_op, diff_op):
        assert errors("set_op", op) == []
    assert [o["op_id"] for o in store.ops()] == ["op-1", "op-2", "op-3"]
    for paper_set in (union, candidates, only_q1):
        assert errors("paper_set", paper_set.doc("run-1")) == []
        assert store.get(paper_set.set_id).members == paper_set.members

    replayed = store.replay()
    assert replayed["s1.candidates"] == candidates.members
    assert replayed["s1.union"] == union.members
    assert replayed["s1.only-q1"] == only_q1.members


def test_free_ids(store):
    store.add(sets.PaperSet("s1.q1", "query", "q1", []))
    store.add(sets.PaperSet("s1.q1-2", "query", "q1", []))
    assert store.free_id("s1.q1") == "s1.q1-3"
    assert store.free_id("s1.q2") == "s1.q2"


def test_a_query_set_is_valid_as_stored(store):
    paper_set = sets.PaperSet("s1.q1", "query", "q1: radar", [member("openalex:W1", "doi:10.1000/a")])
    store.add(copy.deepcopy(paper_set))
    doc = store.get("s1.q1").doc("run-1")
    assert errors("paper_set", doc) == []
    assert doc["made_by"] is None
