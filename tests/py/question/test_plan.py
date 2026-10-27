"""The question → search plan step (`isbe.question.plan`) behind a scripted
OpenAI-compatible model: parsing, checking, the one repair call, the fallback,
versioned edits and the `question_plan@1` contract."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from isbe.core import db, http
from isbe.core.errors import HttpError
from isbe.core.llm import Llm
from isbe.question import plan

CONTRACTS = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "question"
SCHEMAS = [json.loads(p.read_text()) for p in CONTRACTS.glob("*.schema.json")]
REGISTRY = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in SCHEMAS)
PLAN_SCHEMA = next(s for s in SCHEMAS if s["$id"].endswith(":question_plan"))

NOWCASTING = "基于深度学习的雷达回波外推降水临近预报（0–2 小时）有哪些主要方法？"
GREEN = "How does urban green space affect mental health outcomes?"

# The model's part of a plan, as the prompt asks for it.
REPLY = {
    "reformulation": "Deep learning methods for radar-echo extrapolation in precipitation nowcasting.",
    "subject_terms": ["precipitation nowcasting", "radar echo extrapolation"],
    "method_terms": ["deep learning", "recurrent neural network"],
    "outcome_terms": ["forecast skill"],
    "keyword_layers": {
        "canonical": ["precipitation nowcasting", "radar echo extrapolation"],
        "mainstream": ["ConvLSTM precipitation nowcasting"],
        "emerging": ["generative precipitation nowcasting"],
        "method_centric": ["spatiotemporal predictive learning"],
        "cross_domain": ["video prediction"],
    },
    "queries": [
        {"id": "q1", "query": "\"precipitation nowcasting\" deep learning radar", "layer": "canonical", "priority": 1},
        {"id": "q2", "query": "radar echo extrapolation neural network", "layer": "canonical", "priority": 2},
        {"id": "q3", "query": "generative precipitation nowcasting", "layer": "emerging", "priority": 3},
    ],
    "year_from": 2012,
    "year_to": None,
    "criteria": {
        "include": ["Proposes or evaluates a data-driven method for radar precipitation nowcasting"],
        "exclude": ["Seasonal or climate-scale forecasting"],
    },
    "rationale": "Spans extrapolation baselines, recurrent and generative models.",
}


class Model:
    """Chat completions answered from a script, one entry per call: reply text and
    finish reason, or an HTTP status. Each request is kept."""

    def __init__(self, *script: tuple[str, str] | int) -> None:
        self.script = list(script)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        step = self.script.pop(0)
        if isinstance(step, int):
            return httpx.Response(step, json={"error": {"message": "nope"}})
        text, finish = step
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": len(text) // 4},
        })

    def llm(self) -> Llm:
        env = {
            "ISBE_LLM_PROVIDER": "openai",
            "ISBE_LLM_BASE_URL": "http://127.0.0.1:9/v1",
            "ISBE_LLM_NO_AUTH": "true",
            "ISBE_LLM_MODEL_SMART": "strata-qwen",
        }
        model = Llm.from_vars(env.get, client=http.Http(5, transport=httpx.MockTransport(self)))
        model._sleep = lambda _: None  # type: ignore[assignment]
        return model

    def user(self, call: int) -> str:
        return next(m["content"] for m in self.requests[call]["messages"] if m["role"] == "user")


def said(text: str | dict, finish: str = "stop") -> tuple[str, str]:
    return (text if isinstance(text, str) else json.dumps(text, ensure_ascii=False), finish)


@pytest.fixture
def conn():
    return db.connect(":memory:")


def contract_errors(doc: dict) -> list[str]:
    checker = Draft202012Validator(PLAN_SCHEMA, registry=REGISTRY, format_checker=FormatChecker())
    return [e.message for e in checker.iter_errors(doc)]


def test_the_model_plan_is_saved_as_version_one_and_meets_the_contract(conn) -> None:
    model = Model(said(REPLY))
    doc = plan.generate(conn, model.llm(), "run-1", NOWCASTING, "zh")
    assert contract_errors(doc) == []
    assert (doc["version"], doc["parent_version"], doc["source"]) == (1, None, "model")
    assert doc["question"] == NOWCASTING and doc["lang"] == "zh"
    assert [q["query"] for q in doc["queries"]] == [q["query"] for q in REPLY["queries"]]
    assert doc["criteria"] == REPLY["criteria"]
    assert doc["llm"]["model"] == "strata-qwen" and doc["llm"]["calls"] == 1
    assert doc["llm"]["input_tokens"] == 1000
    # The Chinese prompt, the question verbatim, and one logged call under a ledger job.
    request = model.requests[0]
    assert request["messages"][0]["content"].startswith("你是一名科研文献检索馆员")
    assert NOWCASTING in model.user(0)
    assert request["max_completion_tokens"] == plan.PLAN_TOKENS
    job = conn.execute("SELECT r.job, r.status, l.purpose FROM runs r JOIN llm_calls l ON l.run_id = r.id").fetchall()
    assert [tuple(r) for r in job] == [("question_plan", "ok", "question_plan")]
    assert plan.get(conn, "run-1") == doc


def test_english_questions_use_the_english_prompt_and_user_years_win(conn) -> None:
    model = Model(said(REPLY))
    doc = plan.generate(conn, model.llm(), "run-1", GREEN, "en", year_from=2015)
    assert model.requests[0]["messages"][0]["content"].startswith("You are a research librarian")
    assert "2015 to any" in model.user(0)
    assert (doc["year_from"], doc["year_to"]) == (2015, None)


@pytest.mark.parametrize("wrapped", [
    "```json\n{plan}\n```",
    "<think>which layers?</think>\n{plan}",
    "Here is the plan:\n{plan}\nGood luck.",
])
def test_replies_wrapped_in_fences_thinking_or_prose_are_read(conn, wrapped: str) -> None:
    text = wrapped.replace("{plan}", json.dumps(REPLY))
    doc = plan.generate(conn, Model(said(text)).llm(), "run-1", NOWCASTING, "zh")
    assert doc["source"] == "model" and len(doc["queries"]) == 3


def test_a_reply_cut_off_at_its_token_limit_is_asked_again(conn) -> None:
    whole = json.dumps(REPLY)
    cut = whole[: whole.index('"rationale"') + 20]  # inside the rationale string
    model = Model(said(cut, "length"), said(REPLY))
    doc = plan.generate(conn, model.llm(), "run-1", NOWCASTING, "zh")
    assert "cut off at the output limit" in model.user(1)
    assert doc["source"] == "model" and doc["rationale"] == REPLY["rationale"]


def test_a_cut_off_reply_is_closed_and_used_when_the_repair_fails(conn) -> None:
    whole = json.dumps(REPLY)
    cut = whole[: whole.index('"rationale"') + 20]
    model = Model(said(cut, "length"), 500, 500, 500)
    doc = plan.generate(conn, model.llm(), "run-1", NOWCASTING, "zh")
    assert doc["source"] == "model"
    assert doc["criteria"] == REPLY["criteria"] and doc["rationale"] == ""
    assert contract_errors(doc) == []


def test_a_plan_without_include_criteria_is_repaired(conn) -> None:
    bare = dict(REPLY, criteria={"include": [], "exclude": []})
    model = Model(said(bare), said(REPLY))
    doc = plan.generate(conn, model.llm(), "run-1", NOWCASTING, "zh")
    assert "criteria.include is empty" in model.user(1)
    assert doc["criteria"] == REPLY["criteria"]


def test_close_truncated_drops_the_unfinished_value() -> None:
    assert plan.close_truncated('{"a": [1, 2], "b": {"c": "x", "d": "unfini') == {"a": [1, 2], "b": {"c": "x"}}
    assert plan.close_truncated('{"queries": [{"id": "q1", "query": "a b"}, {"id": "q') == {
        "queries": [{"id": "q1", "query": "a b"}]
    }
    assert plan.close_truncated("no json here") is None


def test_a_broken_reply_gets_one_repair_call_listing_the_problems(conn) -> None:
    broken = dict(REPLY, queries=[{"query": "降水 临近预报", "layer": "canonical", "priority": 1}])
    model = Model(said(broken), said(REPLY))
    doc = plan.generate(conn, model.llm(), "run-1", NOWCASTING, "zh")
    assert doc["source"] == "model" and doc["llm"]["calls"] == 2
    repair = model.user(1)
    assert NOWCASTING in repair and "is not in English" in repair and "降水 临近预报" in repair
    assert conn.execute("SELECT stats FROM runs").fetchone()[0] == '{"repaired":true}'


def test_queries_off_the_plans_terms_are_repaired_then_dropped(conn) -> None:
    stray = {"id": "q4", "query": "protein folding structure", "layer": "cross_domain", "priority": 4}
    reply = dict(REPLY, queries=[*REPLY["queries"], stray])
    model = Model(said(reply), said(reply))
    doc = plan.generate(conn, model.llm(), "run-1", NOWCASTING, "zh")
    assert "query q4 shares no word with the plan's terms and keywords" in model.user(1)
    assert [q["id"] for q in doc["queries"]] == ["q1", "q2", "q3"]


@pytest.mark.parametrize(("query", "tidied"), [
    # Two exact phrases side by side: the first short one stays, the long one goes.
    ('"radar echo extrapolation" "precipitation nowcasting"', '"radar echo extrapolation" precipitation nowcasting'),
    ('"machine learning" "numerical weather prediction"', '"machine learning" numerical weather prediction'),
    ('"physics informed neural networks" PDE', "physics informed neural networks PDE"),
    ('"single" word', "single word"),
    # OR groups: alternatives of several words are quoted, `or` is upper-cased.
    ("(machine learning or deep learning) weather", '("machine learning" OR "deep learning") weather'),
    ('(PDE OR "partial differential equations") "neural operator"',
     '(PDE OR "partial differential equations") "neural operator"'),
    ("a ( b OR c ) d", "a (b OR c) d"),
    ("(radar echo) extrapolation", "(radar echo) extrapolation"),
    ("unbalanced (radar OR lidar", "unbalanced radar OR lidar"),
])
def test_model_queries_keep_quotes_for_short_phrases_only(query: str, tidied: str) -> None:
    assert plan.tidy_query(query) == tidied


def test_bare_abbreviations_get_the_full_form_the_plan_names() -> None:
    forms = plan.full_forms(["physics-informed neural networks", "partial differential equations",
                             "long short-term memory"])
    assert forms["PINN"] == "physics-informed neural networks" and forms["LSTM"] == "long short-term memory"
    expand = plan.expand_abbreviations
    assert expand("PINNs PDE", forms) == (
        '(PINNs OR "physics-informed neural networks") (PDE OR "partial differential equations")'
    )
    # Already spelled out, inside an OR group or quoted, or unknown: unchanged.
    for query in ["PDE partial differential equations", '(PINN OR "physics informed neural networks")',
                  '"PDE" solver', "FNO solver"]:
        assert expand(query, forms) == query


def test_model_queries_are_tidied_and_human_edits_are_not(conn) -> None:
    reply = json.loads(json.dumps(REPLY))
    reply["queries"][0]["query"] = '"radar echo extrapolation" "precipitation nowcasting"'
    reply["queries"][1]["query"] = "RNN radar (extrapolation or prediction)"
    first = plan.generate(conn, Model(said(reply)).llm(), "run-1", NOWCASTING, "zh")
    assert [q["query"] for q in first["queries"][:2]] == [
        '"radar echo extrapolation" precipitation nowcasting',
        '(RNN OR "recurrent neural network") radar (extrapolation OR prediction)',
    ]
    edited = [{**first["queries"][0], "query": '"radar echo" "precipitation nowcasting"'}]
    assert plan.edit(conn, "run-1", 1, {"queries": edited})["queries"][0]["query"] == (
        '"radar echo" "precipitation nowcasting"'
    )


CONCEPT_REPLY = {
    "reformulation": REPLY["reformulation"],
    "concepts": [
        {"role": "subject", "terms": ["precipitation nowcasting", "rainfall nowcasting"]},
        {"role": "method", "terms": ["deep learning", "\"neural network\""]},
        {"role": "landmark", "terms": ["ConvLSTM", "PredRNN"]},
    ],
    "year_from": None,
    "year_to": None,
    "criteria": REPLY["criteria"],
    "rationale": REPLY["rationale"],
}


def test_queries_are_composed_from_the_model_concepts(conn) -> None:
    doc = plan.generate(conn, Model(said(CONCEPT_REPLY)).llm(), "run-1", NOWCASTING, "zh")
    assert contract_errors(doc) == []
    assert doc["concepts"][1] == {"role": "method", "terms": ["deep learning", "neural network"]}
    subject = "((precipitation nowcasting) OR (rainfall nowcasting))"
    assert [q["query"] for q in doc["queries"]] == [
        subject,
        f"{subject} ((deep learning) OR (neural network))",
        f"(ConvLSTM OR PredRNN) {subject}",
        "(ConvLSTM OR PredRNN)",
    ]
    assert doc["queries"][-1]["limit"] == 200 and doc["queries"][-1]["priority"] == 2
    # The term fields and layers come from the concepts when the model gives none.
    assert doc["subject_terms"] == ["precipitation nowcasting"]
    assert doc["method_terms"] == ["deep learning"]
    assert doc["keyword_layers"]["method_centric"] == ["ConvLSTM", "PredRNN"]


def test_edited_concepts_recompose_the_queries_and_edited_queries_stay(conn) -> None:
    plan.generate(conn, Model(said(CONCEPT_REPLY)).llm(), "run-1", NOWCASTING, "zh")
    concepts = [{"role": "subject", "terms": ["radar nowcasting"]}, {"role": "method", "terms": ["transformer"]}]
    second = plan.edit(conn, "run-1", 1, {"concepts": concepts})
    assert [q["query"] for q in second["queries"]] == ["radar nowcasting", "radar nowcasting transformer"]
    own = [{"query": '"radar echo" extrapolation', "layer": "canonical", "priority": 1}]
    third = plan.edit(conn, "run-1", 2, {"queries": own, "year_to": 2024})
    assert [q["query"] for q in third["queries"]] == ['"radar echo" extrapolation']
    assert third["concepts"] == concepts and contract_errors(third) == []
    with pytest.raises(HttpError, match="no subject concept"):
        plan.edit(conn, "run-1", 1, {"concepts": [{"role": "method", "terms": ["transformer"]}]})


def test_a_concept_without_a_role_is_repaired(conn) -> None:
    broken = {**CONCEPT_REPLY, "concepts": [{"terms": ["nowcasting"]}]}
    model = Model(said(broken), said(CONCEPT_REPLY))
    doc = plan.generate(conn, model.llm(), "run-1", NOWCASTING, "zh")
    assert "concept 1 needs a role" in model.user(1) and len(doc["queries"]) == 4


@pytest.mark.parametrize("second", [said("still not json"), 500, said("", "content_filter")])
def test_a_plan_still_broken_after_the_repair_falls_back_to_the_question(conn, second) -> None:
    model = Model(said("I cannot write JSON today."), second, second, second)
    doc = plan.generate(conn, model.llm(), "run-1", GREEN, "en")
    assert doc["source"] == "fallback"
    assert doc["queries"] == [
        {"id": "q1", "query": "How does urban green space affect mental health outcomes",
         "layer": "original", "priority": 1}
    ]
    assert doc["rationale"].startswith("No model plan (")
    assert doc["llm"]["calls"] >= 1
    assert contract_errors(doc) == []
    assert "fallback" in json.loads(conn.execute("SELECT stats FROM runs").fetchone()[0])


def test_a_refusal_falls_back_without_a_repair(conn) -> None:
    model = Model(said("", "content_filter"))
    doc = plan.generate(conn, model.llm(), "run-1", GREEN, "en")
    assert doc["source"] == "fallback" and len(model.requests) == 1
    assert "refused" in doc["rationale"]


def test_no_model_falls_back_to_the_question_without_its_punctuation(conn) -> None:
    doc = plan.generate(conn, None, "run-1", NOWCASTING, "zh", unavailable="OPENAI_API_KEY is not set")
    assert doc["source"] == "fallback" and "llm" not in doc
    assert doc["queries"][0]["query"] == "基于深度学习的雷达回波外推降水临近预报（0–2 小时）有哪些主要方法"
    assert doc["rationale"] == "No model plan (OPENAI_API_KEY is not set); the question itself is the only query."
    assert doc["reformulation"] == NOWCASTING
    assert contract_errors(doc) == []
    assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 0


def test_load_model_reports_a_missing_configuration() -> None:
    llm, why = plan.load_model()
    assert llm is None and "ANTHROPIC_API_KEY is not set" in why


def test_edits_are_new_versions_checked_strictly(conn) -> None:
    first = plan.generate(conn, Model(said(REPLY)).llm(), "run-1", NOWCASTING, "zh")
    queries = [*first["queries"][:2], {"query": "radar nowcasting benchmark", "layer": "mainstream", "priority": 3}]
    second = plan.edit(conn, "run-1", 1, {"queries": queries, "year_to": 2024})
    assert (second["version"], second["parent_version"], second["source"]) == (2, 1, "human")
    assert [q["id"] for q in second["queries"]] == ["q1", "q2", "q3"]
    assert second["year_to"] == 2024 and second["criteria"] == first["criteria"]
    assert "llm" not in second and contract_errors(second) == []
    # Editing the model's version again branches from it; nothing earlier changes.
    third = plan.edit(conn, "run-1", 1, {"criteria": {"include": ["Radar only"], "exclude": []}})
    assert (third["version"], third["parent_version"]) == (3, 1)
    assert plan.get(conn, "run-1", 1) == first
    assert plan.get(conn, "run-1") == third
    assert [v["source"] for v in plan.versions(conn, "run-1")] == ["model", "human", "human"]


@pytest.mark.parametrize(("changes", "message"), [
    ({"question": "something else"}, "cannot be edited: question"),
    ({"queries": []}, "queries must be a non-empty list"),
    ({"queries": [{"query": "a b", "layer": "sideways", "priority": 1}]}, "no valid layer"),
    ({"queries": [{"query": "x", "layer": "canonical", "priority": 1, "boost": 2}]}, "fields other than"),
    ({"queries": [{"query": f"q{i}", "layer": "canonical", "priority": i} for i in range(1, 18)]}, "at most 16"),
    ({"year_from": 2030, "year_to": 2020}, "year_from is after year_to"),
    ({"subject_terms": "radar"}, "subject_terms must be a list of strings"),
    ({"criteria": {"include": [], "keep": []}}, "criteria must be an object"),
])
def test_bad_edits_are_refused_and_save_nothing(conn, changes, message) -> None:
    plan.generate(conn, Model(said(REPLY)).llm(), "run-1", NOWCASTING, "zh")
    with pytest.raises(HttpError, match=message) as refused:
        plan.edit(conn, "run-1", 1, changes)
    assert refused.value.status == 400
    assert len(plan.versions(conn, "run-1")) == 1


def test_the_fallback_query_stays_editable(conn) -> None:
    plan.generate(conn, None, "run-1", NOWCASTING, "zh")
    edited = plan.edit(conn, "run-1", 1, {"rationale": "Kept as asked."})
    assert edited["queries"][0]["layer"] == "original"


def test_unknown_versions_runs_and_bad_input_are_refused(conn) -> None:
    with pytest.raises(HttpError) as missing:
        plan.get(conn, "run-1")
    assert missing.value.status == 404
    with pytest.raises(HttpError, match="No such plan version"):
        plan.edit(conn, "run-1", 1, {})
    for bad in ({"run_id": "../x"}, {"question": "  "}, {"lang": "fr"}, {"year_from": 3000}):
        args = {"run_id": "run-1", "question": GREEN, "lang": "en"} | bad
        year = args.pop("year_from", None)
        with pytest.raises(HttpError):
            plan.generate(conn, None, args["run_id"], args["question"], args["lang"], year_from=year)


RECORDED = sorted((CONTRACTS.parents[2] / "tests" / "fixtures" / "question").glob("*/model.strata.json"))
SAMPLES = {q["id"]: q for q in json.loads((CONTRACTS / "samples.json").read_text())["questions"]}


class Replayed(Model):
    """Answers from recorded `model_replies@1` replies, by the hash of the system and
    user text; a prompt with no recording fails the call."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.replies = {r["prompt_sha"]: r["reply"] for r in json.loads(path.read_text())["replies"]}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system, user = (next(m["content"] for m in body["messages"] if m["role"] == r) for r in ("system", "user"))
        sha = hashlib.sha256(json.dumps([system, user], ensure_ascii=False).encode()).hexdigest()
        reply = self.replies.get(sha)
        if reply is None:
            return httpx.Response(500, json={"error": {"message": f"no recording for {sha}"}})
        self.script = [said(reply)]
        return super().__call__(request)


