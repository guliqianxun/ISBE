"""The question-research contracts in docs/contracts/question: every schema is
valid, every example matches its schema, the schemas reject what they should,
and the fixed sample questions meet the acceptance rules."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator
from question_schemas import ROOT, SCHEMAS, errors

# Example file -> schema name.
EXAMPLES = {
    "question_plan": "question_plan",
    "paper_set": "paper_set",
    "set_op": "set_op",
    "screen_verdict": "screen_verdict",
    "screen_override": "screen_override",
    "question_graph": "question_graph",
    "question_metrics": "question_metrics",
}


def example(name: str):
    return json.loads((ROOT / "examples" / f"{name}.json").read_text())


def events() -> list[dict]:
    return example("question_events")


def test_every_schema_is_valid_and_versioned():
    assert set(SCHEMAS) == {
        "common", "question_plan", "paper_set", "set_op", "screen_verdict",
        "screen_override", "question_event", "question_graph", "question_metrics", "question_samples",
    }
    for name, schema in SCHEMAS.items():
        Draft202012Validator.check_schema(schema)
        assert schema["$id"] == f"urn:isbe:question:{name}"
        if name != "common":
            assert schema["properties"]["schema"] == {"const": f"{name}@1"}


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_examples_match_their_schema(name):
    assert errors(EXAMPLES[name], example(name)) == []


def test_example_event_stream():
    stream = events()
    for event in stream:
        assert errors("question_event", event) == [], event["seq"]
    assert [e["seq"] for e in stream] == list(range(1, len(stream) + 1))
    assert stream[-1]["type"] == "done"
    assert {e["type"] for e in stream} == set(SCHEMAS["question_event"]["properties"]["type"]["enum"]) - {"expand"}


@pytest.mark.parametrize(
    ("key", "ok"),
    [
        ("openalex:W1485009520", True),
        ("doi:10.1038/s41586-021-03854-z", True),
        ("arxiv:1506.04214", True),
        ("arxiv:hep-th/9901001", True),
        ("arxiv:1506.04214v2", False),  # versions are stripped
        ("doi:10.1038/S41586-021-03854-Z", False),  # DOIs are lower-cased
        ("W1485009520", False),
        ("pmid:123", False),
    ],
)
def test_identity_keys(key, ok):
    verdict = {**example("screen_verdict"), "key": key}
    assert (errors("screen_verdict", verdict) == []) is ok


def test_rejects_unknown_fields_and_types():
    plan = {**example("question_plan"), "extra": 1}
    assert errors("question_plan", plan)
    event = {**events()[0], "type": "progress"}
    assert errors("question_event", event)


def test_event_data_must_match_its_type():
    hit = next(e for e in events() if e["type"] == "hit")
    assert errors("question_event", {**hit, "data": {"query_id": "q1", "paper": hit["data"]["paper"], "new": True}})
    error = next(e for e in events() if e["type"] == "error")
    assert errors("question_event", {**error, "data": {"stage": "search", "message": "x"}})  # fatal is required
    state = events()[0]
    assert errors("question_event", {**state, "data": {"state": "begin"}})


def test_unscreened_verdict_has_no_score():
    verdict = {**example("screen_verdict"), "decision": "unscreened", "score": None}
    assert errors("screen_verdict", verdict) == []
    assert errors("screen_verdict", {**verdict, "score": 0.5})


def test_reverting_an_override_names_it():
    revert = {**example("screen_override"), "override_id": "o2", "decision": None, "revokes": "o1"}
    assert errors("screen_override", revert) == []
    assert errors("screen_override", {**revert, "revokes": None})


def test_search_provenance_needs_query_and_rank():
    paper_set = example("paper_set")
    broken = copy.deepcopy(paper_set)
    broken["members"][0]["provenance"][0] = {"via": "search"}
    assert errors("paper_set", broken)
    broken["members"][0]["provenance"][0] = {"via": "cites"}
    assert errors("paper_set", broken)


def test_difference_needs_two_inputs():
    op = {**example("set_op"), "op": "difference", "inputs": ["a"]}
    assert errors("set_op", op)
    assert errors("set_op", {**op, "inputs": ["a", "b"]}) == []


def test_graph_edges_join_the_right_node_kinds():
    graph = example("question_graph")
    paper, keyword = graph["nodes"][0]["id"], "kw:precipitation nowcasting"
    for edge, ok in [
        ({"source": paper, "target": keyword, "kind": "cites", "weight": 1}, False),
        ({"source": keyword, "target": paper, "kind": "has_keyword", "weight": 1}, False),
        ({"source": keyword, "target": "kw:radar", "kind": "keyword_cooccur", "weight": 2}, True),
        ({"source": paper, "target": paper, "kind": "coupling", "weight": 0}, False),
    ]:
        assert (errors("question_graph", {**graph, "edges": [edge]}) == []) is ok, edge
    keyword_node = {**graph["nodes"][2], "decision": "include"}
    assert errors("question_graph", {**graph, "nodes": [keyword_node]})


def test_sample_questions():
    samples = json.loads((ROOT / "samples.json").read_text())
    assert errors("question_samples", samples) == []
    questions = samples["questions"]
    assert 4 <= len(questions) <= 5
    assert any(q["lang"] == "zh" for q in questions)
    assert any(q["topic"] == "nowcasting" for q in questions)
    assert {q["breadth"] for q in questions} == {"narrow", "broad"}
    assert len({q["id"] for q in questions}) == len(questions)
    for q in questions:
        keys = [k for m in q["must_find"] for k in m["keys"]]
        assert len(keys) == len(set(keys)), q["id"]  # no key names two papers
        for m in q["must_find"]:
            assert any(k.startswith("openalex:") for k in m["keys"]), m["title"]
