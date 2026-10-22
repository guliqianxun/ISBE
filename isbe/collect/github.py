"""Tracked GitHub repos: one REST call per repo for stars and last push
. `GITHUB_TOKEN` is optional (60 unauthenticated calls
an hour is plenty)."""

from typing import Any

from isbe.core import http, settings

from .items import CollectError, Item, checked


def fetch(client: http.Http, api: str, repo: str) -> Item:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = settings.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Renamed repos answer 301; the client follows it.
    response = checked(client.get(f"{api.rstrip('/')}/repos/{repo}", headers=headers))
    try:
        body = response.json()
    except ValueError as error:
        raise CollectError(f"json: {error}") from None
    return parse(repo, body)


def parse(repo: str, v: Any) -> Item:
    """`published_at` is the creation date, which never moves; stars and the last
    push live in `extra`, which every collect refreshes."""
    obj = v if isinstance(v, dict) else {}

    def s(key: str) -> str | None:
        value = obj.get(key)
        return value if isinstance(value, str) else None

    owner = obj.get("owner")
    login = owner.get("login") if isinstance(owner, dict) else None
    created = s("created_at")
    if created is None:
        raise CollectError(f"{repo}: no created_at")
    return Item(
        id=f"gh:{repo}",
        kind="repo",
        source="github",
        title=s("full_name") or repo,
        summary=s("description"),
        url=s("html_url") or f"https://github.com/{repo}",
        authors=[login] if isinstance(login, str) else [],
        lang="und",
        published_at=created,
        extra={
            "stars": obj.get("stargazers_count"),
            "pushed_at": obj.get("pushed_at"),
            "language": obj.get("language"),
            "archived": obj.get("archived"),
        },
    )
