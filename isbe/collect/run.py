"""`collect`: fetch every source of a topic and store what they return
. Sole writer of `items` and `topic_items`.

Configured services (essaies, the arXiv API, the GitHub API, all from
settings or flags) use plain `core.http.Http`; feed URLs use the public-only
fetcher (`rss.fetcher`).
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from isbe.core import http, netguard
from isbe.core.errors import describe
from isbe.topics import Topic

from . import arxiv, essaies, github, rss
from .items import CollectError, Item, store

TIMEOUT = 90.0


@dataclass(frozen=True)
class Endpoints:
    """Where papers come from: essaies when its URL is set, else the arXiv API."""

    essaies: str | None
    arxiv_api: str
    github_api: str


def collect(
    conn: sqlite3.Connection,
    topic: Topic,
    ep: Endpoints,
    stats: dict[str, Any],
    *,
    client: http.Http | None = None,
    feeds: netguard.PublicFetcher | None = None,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Fetch every source of `topic` and store what they return.

    A failing source does not stop the others; their items are stored, then
    `CollectError` lists every failure (`name: cause; ...`), so the run is
    recorded as failed. `stats` gets `found` (items per source name), `new`
    (items new to the topic) and `not_yet_supported` for a zhihu block.
    `client`, `feeds` and `sleep` replace the network and arXiv's pause in tests.
    """
    owned = client is None
    client = client or http.Http(TIMEOUT)
    feeds = feeds or rss.fetcher()
    now = datetime.now(UTC)
    items: list[Item] = []
    found: dict[str, int] = {}
    errors: list[str] = []

    def tally(name: str, fetch: Callable[..., list[Item]], *args: Any, **options: Any) -> None:
        try:
            got = fetch(*args, **options)
        except Exception as error:  # every source failure is reported, not raised
            errors.append(f"{name}: {describe(error)}")
            return
        found[name] = len(got)
        items.extend(got)

    try:
        if (a := topic.arxiv) is not None:
            since = topic.since(now)
            if ep.essaies is not None:
                tally("essaies", essaies.fetch, client, ep.essaies, a, since)
            else:
                pause = {} if sleep is None else {"sleep": sleep}
                tally("arxiv", arxiv.fetch, client, ep.arxiv_api, a, since, **pause)
        if (r := topic.rss) is not None:
            for feed in r.feeds:
                tally(f"rss:{feed.name}", rss.fetch, feeds, feed, r, now)
        if (g := topic.github) is not None:
            repos: list[Item] = []
            for repo in g.repos:
                try:
                    repos.append(github.fetch(client, ep.github_api, repo))
                except Exception as error:
                    errors.append(f"gh:{repo}: {describe(error)}")
            found["github"] = len(repos)
            items.extend(repos)
    finally:
        if owned:
            client.close()
    if topic.zhihu is not None:
        stats["not_yet_supported"] = ["zhihu"]
    stats["found"] = found
    stats["new"] = store(conn, topic.id, items)
    if errors:
        raise CollectError("; ".join(errors))
