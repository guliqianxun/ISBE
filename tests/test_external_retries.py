"""Retry behavior on transient HTTP/LLM failures.

Ops-review-flagged: a single 429 from arxiv or a 5xx from the LLM provider
killed the whole weekly digest. Now the two external boundaries retry with
exponential backoff before giving up.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# fetch_arxiv_atom: arxiv API
# ---------------------------------------------------------------------------


def _atom(entries: int = 1) -> str:
    return (
        "<?xml version='1.0'?>"
        "<feed xmlns='http://www.w3.org/2005/Atom'>"
        + "".join(
            f"<entry><id>http://arxiv.org/abs/26{i:02d}.00001</id>"
            f"<title>p{i}</title><summary>s{i}</summary>"
            f"<published>2026-05-{1+i:02d}T00:00:00Z</published>"
            f"<author><name>A</name></author>"
            f"<arxiv:primary_category xmlns:arxiv='http://arxiv.org/schemas/atom' term='cs.LG'/>"
            "</entry>"
            for i in range(entries)
        )
        + "</feed>"
    )


def _resp(status: int, body: str = "") -> httpx.Response:
    req = httpx.Request("GET", "https://export.arxiv.org/api/query")
    return httpx.Response(status_code=status, text=body, request=req)


def test_arxiv_retries_on_429_then_succeeds() -> None:
    from isbe.topics._shared import arxiv as arxiv_mod

    call_count = [0]

    def fake_get(url, timeout):
        call_count[0] += 1
        if call_count[0] < 3:
            return _resp(429, "rate limited")
        return _resp(200, _atom(entries=1))

    with patch.object(arxiv_mod.httpx, "get", side_effect=fake_get):
        with patch.object(arxiv_mod, "_sleep_between_attempts", lambda s: None):
            entries = arxiv_mod.fetch_arxiv_atom.fn(["cs.LG"], ["nowcasting"], 10)

    assert call_count[0] == 3
    assert len(entries) == 1


def test_arxiv_retries_on_5xx_then_succeeds() -> None:
    from isbe.topics._shared import arxiv as arxiv_mod

    seq = [_resp(503), _resp(502), _resp(200, _atom(1))]

    def fake_get(url, timeout):
        return seq.pop(0)

    with patch.object(arxiv_mod.httpx, "get", side_effect=fake_get):
        with patch.object(arxiv_mod, "_sleep_between_attempts", lambda s: None):
            entries = arxiv_mod.fetch_arxiv_atom.fn(["cs.LG"], ["x"], 10)
    assert entries  # didn't raise
    assert not seq  # consumed all 3 responses


def test_arxiv_gives_up_after_max_attempts() -> None:
    from isbe.topics._shared import arxiv as arxiv_mod

    call_count = [0]

    def always_429(url, timeout):
        call_count[0] += 1
        return _resp(429, "still rate limited")

    with patch.object(arxiv_mod.httpx, "get", side_effect=always_429):
        with patch.object(arxiv_mod, "_sleep_between_attempts", lambda s: None):
            with pytest.raises(httpx.HTTPStatusError):
                arxiv_mod.fetch_arxiv_atom.fn(["cs.LG"], ["x"], 10)
    assert call_count[0] == 3  # exactly max_attempts


def test_arxiv_does_not_retry_on_4xx_other_than_429() -> None:
    from isbe.topics._shared import arxiv as arxiv_mod

    call_count = [0]

    def fake_get(url, timeout):
        call_count[0] += 1
        return _resp(404, "not found")

    with patch.object(arxiv_mod.httpx, "get", side_effect=fake_get):
        with patch.object(arxiv_mod, "_sleep_between_attempts", lambda s: None):
            with pytest.raises(httpx.HTTPStatusError):
                arxiv_mod.fetch_arxiv_atom.fn(["cs.LG"], ["x"], 10)
    assert call_count[0] == 1  # no retry on permanent client error


def test_arxiv_retries_on_timeout() -> None:
    from isbe.topics._shared import arxiv as arxiv_mod

    call_count = [0]

    def fake_get(url, timeout):
        call_count[0] += 1
        if call_count[0] < 3:
            raise httpx.ReadTimeout("slow")
        return _resp(200, _atom(1))

    with patch.object(arxiv_mod.httpx, "get", side_effect=fake_get):
        with patch.object(arxiv_mod, "_sleep_between_attempts", lambda s: None):
            entries = arxiv_mod.fetch_arxiv_atom.fn(["cs.LG"], ["x"], 10)
    assert call_count[0] == 3
    assert entries


# ---------------------------------------------------------------------------
# complete(): LLM provider
# ---------------------------------------------------------------------------


@pytest.fixture
def force_deepseek(monkeypatch):
    monkeypatch.setenv("ISBE_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "stub")


def _ds_resp(status: int, body: dict | None = None) -> httpx.Response:
    req = httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions")
    import json

    return httpx.Response(
        status_code=status,
        content=json.dumps(body or {}).encode(),
        request=req,
    )


def test_deepseek_retries_on_5xx(monkeypatch, force_deepseek) -> None:
    from isbe.llm import client as client_mod

    call_count = [0]
    ok_body = {
        "id": "x",
        "choices": [{"message": {"content": "hello"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }

    def fake_post(url, headers, json, timeout):
        call_count[0] += 1
        if call_count[0] < 3:
            return _ds_resp(503)
        return _ds_resp(200, ok_body)

    with patch.object(client_mod.httpx, "post", side_effect=fake_post):
        with patch.object(client_mod, "_sleep_between_attempts", lambda s: None):
            resp = client_mod.complete(system="s", user="u")
    assert call_count[0] == 3
    assert resp.text == "hello"


def test_deepseek_retries_on_429(monkeypatch, force_deepseek) -> None:
    from isbe.llm import client as client_mod

    call_count = [0]
    ok_body = {
        "id": "x",
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }

    def fake_post(url, headers, json, timeout):
        call_count[0] += 1
        return _ds_resp(429) if call_count[0] < 2 else _ds_resp(200, ok_body)

    with patch.object(client_mod.httpx, "post", side_effect=fake_post):
        with patch.object(client_mod, "_sleep_between_attempts", lambda s: None):
            resp = client_mod.complete(system="s", user="u")
    assert call_count[0] == 2
    assert resp.text == "ok"


def test_deepseek_does_not_retry_on_4xx_other_than_429(monkeypatch, force_deepseek) -> None:
    from isbe.llm import client as client_mod

    call_count = [0]

    def fake_post(url, headers, json, timeout):
        call_count[0] += 1
        return _ds_resp(400, {"error": "bad request"})

    with patch.object(client_mod.httpx, "post", side_effect=fake_post):
        with patch.object(client_mod, "_sleep_between_attempts", lambda s: None):
            with pytest.raises(httpx.HTTPStatusError):
                client_mod.complete(system="s", user="u")
    assert call_count[0] == 1  # no retry — caller probably has a real bug


def test_deepseek_gives_up_after_max_attempts(monkeypatch, force_deepseek) -> None:
    from isbe.llm import client as client_mod

    call_count = [0]

    def fake_post(url, headers, json, timeout):
        call_count[0] += 1
        return _ds_resp(503)

    with patch.object(client_mod.httpx, "post", side_effect=fake_post):
        with patch.object(client_mod, "_sleep_between_attempts", lambda s: None):
            with pytest.raises(httpx.HTTPStatusError):
                client_mod.complete(system="s", user="u")
    assert call_count[0] == 3


def test_deepseek_retries_on_timeout(monkeypatch, force_deepseek) -> None:
    from isbe.llm import client as client_mod

    call_count = [0]
    ok_body = {
        "id": "x",
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }

    def fake_post(url, headers, json, timeout):
        call_count[0] += 1
        if call_count[0] < 2:
            raise httpx.ReadTimeout("slow")
        return _ds_resp(200, ok_body)

    with patch.object(client_mod.httpx, "post", side_effect=fake_post):
        with patch.object(client_mod, "_sleep_between_attempts", lambda s: None):
            resp = client_mod.complete(system="s", user="u")
    assert call_count[0] == 2
    assert resp.text == "ok"
