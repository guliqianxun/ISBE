from isbe.question import plan, problems, sources


def test_an_essaies_failure_is_named_for_whose_fault_it_is():
    code = problems.source_code
    assert code(sources.Unreachable("refused", connect=True)) == problems.ESSAIES_UNREACHABLE
    assert code(sources.Unreachable("3 in a row", 503)) == problems.ESSAIES_DOWN
    assert code(sources.SourceError("timed out", kind="ReadTimeout")) == problems.ESSAIES_DOWN
    assert code(sources.SourceError("answered HTTP 503", 503)) == problems.ESSAIES_DOWN
    # A proxy or essaies refusing the service is not the query's fault.
    for status in (401, 403, 405):
        assert code(sources.SourceError(f"answered HTTP {status}", status)) == problems.ESSAIES_DOWN
    assert code(sources.SourceError("answered HTTP 429", 429)) == problems.OPENALEX_QUOTA
    assert code(sources.SourceError("answered HTTP 400: bad query", 400)) == problems.QUERY_FAILED


def test_public_text_names_the_failure_without_the_reply():
    assert sources.SourceError("essaies answered HTTP 503: <html>host</html>", 503).public() == "HTTP 503"
    error = sources.Unreachable("x", connect=True, kind="ConnectError", os_error="[Errno 111] Connection refused")
    assert error.public() == "ConnectError: [Errno 111] Connection refused"
    assert sources.SourceError("x", kind="RemoteProtocolError").public() == "RemoteProtocolError"


def test_a_model_detail_is_the_cause_without_the_call_or_the_advice():
    detail = problems.model_detail
    assert detail("question_screen call to gpt-x: HTTP 503: provider error") == "HTTP 503: provider error"
    assert detail("question_plan reply from m: no JSON object") == "no JSON object"
    assert detail("question_screen call to qwen3:8b: HTTP 503: down") == "HTTP 503: down"
    assert detail("question_plan call to anthropic.claude-v1:0: timed out") == "timed out"
    assert detail("ANTHROPIC_API_KEY is not set (ISBE_LLM_PROVIDER=anthropic); local services may…") == (
        "ANTHROPIC_API_KEY is not set (ISBE_LLM_PROVIDER=anthropic)"
    )
    assert detail("") == "no model is configured"


def test_a_fallback_plan_says_why_it_is_one():
    rationale = "No model plan (HTTP 503 (twice)); the question itself is the only query."
    assert plan.fallback_reason({"source": "fallback", "rationale": rationale}) == "HTTP 503 (twice)"
    assert plan.fallback_reason({"source": "model", "rationale": rationale}) is None
