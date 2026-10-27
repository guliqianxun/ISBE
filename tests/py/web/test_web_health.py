"""`GET /api/health` (isbe.web.health): the worker and each upstream, with no sign-in."""

from pathlib import Path

import httpx
import pytest
from conftest import call, make_console

from isbe.core import http, upstreams
from isbe.web import health


class Essaies:
    def __init__(self) -> None:
        self.gone = False
        self.requests = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.gone:
            raise httpx.ConnectError("[Errno 113] No route to host", request=request)
        return httpx.Response(200, json={"capabilities": ["batch_lookup"]})


@pytest.fixture
def essaies(monkeypatch: pytest.MonkeyPatch) -> Essaies:
    fake = Essaies()
    monkeypatch.setenv("ESSAIES_URL", "http://essaies.test")
    monkeypatch.setattr(health, "_client", lambda: http.Http(5, transport=httpx.MockTransport(fake)))
    return fake


def test_health_probes_essaies_and_says_when_it_is_gone(data: Path, essaies: Essaies) -> None:
    console = make_console(data)
    console.generation.worker_alive = True  # type: ignore[attr-defined]
    up = call(console, "GET", "/api/health")
    assert up.status == 200 and up.body["worker"] == {"alive": True}
    papers = up.body["upstreams"]["papers"]
    assert papers["source"] == "essaies" and papers["state"] == "ok" and papers["checked_at"]
    # No model is configured, so the service is not ok.
    assert up.body["upstreams"]["model"] == {"configured": False, "state": "unknown", "checked_at": None, "error": None}
    assert up.body["ok"] is False
    # A recent answer is not asked again.
    call(console, "GET", "/api/health")
    assert essaies.requests == 1
    essaies.gone = True
    upstreams.record(upstreams.ESSAIES, True, clock=lambda: -1000.0)  # long ago
    down = call(console, "GET", "/api/health").body["upstreams"]["papers"]
    assert down["state"] == "down" and down["error"] == "ConnectError: [Errno 113] No route to host"
    assert essaies.requests == 2


def test_health_reports_what_a_run_last_saw_and_needs_no_sign_in(data: Path, essaies: Essaies, monkeypatch) -> None:
    for name, value in {"ISBE_LLM_PROVIDER": "openai", "OPENAI_API_KEY": "k", "ISBE_LLM_BASE_URL": "http://m.test/v1"}.items():
        monkeypatch.setenv(name, value)
    console = make_console(data, shared=True)
    upstreams.record(upstreams.MODEL, False, "ConnectError: [Errno 111] Connection refused")
    body = call(console, "GET", "/api/health").body
    assert body["upstreams"]["model"]["state"] == "down" and "Connection refused" in body["upstreams"]["model"]["error"]
    assert body["worker"] == {"alive": False} and body["ok"] is False
    assert call(console, "GET", "/api/session").status == 200


def test_health_probes_the_openalex_api_by_default(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[httpx.URL] = []

    def openalex(request: httpx.Request) -> httpx.Response:
        asked.append(request.url)
        return httpx.Response(200, json={"documentation_url": "https://docs.openalex.org"})

    monkeypatch.setenv("OPENALEX_API_KEY", "oa-key")
    monkeypatch.setattr(health, "_client", lambda: http.Http(5, transport=httpx.MockTransport(openalex)))
    body = call(make_console(data), "GET", "/api/health").body
    papers = body["upstreams"]["papers"]
    assert papers["source"] == "openalex" and papers["configured"] and papers["state"] == "ok"
    # One free request to the API root, with the key; the key never shows in the answer.
    assert [(u.host, u.path, u.params.get("api_key")) for u in asked] == [("api.openalex.org", "/", "oa-key")]
    assert "oa-key" not in str(body)
