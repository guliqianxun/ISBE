import httpx
import pytest

from isbe.collect import github
from isbe.collect.items import CollectError
from isbe.core import http


def test_parses_repo() -> None:
    r = github.parse("NVIDIA/modulus", {
        "full_name": "NVIDIA/physicsnemo", "description": "Sci-ML",
        "html_url": "https://github.com/NVIDIA/physicsnemo", "owner": {"login": "NVIDIA"},
        "created_at": "2023-02-01T00:00:00Z", "pushed_at": "2026-10-12T09:00:00Z",
        "stargazers_count": 2100, "language": "Python", "archived": False,
    })
    assert (r.id, r.title, r.published_at) == ("gh:NVIDIA/modulus", "NVIDIA/physicsnemo", "2023-02-01T00:00:00Z")
    assert r.authors == ["NVIDIA"]
    assert r.extra["stars"] == 2100
    assert r.extra["pushed_at"] == "2026-10-12T09:00:00Z"
    assert r.extra == {"stars": 2100, "pushed_at": "2026-10-12T09:00:00Z", "language": "Python", "archived": False}


def test_missing_fields_fall_back_and_a_repo_needs_its_creation_date() -> None:
    r = github.parse("a/b", {"created_at": "2020-01-01T00:00:00Z"})
    assert (r.title, r.url, r.summary, r.authors) == ("a/b", "https://github.com/a/b", None, [])
    assert r.extra == {"stars": None, "pushed_at": None, "language": None, "archived": None}
    with pytest.raises(CollectError, match="^a/b: no created_at$"):
        github.parse("a/b", {})


def test_token_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def api(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"created_at": "2020-01-01T00:00:00Z"})

    client = http.Http(5, transport=httpx.MockTransport(api))
    github.fetch(client, "https://api.github.com/", "a/b")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    github.fetch(client, "https://api.github.com", "a/b")
    assert [str(r.url) for r in seen] == ["https://api.github.com/repos/a/b"] * 2
    assert "authorization" not in seen[0].headers
    assert seen[1].headers["authorization"] == "Bearer ghp_test"
    assert seen[1].headers["x-github-api-version"] == "2022-11-28"
    missing = http.Http(5, transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(CollectError, match="^http status: 404$"):
        github.fetch(missing, "https://api.github.com", "a/b")