@pytest.mark.parametrize("path", RECORDED, ids=[p.parent.name for p in RECORDED])
def test_recorded_strata_plans_replay_without_a_repair(conn, path: Path) -> None:
    sample = SAMPLES[path.parent.name]
    model = Replayed(path)
    doc = plan.generate(conn, model.llm(), "run-1", sample["question"], sample["lang"],
                        year_from=sample["year_from"], year_to=sample["year_to"])
    assert len(model.requests) == 1, "the recorded plan needed a repair"
    assert doc["source"] == "model" and contract_errors(doc) == []
    assert 4 <= len(doc["queries"]) <= plan.MAX_QUERIES and doc["criteria"]["include"]


def test_question_runs_get_a_model_that_fails_fast(monkeypatch) -> None:
    for name, value in {"ISBE_LLM_PROVIDER": "openai", "OPENAI_API_KEY": "k"}.items():
        monkeypatch.setenv(name, value)
    llm, why = plan.load_model()
    assert why == "" and llm is not None
    assert llm.http.timeout == plan.QUESTION_TIMEOUT and llm.retry_connect is False and llm.waits == (5,)
    monkeypatch.setenv("ISBE_LLM_TIMEOUT_S", "45")
    llm, _ = plan.load_model()
    assert llm is not None and llm.http.timeout == 45
    monkeypatch.delenv("OPENAI_API_KEY")
    assert plan.load_model() == (None, "OPENAI_API_KEY is not set (ISBE_LLM_PROVIDER=openai); local OpenAI-compatible "
                                        "services may explicitly enable ISBE_LLM_NO_AUTH")
