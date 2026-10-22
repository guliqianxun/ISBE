"""The public arXiv API (Atom), for installs without essaies."""

import time
from collections.abc import Callable
from datetime import datetime

from isbe.core import http, validators
from isbe.topics import Arxiv

from . import feed
from .items import CollectError, Item, checked, squash
from .timestamps import format_rfc3339


def fetch(
    client: http.Http,
    api: str,
    a: Arxiv,
    since: datetime,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Item]:
    params = [
        ("search_query", query(a)),
        ("sortBy", "submittedDate"),
        ("sortOrder", "descending"),
        ("max_results", str(a.max_results)),
    ]
    body = checked(client.get(api, params=params)).content
    # arXiv's API terms: no more than one request every three seconds.
    sleep(3)
    return parse(body, since)


def query(a: Arxiv) -> str:
    """`(cat:a OR cat:b) AND (ti:"k" OR abs:"k" ...)`: exact phrases over title and abstract."""
    cats = [f"cat:{c}" for c in a.categories]
    phrases = []
    for keyword in a.include_keywords:
        keyword = validators.trim(keyword)
        if keyword:
            keyword = keyword.replace("\\", "\\\\").replace('"', '\\"')
            phrases += [f'ti:"{keyword}"', f'abs:"{keyword}"']
    return f"({' OR '.join(cats)}) AND ({' OR '.join(phrases)})"


def parse(atom: bytes, since: datetime) -> list[Item]:
    parsed = feed.parse(atom)
    # arXiv can return an Atom error entry even when HTTP succeeds.
    # Check before applying the date window: the error timestamp can be old.
    for entry in parsed.entries:
        if entry.id.startswith(("http://arxiv.org/api/errors#", "https://arxiv.org/api/errors#")):
            message = "search request rejected" if entry.summary is None else squash(entry.summary)
            raise CollectError(f"arXiv API error: {message}")
    items = []
    for entry in parsed.entries:
        paper = arxiv_id(entry.id)
        when = entry.updated or entry.published
        if paper is None or when is None or when < since or entry.title is None:
            continue
        items.append(
            Item(
                id=f"arxiv:{paper}",
                kind="paper",
                source="arxiv",
                title=squash(entry.title),
                summary=None if entry.summary is None else squash(entry.summary),
                url=f"https://arxiv.org/abs/{paper}",
                authors=list(entry.authors),
                lang="en",
                published_at=format_rfc3339(when),
                extra={"categories": list(entry.categories)},
            )
        )
    return items


def arxiv_id(url: str) -> str | None:
    """`http://arxiv.org/abs/2609.01234v2` → `2609.01234` (old-style `math/0601001` too)."""
    if "/abs/" not in url:
        return None
    paper = url.split("/abs/", 1)[1]
    stem = paper.rstrip("0123456789")
    return stem[:-1] if stem.endswith("v") else paper
