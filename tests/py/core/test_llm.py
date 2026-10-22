import json
import re
from collections.abc import Callable

import httpx
import pytest

from isbe.core import db, http, jsonfmt, llm
from isbe.core.errors import describe
from isbe.core.llm import Llm, LlmError, Prompt, Refused, Tier
from isbe.core.validators import Invalid

PROMPT = Prompt(purpose="test", tier=Tier.FAST, system="test", user="test", max_tokens=10)
type Handler = Callable[[httpx.Request], httpx.Response]


def configured(handler: Handler, **values: str) -> tuple[Llm, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    env = {"ISBE_LLM_PROVIDER": "openai", "ISBE_LLM_BASE_URL": "http://127.0.0.1:9/v1"}
    env.setdefault("ISBE_LLM_NO_AUTH", "true")
    env.update(values)
    client = http.Http(5, transport=httpx.MockTransport(record))
    model = Llm.from_vars(env.get, client=client)
    model._sleep = lambda seconds: seen.append(seconds)  # type: ignore[assignment,arg-type]
    return model, seen


def answer(body: object, status: int = 200) -> Handler:
    return lambda request: httpx.Response(status, content=json.dumps(body).encode())


def run(model: Llm, prompt: Prompt = PROMPT) -> tuple[object, object]:
    conn = db.connect(":memory:")
    outcome: dict[str, object] = {}

    def call(run_id: str, stats: dict[str, object]) -> None:
        outcome["reply"] = model.complete(conn, run_id, prompt)

    try:
        db.job(conn, "test", None, call)
    except Exception as error:
        return error, conn
    return outcome["reply"], conn


def test_no_auth_is_explicit_local_and_does_not_disable_remote_key_validation() -> None:
    def config(provider: str, base: str, no_auth: str) -> Llm:
        env = {"ISBE_LLM_PROVIDER": provider, "ISBE_LLM_BASE_URL": base, "ISBE_LLM_NO_AUTH": no_auth}
        return Llm.from_vars(env.get)

    for base in [
        "http://localhost:11434/v1",
        "http://127.0.0.1:11434/v1",
        "http://192.168.1.2:8000/v1",
        "http://[::1]:8000/v1",
        "http://[fd00::1]:8000/v1",
    ]:
        assert config("openai", base, "true")._key == "", base
        with pytest.raises(LlmError, match="OPENAI_API_KEY is not set"):
            config("openai", base, "false")
    for base in [
        "",
        "https://api.openai.com/v1",
        "http://8.8.8.8/v1",
        "http://localhost.example/v1",
        "http://user:secret@localhost/v1",
        "file:///tmp/model",
        "http://[fe80::1]/v1",
    ]:
        with pytest.raises((LlmError, Invalid)):
            config("openai", base, "true")
    with pytest.raises(Invalid, match="ISBE_LLM_NO_AUTH must be true or false"):
        config("openai", "http://localhost/v1", "yes")
    with pytest.raises(LlmError, match="ANTHROPIC_API_KEY"):
        config("anthropic", "http://localhost", "true")
    with pytest.raises(LlmError, match="DEEPSEEK_API_KEY"):
        config("deepseek", "http://localhost", "true")


def test_from_vars_defaults_trims_and_reports_bad_providers() -> None:
    model = Llm.from_vars({"ANTHROPIC_API_KEY": " k ", "ISBE_LLM_MODEL_SMART": " big "}.get)
    assert (model.provider, model.base, model.fast, model.smart) == (
        "anthropic", "https://api.anthropic.com", "claude-haiku-5-5", "big",
    )
    assert model._key == "k" and model.cap == 2**32 - 1 and model.extra == {}
    assert model.http.timeout == 900
    assert "k" not in repr(model).replace("anthropic", "").replace("haiku", "")
    tuned = Llm.from_vars({
        "ISBE_LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "k", "ISBE_LLM_BASE_URL": "https://d.example//",
        "ISBE_LLM_MAX_TOKENS": "+64", "ISBE_LLM_TIMEOUT_S": "30",
        "ISBE_LLM_EXTRA_BODY": '{"temperature": 0}',
    }.get)
    assert (tuned.base, tuned.cap, tuned.extra, tuned.http.timeout) == (
        "https://d.example", 64, {"temperature": 0}, 30,
    )
    with pytest.raises(LlmError) as caught:
        Llm.from_vars({"ISBE_LLM_PROVIDER": 'Open"AI\n'}.get)
    assert str(caught.value) == 'ISBE_LLM_PROVIDER="Open\\"AI": expected anthropic, deepseek or openai'


def test_local_fixture_receives_no_authorization_even_with_a_saved_key() -> None:
    reply = {"choices": [{"message": {"content": "fixture reply"}, "finish_reason": "stop"}]}
    for no_auth, expected in [("true", None), ("false", "Bearer fixture-key")]:
        model, seen = configured(answer(reply), ISBE_LLM_NO_AUTH=no_auth, OPENAI_API_KEY="fixture-key")
        response = model.send("fixture-model", PROMPT, 10)
        assert llm.parse_openai(response)[0] == "fixture reply"
        assert seen[0].url.path == "/v1/chat/completions"
        assert seen[0].headers.get("authorization") == expected


def test_wire_formats_of_each_provider() -> None:
    ok = {"content": [{"type": "text", "text": "a"}], "stop_reason": "end_turn"}
    model, seen = configured(answer(ok), ISBE_LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="secret-a",
                             ISBE_LLM_BASE_URL="https://anthropic.test/", ISBE_LLM_MAX_TOKENS="7",
                             ISBE_LLM_EXTRA_BODY='{"metadata":{"user_id":"u"}}')
    reply, _ = run(model, Prompt("digest", Tier.SMART, "sys", "usr", 4000))
    assert reply.text == "a" and reply.stop == "end_turn"  # type: ignore[attr-defined]
    request = seen[0]
    assert str(request.url) == "https://anthropic.test/v1/messages"
    assert request.headers["x-api-key"] == "secret-a"
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in request.headers
    assert request.content == jsonfmt.dumps({
        "max_tokens": 7, "messages": [{"content": "usr", "role": "user"}],
        "metadata": {"user_id": "u"}, "model": "claude-sonnet-5-5", "system": "sys",
    }).encode()
    chat = {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]}
    model, seen = configured(answer(chat), ISBE_LLM_PROVIDER="deepseek", DEEPSEEK_API_KEY="k",
                             ISBE_LLM_NO_AUTH="false", ISBE_LLM_BASE_URL="https://ds.test")
    run(model, Prompt("judge", Tier.FAST, "s", "u", 20000))
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/chat/completions"
    assert body == {"model": "deepseek-chat", "max_tokens": 8192,
                    "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]}
    model, seen = configured(answer(chat))
    run(model, Prompt("judge", Tier.FAST, "s", "u", 20000))
    body = json.loads(seen[0].content)
    assert body["max_completion_tokens"] == 20000 and "max_tokens" not in body
    assert body["model"] == "gpt-5-mini"


def test_calls_are_logged_in_the_stored_format() -> None:
    reply_body = {"choices": [{"message": {"content": "主题 reply"}, "finish_reason": "stop"}],
                  "usage": {"prompt_tokens": 17, "completion_tokens": 3}}
    model, _ = configured(answer(reply_body), ISBE_LLM_MAX_TOKENS="5")
    reply, conn = run(model)
    row = conn.execute("SELECT * FROM llm_calls").fetchone()  # type: ignore[attr-defined]
    prompt = '{"max_tokens":5,"system":"test","user":"test"}'
    assert row["prompt"] == prompt
    assert row["prompt_sha"] == db.sha256(prompt)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z/test/" + db.sha256(prompt)[:12], row["id"])
    assert row["id"].split("/")[0] == row["created_at"]
    assert (row["purpose"], row["model"], row["response"], row["tokens_in"], row["tokens_out"]) == (
        "test", "gpt-5-mini", "主题 reply", 17, 3,
    )
    assert isinstance(row["ms"], int) and row["ms"] >= 0
    run_id = conn.execute("SELECT id FROM runs").fetchone()[0]  # type: ignore[attr-defined]
    assert row["run_id"] == run_id
    assert reply.call_id == row["id"] and reply.stop == "stop"  # type: ignore[attr-defined]


def test_provider_error_body_is_not_exposed_or_persisted() -> None:
    model, _ = configured(
        answer({"error": {"message": "Invalid key fixture-secret"}}, 401),
        ISBE_LLM_NO_AUTH="false", OPENAI_API_KEY="fixture-secret",
    )
    error, conn = run(model)
    stored = conn.execute("SELECT error FROM runs").fetchone()[0]  # type: ignore[attr-defined]
    for message in [describe(error), stored]:  # type: ignore[arg-type]
        assert "HTTP 401" in message
        assert "fixture-secret" not in message
        assert "Invalid key" not in message
    assert stored == "test call to gpt-5-mini: HTTP 401: model provider rejected the request"


KNOWN = {"error": {
    "type": "invalid_request_error",
    "message": "request exceeds the context window: prompt (10064 tokens) + max tokens (24000) "
               "exceeds the context (32768); fixture-secret",
}}


@pytest.mark.parametrize(
    ("status", "body", "classified"),
    [
        (400, json.dumps(KNOWN), True),
        (401, json.dumps(KNOWN), False),
        (400, json.dumps({"error": {**KNOWN["error"], "type": "fixture-secret"}}), False),
        (400, json.dumps({"error": {**KNOWN["error"], "message": "Invalid key fixture-secret"}}), False),
        (400, "{fixture-secret", False),
        (400, json.dumps({**KNOWN, "padding": "x" * 16 * 1024}), False),
    ],
)
def test_context_rejection_uses_only_a_static_message(status: int, body: str, classified: bool) -> None:
    model, _ = configured(lambda request: httpx.Response(status, content=body.encode()))
    error, conn = run(model)
    stored = conn.execute("SELECT error FROM runs").fetchone()[0]  # type: ignore[attr-defined]
    expected = (
        "HTTP 400: input and requested output exceed the model context window; reduce input or maximum output tokens"
        if classified
        else f"HTTP {status}: model provider rejected the request"
    )
    for message in [describe(error), stored]:  # type: ignore[arg-type]
        assert message.endswith(expected), message
        for secret in ["fixture-secret", "10064", "24000", "32768"]:
            assert secret not in message


REPORT = Prompt(purpose="digest", tier=Tier.SMART, system="s", user="u", max_tokens=24_000)


def context(prompt: int, window: int, finish: str = "stop") -> Handler:
    """Strata with a `window`-token context and `fit_max_tokens` off, counting `prompt`
    tokens for any prompt: it refuses prompt + max_tokens + 8 > context."""

    def handle(request: httpx.Request) -> httpx.Response:
        asked = json.loads(request.content)["max_completion_tokens"]
        if prompt + asked + 8 > window:
            message = (f"request exceeds the context window: prompt ({prompt} tokens) + "
                       f"max tokens ({asked}) exceeds the context ({window}); fixture-secret")
            body = {"error": {"type": "invalid_request_error", "message": message}}
            return httpx.Response(400, content=json.dumps(body).encode())
        reply = {"choices": [{"message": {"content": "## TL;DR"}, "finish_reason": finish}],
                 "usage": {"prompt_tokens": prompt, "completion_tokens": 5}}
        return httpx.Response(200, content=json.dumps(reply).encode())

    return handle


def asked(seen: list[httpx.Request]) -> list[int]:
    return [json.loads(r.content)["max_completion_tokens"] for r in seen]


def test_context_rejection_is_retried_once_with_the_output_the_context_leaves() -> None:
    # The nowcasting rewrite on the server: 9057-token prompt, 24000 output, 32768 context.
    model, seen = configured(context(9057, 32768))
    reply, conn = run(model, REPORT)
    assert reply.text == "## TL;DR"  # type: ignore[attr-defined]
    assert asked(seen) == [24_000, 32768 - 9057 - 64]
    logged = conn.execute("SELECT prompt FROM llm_calls").fetchall()  # type: ignore[attr-defined]
    assert [json.loads(row[0])["max_tokens"] for row in logged] == [32768 - 9057 - 64]
    assert conn.execute("SELECT status FROM runs").fetchone()[0] == "ok"  # type: ignore[attr-defined]


def test_context_fit_is_tried_once_and_only_when_it_can_help() -> None:
    def rejects(prompt: int, window: int) -> Handler:
        message = (f"request exceeds the context window: prompt ({prompt} tokens) + "
                   f"max tokens (24000) exceeds the context ({window}); fixture-secret")
        return answer({"error": {"type": "invalid_request_error", "message": message}}, 400)

    # A second rejection, a prompt the context cannot hold, and a context that
    # already holds the output asked for: no further request in any case.
    for handler, requests in [
        (rejects(9057, 32768), [24_000, 32768 - 9057 - 64]),
        (rejects(40_000, 32768), [24_000]),
        (rejects(1000, 32768), [24_000]),
        (answer({"error": {**KNOWN["error"], "message": "request exceeds the context window: prompt ("}}, 400),
         [24_000]),
    ]:
        model, seen = configured(handler)
        error, conn = run(model, REPORT)
        assert asked(seen) == requests
        assert describe(error).endswith(  # type: ignore[arg-type]
            "HTTP 400: input and requested output exceed the model context window; "
            "reduce input or maximum output tokens"
        )
        assert "fixture-secret" not in describe(error)  # type: ignore[arg-type]
        assert conn.execute("SELECT count(*) FROM llm_calls").fetchone()[0] == 0  # type: ignore[attr-defined]


def test_a_reply_cut_off_at_a_fitted_limit_says_the_limit_was_lowered() -> None:
    model, _ = configured(context(9057, 32768, finish="length"))
    error, _ = run(model, REPORT)
    assert f"digest reply hit max_tokens ({32768 - 9057 - 64}, lowered to fit the model context)" in str(error)


def test_null_content_at_length_limit_is_logged_as_a_failed_call() -> None:
    body = {"choices": [{"message": {"content": None, "reasoning_content": "private reasoning"},
                         "finish_reason": "length"}],
            "usage": {"prompt_tokens": 17, "completion_tokens": 10}}
    model, _ = configured(answer(body))
    error, conn = run(model)
    recorded = conn.execute("SELECT response, tokens_in, tokens_out FROM llm_calls").fetchone()  # type: ignore[attr-defined]
    assert tuple(recorded) == ("", 17, 10)
    status, stored = conn.execute("SELECT status, error FROM runs").fetchone()  # type: ignore[attr-defined]
    assert status == "failed"
    for message in [describe(error), stored]:  # type: ignore[arg-type]
        assert "hit max_tokens (10)" in message
        assert "see llm_calls" in message
        assert "private reasoning" not in message


def test_null_content_requires_length_and_other_invalid_content_is_rejected() -> None:
    response = {"choices": [{"message": {"content": None}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3}}
    assert llm.parse_openai(response) == ("", 7, 3, "length")
    for reason in ["stop", "tool_calls", None]:
        response["choices"][0]["finish_reason"] = reason  # type: ignore[index]
        with pytest.raises(ValueError):
            llm.parse_openai(response)
    response["choices"][0]["finish_reason"] = "length"  # type: ignore[index]
    for content in [17, True, [], {}]:
        response["choices"][0]["message"]["content"] = content  # type: ignore[index]
        with pytest.raises(ValueError):
            llm.parse_openai(response)
    response["choices"][0]["message"] = {"reasoning_content": "not an answer"}  # type: ignore[index]
    with pytest.raises(ValueError):
        llm.parse_openai(response)


def test_parses_both_wire_formats() -> None:
    a = {"content": [{"type": "thinking", "thinking": "hm"}, {"type": "text", "text": "## TL;DR"},
                     {"type": "text", "text": "\n- x"}],
         "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 4}}
    assert llm.parse_anthropic(a) == ("## TL;DR\n- x", 10, 4, "end_turn")
    o = {"choices": [{"message": {"content": "1|IN|x|y"}, "finish_reason": "length"}],
         "usage": {"prompt_tokens": 7, "completion_tokens": 3}}
    assert llm.parse_openai(o) == ("1|IN|x|y", 7, 3, "length")
    with pytest.raises(ValueError):
        llm.parse_anthropic({"type": "error"})
    assert llm.parse_anthropic({"content": [], "usage": {"input_tokens": 1.0, "output_tokens": True}}) == (
        "", None, None, None,
    )


def test_anthropic_max_tokens_cut_is_an_error() -> None:
    body = {"content": [{"type": "text", "text": "partial"}], "stop_reason": "max_tokens"}
    model, _ = configured(answer(body), ISBE_LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="k")
    error, conn = run(model)
    assert isinstance(error, LlmError) and "hit max_tokens (10)" in str(error)
    assert conn.execute("SELECT response FROM llm_calls").fetchone()[0] == "partial"  # type: ignore[attr-defined]


def test_transient_failures_are_retried_twice_but_timeouts_are_not() -> None:
    statuses = iter([429, 503, 200])
    ok = {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]}
    model, seen = configured(lambda r: httpx.Response(next(statuses), json=ok))
    reply, _ = run(model)
    assert reply.text == "done"  # type: ignore[attr-defined]
    assert [s for s in seen if not isinstance(s, httpx.Request)] == [5, 30]
    model, seen = configured(answer({}, 500))
    error, _ = run(model)
    assert describe(error).endswith("HTTP 500: model provider rejected the request")  # type: ignore[arg-type]
    assert len([s for s in seen if isinstance(s, httpx.Request)]) == 3
    model, seen = configured(answer({}, 404))
    run(model)
    assert len(seen) == 1, "a client error is not retried"

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    model, seen = configured(refuse)
    error, _ = run(model)
    assert "connection refused" in describe(error)  # type: ignore[arg-type]
    assert seen.count(5) == 1 and seen.count(30) == 1

    def stall(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    model, seen = configured(stall)
    error, conn = run(model)
    assert isinstance(error, LlmError) and len(seen) == 1
    assert conn.execute("SELECT count(*) FROM llm_calls").fetchone()[0] == 0  # type: ignore[attr-defined]


def test_issue38c_extra_body_never_replaces_request_fields() -> None:
    with pytest.raises(Invalid, match="must not set model"):
        Llm.from_vars({"ANTHROPIC_API_KEY": "k", "ISBE_LLM_EXTRA_BODY": '{"model":"other"}'}.get)
    ok = {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]}
    model, seen = configured(answer(ok), ISBE_LLM_EXTRA_BODY='{"temperature":0.5}')
    model.extra.update({"model": "evil", "stream": True, "messages": [], "max_completion_tokens": 1})
    run(model)
    body = json.loads(seen[0].content)
    assert body["model"] == "gpt-5-mini" and body["max_completion_tokens"] == 10
    assert body["temperature"] == 0.5 and "stream" not in body and len(body["messages"]) == 2


def test_issue38d_refusals_are_logged_and_raised_with_the_stop_reason() -> None:
    refusal = {"content": [], "stop_reason": "refusal", "usage": {"input_tokens": 5, "output_tokens": 0}}
    model, _ = configured(answer(refusal), ISBE_LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="k")
    error, conn = run(model)
    assert isinstance(error, Refused)
    assert error.reply.stop == "refusal" and error.reply.text == ""
    row = conn.execute("SELECT id, response FROM llm_calls").fetchone()  # type: ignore[attr-defined]
    assert tuple(row) == (error.reply.call_id, "")
    assert conn.execute("SELECT status FROM runs").fetchone()[0] == "failed"  # type: ignore[attr-defined]
    assert "refused" in str(error) and error.reply.call_id in str(error)
    for content in [None, "partial filtered"]:
        filtered = {"choices": [{"message": {"content": content}, "finish_reason": "content_filter"}]}
        model, _ = configured(answer(filtered))
        error, _ = run(model)
        assert isinstance(error, Refused) and error.reply.stop == "content_filter"
        assert error.reply.text == (content or "")


def test_a_real_local_server_sees_the_request(serve) -> None:  # type: ignore[no-untyped-def]
    seen: list[tuple[str, str | None, bytes]] = []

    def model(req) -> None:  # type: ignore[no-untyped-def]
        body = req.rfile.read(int(req.headers["Content-Length"]))
        seen.append((req.path, req.headers.get("Authorization"), body))
        serve.reply(req, 200, json.dumps({"choices": [{"message": {"content": "served"}, "finish_reason": "stop"}]}))

    base = serve(model)
    env = {"ISBE_LLM_PROVIDER": "openai", "ISBE_LLM_BASE_URL": f"{base}/v1", "ISBE_LLM_NO_AUTH": "true"}
    with http.Http(5, environ={}) as client:
        reply, _ = run(Llm.from_vars(env.get, client=client))
    assert reply.text == "served"  # type: ignore[attr-defined]
    assert seen == [(
        "/v1/chat/completions",
        None,
        b'{"max_completion_tokens":10,"messages":[{"content":"test","role":"system"},'
        b'{"content":"test","role":"user"}],"model":"gpt-5-mini"}',
    )]
