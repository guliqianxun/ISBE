"""`FakeModel` and `ModelRecorder` behind ISBE's real `Llm`: fixed replies by
prompt hash or text, and each injected failure surfacing the way the real
provider's would."""

import json

import httpx
import pytest
import replay

from isbe.core import db, http
from isbe.core.llm import Llm, LlmError, Prompt, Refused, Tier

SYSTEM = "You plan literature searches."


def prompt(user: str, max_tokens: int = 2000) -> Prompt:
    return Prompt(purpose="plan", tier=Tier.FAST, system=SYSTEM, user=user, max_tokens=max_tokens)


def model_for(transport: httpx.BaseTransport) -> tuple[Llm, list[float]]:
    env = {"ISBE_LLM_PROVIDER": "openai", "ISBE_LLM_BASE_URL": "http://127.0.0.1:9/v1", "ISBE_LLM_NO_AUTH": "true"}
    model = Llm.from_vars(env.get, client=http.Http(5, transport=transport))
    sleeps: list[float] = []
    model._sleep = sleeps.append  # type: ignore[assignment]
    return model, sleeps


def complete(model: Llm, p: Prompt):
    conn = db.connect(":memory:")
    out: dict[str, object] = {}

    def call(run_id: str, stats: dict[str, object]) -> None:
        out["reply"] = model.complete(conn, run_id, p)

    db.job(conn, "test", None, call)
    return out["reply"]


@pytest.mark.parametrize("sample_id", replay.sample_ids())
def test_sample_replies_give_the_plan_for_the_question(sample_id):
    s = replay.sample(sample_id)
    model, _ = model_for(s.model().transport())
    reply = complete(model, prompt(f"Question: {s.question['question']}"))
    planned = json.loads(reply.text)
    assert planned["queries"] == s.plan["queries"]
    assert planned == json.loads(replay.plan_reply(s.plan))


def test_replies_by_prompt_hash_win_over_none_and_unknown_prompts_fail():
    fake = replay.FakeModel().reply("by hash", sha=replay.prompt_sha(SYSTEM, "exact"))
    model, _ = model_for(fake.transport())
    assert complete(model, prompt("exact")).text == "by hash"
    with pytest.raises(LlmError) as raised:
        complete(model, prompt("something else"))
    assert isinstance(raised.value.__cause__, replay.Unrecorded)
    assert [c["user"] for c in fake.calls] == ["exact", "something else"]


def test_bad_json_is_returned_as_the_reply_text():
    fake = replay.FakeModel().reply({"queries": []}).fault("bad_json")
    model, _ = model_for(fake.transport())
    with pytest.raises(json.JSONDecodeError):
        json.loads(complete(model, prompt("q")).text)
    assert json.loads(complete(model, prompt("q")).text) == {"queries": []}  # one call only


def test_timeout_is_not_retried():
    fake = replay.FakeModel().reply("ok").fault("timeout")
    model, sleeps = model_for(fake.transport())
    with pytest.raises(LlmError) as raised:
        complete(model, prompt("q"))
    assert isinstance(raised.value.__cause__, httpx.TimeoutException)
    assert (len(fake.calls), sleeps) == (1, [])


def test_context_rejection_is_fitted_and_retried_with_the_same_reply():
    fake = replay.FakeModel(context=1000).reply("fitted", contains="long").fault("context")
    model, _ = model_for(fake.transport())
    reply = complete(model, prompt("long " * 400, max_tokens=5000))
    assert reply.text == "fitted"
    first, second = fake.calls
    assert first["sha"] == second["sha"]
    assert second["max_tokens"] < first["max_tokens"] == 5000


def test_cut_refusal_and_server_errors_surface_as_the_real_provider_would():
    model, _ = model_for(replay.FakeModel().reply("x").fault("cut").transport())
    with pytest.raises(LlmError, match="hit max_tokens"):
        complete(model, prompt("q"))
    model, _ = model_for(replay.FakeModel().reply("x").fault("refuse").transport())
    with pytest.raises(Refused):
        complete(model, prompt("q"))
    fake = replay.FakeModel().reply("after retries").fault("server_error", times=2)
    model, sleeps = model_for(fake.transport())
    assert complete(model, prompt("q")).text == "after retries"
    assert sleeps == [5, 30]
    fake = replay.FakeModel().reply("never").fault("server_error", times=None)
    model, _ = model_for(fake.transport())
    with pytest.raises(LlmError):
        complete(model, prompt("q"))


def test_unknown_fault_is_refused():
    with pytest.raises(ValueError, match="unknown fault"):
        replay.FakeModel().fault("slow")


def test_recorder_keeps_replies_for_the_fake_model_to_replay(tmp_path):
    upstream = replay.FakeModel().reply("real answer")
    recorder = replay.ModelRecorder(upstream.transport(), purpose=lambda system, user: "plan")
    model, _ = model_for(recorder.transport())
    assert complete(model, prompt("recorded question")).text == "real answer"
    path = tmp_path / "model.json"
    recorder.save(path)
    doc = json.loads(path.read_text())
    assert doc["replies"] == [
        {"purpose": "plan", "prompt_sha": replay.prompt_sha(SYSTEM, "recorded question"), "reply": "real answer"}
    ]
    model, _ = model_for(replay.FakeModel.load(path).transport())
    assert complete(model, prompt("recorded question")).text == "real answer"


def test_recorder_refuses_to_save_a_leaking_reply(tmp_path):
    upstream = replay.FakeModel().reply("served from /home/someone/models")
    recorder = replay.ModelRecorder(upstream.transport())
    model, _ = model_for(recorder.transport())
    complete(model, prompt("q"))
    with pytest.raises(ValueError, match="local path"):
        recorder.save(tmp_path / "model.json")
