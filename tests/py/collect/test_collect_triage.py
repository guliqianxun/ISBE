import json
from collections.abc import Callable

import httpx
import pytest

from isbe.core import db, http, jsonfmt
from isbe.core.llm import Llm
from isbe.pipeline import triage
from isbe.pipeline.triage import gate, parse_verdicts, tier
from isbe.topics import Retrieval, Topic


def contract() -> Retrieval:
    return Retrieval(
        intent="precipitation nowcasting",
        out_of_scope_keywords=["COVID"],
        require_any=["precipitation", "radar echo"],
        secondary_terms=["downscaling"],
    )


def topic(**blocks: object) -> Topic:
    return Topic.model_validate({"id": "t", "label": "T", "cadence": "weekly", **blocks})


def unused_llm() -> Llm:
    return Llm.from_vars(lambda k: "unused" if k == "ANTHROPIC_API_KEY" else None)


def judge_llm(reply: Callable[[httpx.Request], httpx.Response]) -> tuple[Llm, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return reply(request)

    env = {"ISBE_LLM_PROVIDER": "openai", "ISBE_LLM_BASE_URL": "http://127.0.0.1:9/v1", "ISBE_LLM_NO_AUTH": "true"}
    llm = Llm.from_vars(env.get, client=http.Http(5, transport=httpx.MockTransport(record)))
    llm._sleep = lambda seconds: None  # type: ignore[method-assign]
    return llm, seen


def answer(text: str, finish: str = "stop") -> Callable[[httpx.Request], httpx.Response]:
    body = {"choices": [{"message": {"content": text}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    return lambda request: httpx.Response(200, json=body)


def items(conn, rows: list[tuple[str, str, str | None, str]]) -> None:  # type: ignore[no-untyped-def]
    now = db.now()
    for id, title, summary, published in rows:
        conn.execute(
            "INSERT INTO items (id, kind, source, title, summary, url, lang, published_at) "
            "VALUES (?, ?, 'arxiv', ?, ?, 'u', 'en', ?)",
            (id, "repo" if id.startswith("gh:") else "paper", title, summary, published or now),
        )
        conn.execute("INSERT INTO topic_items VALUES ('t', ?, ?)", (id, now))


def test_gates_look_at_the_title_only() -> None:
    r = contract()
    assert gate(r, "Precipitation nowcasting with diffusion") is None
    assert gate(r, "Precipitation and covid-19 spread") == "out_of_scope keyword in title: COVID"
    assert gate(r, "A video model") is not None
    assert tier(r, "Radar echo extrapolation", "We apply statistical downscaling.") == "secondary"


def test_parses_judge_lines_leniently() -> None:
    v = parse_verdicts(
        "[1] | IN | generative-diffusion | 扩散短临\n2|out||经济 nowcasting\\n4|IN|x|y\nnoise\n9|IN|x|out of batch\n3|MAYBE|x|y",
        4,
        [],
    )
    assert len(v) == 3
    assert v[3][0]
    w = parse_verdicts("1|OUT|经济预测\n2|IN|radar", 2, ["radar"])
    assert w[0] == (False, "", "经济预测")
    assert w[1] == (True, "radar", "in scope")
    assert v[0] == (True, "generative-diffusion", "扩散短临")
    assert v[1] == (False, "", "经济 nowcasting")
    # What the local Qwen wrote on the server, plus a table row and a numbered list.
    u = parse_verdicts(
        "[1] OUT | 水文径流模型非降水短临预报\n[3] IN | downscaling | 卫星降水降尺度方法\n"
        "| 4 | IN | downscaling | 表格行 |\n5. OUT 列表\n6 INSIDE | x",
        6,
        ["downscaling"],
    )
    assert u[0] == (False, "", "水文径流模型非降水短临预报")
    assert u[2] == (True, "downscaling", "卫星降水降尺度方法")
    assert u[3] == (True, "downscaling", "表格行")
    assert u[4] == (False, "", "列表")
    assert 5 not in u


def test_verdict_lines_follow_the_parsing_rules() -> None:
    assert parse_verdicts("0001|in|`why`\r\n", 1, []) == {0: (True, "", "why")}
    assert parse_verdicts("1|ınside", 1, []) == {}  # no Unicode case folding
    assert parse_verdicts("1 IN2", 1, []) == {}
    assert parse_verdicts("9" * 5000 + "|IN|x", 1, []) == {}
    assert parse_verdicts("1|IN|a|b|c\n1|OUT", 1, []) == {0: (False, "", "out of scope")}


def test_run_without_contract_keeps_everything_once() -> None:
    conn = db.connect(":memory:")
    items(conn, [("arxiv:1", "P", None, ""), ("gh:a/b", "a/b", None, "")])
    t = topic(arxiv={"categories": ["cs.CV"], "include_keywords": ["x"]})
    for expect in (1, 0):
        stats = db.job(conn, "triage", "t", lambda id, s: triage.run(conn, unused_llm(), id, t, s))
        assert stats["to_judge"] == expect
        assert stats["window_candidates"] == 1
        assert stats["cached"] == 1 - expect
        if expect == 0:
            assert stats["cached_kept"] == 1
    assert conn.execute("SELECT verdict FROM triage").fetchone()[0] == "kept"


def test_cached_drops_are_visible_and_changed_rules_are_rejudged() -> None:
    conn = db.connect(":memory:")
    items(conn, [("arxiv:1", "Wind forecast", None, ""), ("arxiv:old", "Old wind forecast", None, "2000-01-01T00:00:00Z")])
    conn.execute(
        "INSERT INTO items (id, kind, source, title, url, lang, published_at) "
        "VALUES ('arxiv:other', 'paper', 'arxiv', 'Other topic', 'u', 'en', ?)", (db.now(),)
    )
    conn.execute("INSERT INTO topic_items VALUES ('other', 'arxiv:other', ?)", (db.now(),))
    t = topic(arxiv={"categories": ["physics.ao-ph"], "include_keywords": ["wind"]},
              retrieval={"require_any": ["gust"]})
    for expected in (1, 0):
        stats = db.job(conn, "triage", "t", lambda id, s: triage.run(conn, unused_llm(), id, t, s))
        assert stats["window_candidates"] == 1
        assert stats["to_judge"] == expected
        assert stats["cached"] == 1 - expected
        if expected == 0:
            assert stats["cached_gate_dropped"] == 1
    # Removing the filter invalidates cached drops without deleting history.
    plain = t.model_copy(update={"retrieval": None})
    stats = db.job(conn, "triage", "t", lambda id, s: triage.run(conn, unused_llm(), id, plain, s))
    assert (stats["cached"], stats["to_judge"], stats["kept"]) == (0, 1, 1)


JUDGED = topic(
    arxiv={"categories": ["cs.CV"]},
    retrieval={"intent": "radar nowcasting", "in_scope": ["radar"], "out_of_scope": ["climate"],
               "facets": ["radar", "satellite"], "quality_bar": "peer-level",
               "secondary_terms": ["downscaling"]},
)


def three(conn) -> None:  # type: ignore[no-untyped-def]
    items(conn, [
        ("arxiv:1", "Radar nowcasting", "Line one\nline two " + "x" * 300, "2099-01-03T00:00:00Z"),
        ("arxiv:2", "Climate projection", None, "2099-01-02T00:00:00Z"),
        ("arxiv:3", "Satellite downscaling", "Statistical downscaling.", "2099-01-01T00:00:00Z"),
    ])


def test_judged_batch_stats_rows_and_prompt() -> None:
    conn = db.connect(":memory:")
    three(conn)
    llm, seen = judge_llm(answer("1|IN|radar|on topic\n2|OUT|climate, not weather\n3|IN|satellite|ok"))
    stats = db.job(conn, "triage", "t", lambda id, s: triage.run(conn, llm, id, JUDGED, s))
    assert jsonfmt.dumps(stats) == '{"cached":0,"judge_dropped":1,"kept":2,"to_judge":3,"window_candidates":3}'
    rows = conn.execute("SELECT item_id, contract_sha, verdict, tier, reason FROM triage ORDER BY item_id").fetchall()
    system = triage.judge_prompt(JUDGED)
    sha = db.sha256(f"{JUDGED.retrieval.contract_json()}\n{system}")  # type: ignore[union-attr]
    assert [tuple(r) for r in rows] == [
        ("arxiv:1", sha, "kept", "core", "[radar] on topic"),
        ("arxiv:2", sha, "judge_dropped", None, "climate, not weather"),
        ("arxiv:3", sha, "kept", "secondary", "[satellite] ok"),
    ]
    sent = json.loads(seen[0].content)
    assert sent["messages"][0] == {"role": "system", "content": system}
    assert sent["messages"][1]["content"] == (
        "intent: radar nowcasting\nin_scope:\n  - radar\nout_of_scope:\n  - climate\n"
        "facets: radar, satellite\nquality_bar: peer-level\n"
        "\n[1] Radar nowcasting\nLine one line two " + "x" * 262 +
        "\n[2] Climate projection\n"
        "\n[3] Satellite downscaling\nStatistical downscaling."
    )
    # Cached now: nothing is sent again.
    stats = db.job(conn, "triage", "t", lambda id, s: triage.run(conn, llm, id, JUDGED, s))
    assert stats == {"window_candidates": 3, "cached": 3, "cached_kept": 2, "cached_judge_dropped": 1, "to_judge": 0}
    assert len(seen) == 1


def test_failed_batches_keep_items_unjudged_and_fail_the_run() -> None:
    conn = db.connect(":memory:")
    items(conn, [(f"arxiv:{n:02}", f"Paper {n}", None, f"2099-01-01T00:00:{n:02}Z") for n in range(21)])
    calls: list[int] = []

    def reply(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return answer("1|IN|ok")(request)
        return httpx.Response(400, json={"error": {"message": "secret-bearing provider body"}})

    llm, _ = judge_llm(reply)
    t = topic(arxiv={"categories": ["cs.CV"]}, retrieval={"intent": "x"})
    with pytest.raises(triage.TriageError) as failure:
        db.job(conn, "triage", "t", lambda id, s: triage.run(conn, llm, id, t, s))
    message = str(failure.value)
    assert message.startswith("judge failed on 1 of 2 batches; those items are kept and re-judged next run: judge call to ")
    stats = json.loads(conn.execute("SELECT stats FROM runs").fetchone()[0])
    assert (stats["kept"], stats["unjudged"]) == (1, 20)
    unjudged = conn.execute("SELECT count(*) FROM triage WHERE contract_sha = '' AND verdict = 'kept' "
                            "AND reason = 'not judged yet (judge failed or skipped it)'").fetchone()[0]
    assert unjudged == 20


@pytest.mark.parametrize("finish", ["content_filter", "refusal"])
def test_refused_judge_reply_fails_the_run(finish: str) -> None:
    conn = db.connect(":memory:")
    three(conn)
    # A refusal may still carry text that looks like verdicts; none is trusted.
    llm, _ = judge_llm(answer("1|IN|radar|on topic", finish))
    with pytest.raises(triage.TriageError) as failure:
        db.job(conn, "triage", "t", lambda id, s: triage.run(conn, llm, id, JUDGED, s))
    call = conn.execute("SELECT id FROM llm_calls").fetchone()[0]
    assert str(failure.value) == (
        "judge failed on 1 of 1 batches; those items are kept and re-judged next run: "
        f"judge reply was refused by the model ({finish}); see llm_calls {call}"
    )
    status, stats, error = conn.execute("SELECT status, stats, error FROM runs").fetchone()
    assert (status, error) == ("failed", str(failure.value))
    assert json.loads(stats) == {"cached": 0, "to_judge": 3, "unjudged": 3, "window_candidates": 3}
    rows = conn.execute("SELECT contract_sha, verdict, reason FROM triage").fetchall()
    assert {tuple(r) for r in rows} == {("", "kept", triage.UNJUDGED)}
