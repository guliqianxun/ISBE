"""essaies, the local arXiv archive: one FTS5 search over the window."""

from datetime import datetime
from typing import Any

from isbe.core import http, validators
from isbe.topics import Arxiv
from isbe.topics.model import debug

from .items import CollectError, Item, checked, split_whitespace, squash
from .timestamps import ymd

# ponytail: one page at essaies' cap; a window with more hits loses its oldest.
# Page with offset if a topic ever gets there.
LIMIT = "2000"


def fetch(client: http.Http, base: str, a: Arxiv, since: datetime) -> list[Item]:
    params = [
        ("q", fts_query(a.include_keywords)),
        ("since", f"{since.year:04}-{since.month:02}-{since.day:02}"),
        ("categories", ",".join(a.categories)),
        ("sort", "date"),
        ("limit", LIMIT),
    ]
    response = client.get(f"{base.rstrip('/')}/api/search", params=params)
    if response.status == 422:
        raise CollectError(
            "essaies rejected the search (422): the server predates since=/categories=, update it"
        )
    try:
        body = checked(response).json()
    except ValueError as error:
        raise CollectError(f"json: {error}") from None
    return parse(body)


def fts_query(keywords: list[str]) -> str:
    """`"radar echo" OR "QPE"`: each keyword as an FTS5 phrase of its alphanumeric
    tokens, so `text-to-video` matches the way the arXiv API's phrase search does."""
    phrases = []
    for keyword in keywords:
        tokens, current = [], []
        for c in keyword + " ":
            if c.isalnum():
                current.append(c)
            elif current:
                tokens.append("".join(current))
                current = []
        if tokens:
            phrases.append(f'"{" ".join(tokens)}"')
    return " OR ".join(phrases)


def _prefix(date: str) -> str:
    """`date.get(..10).unwrap_or(&date)`: the first 10 bytes when that is a char boundary."""
    raw = date.encode()
    if len(raw) < 10:
        return date
    try:
        return raw[:10].decode()
    except UnicodeDecodeError:
        return date


def _take(extra: dict[str, Any], key: str) -> str | None:
    """Remove `key` from the row's leftover fields; its value if it is a string."""
    value = extra.pop(key, None)
    return value if isinstance(value, str) else None


def parse(resp: Any) -> list[Item]:
    """Rows of `/api/search`. What ISBE does not map to a column (has_pdf, path,
    year, citation metrics) is kept in `extra`."""
    rows = resp.get("items") if isinstance(resp, dict) else None
    if not isinstance(rows, list):
        raise CollectError("essaies response has no items array")
    items = []
    for row in rows:
        if not isinstance(row, dict):
            raise CollectError("essaies item is not an object")
        extra = dict(row)
        paper = _take(extra, "id")
        if paper is None:
            raise CollectError("essaies item without id")
        # A server without `since=` ignores it and returns the whole archive's
        # best matches; it also predates `update_date`, so refuse on that.
        date = _take(extra, "update_date")
        if date is None:
            raise CollectError(
                "essaies is too old: /api/search must support since= and return update_date"
            )
        day = ymd(_prefix(date))
        if day is None:
            raise CollectError(f"{paper}: bad update_date {debug(date)}")
        title = squash(_take(extra, "title") or "")
        abstract = _take(extra, "abstract")
        authors = _take(extra, "authors")
        categories = _take(extra, "categories")
        extra.pop("rank", None)
        extra["categories"] = [] if categories is None else split_whitespace(categories)
        items.append(
            Item(
                id=f"arxiv:{paper}",
                kind="paper",
                source="essaies",
                title=title,
                summary=None if abstract is None else squash(abstract),
                url=f"https://arxiv.org/abs/{paper}",
                authors=[] if authors is None else [
                    name for part in authors.split(";") if (name := validators.trim(part))
                ],
                lang="en",
                published_at=f"{day.year:04}-{day.month:02}-{day.day:02}T00:00:00Z",
                extra=extra,
            )
        )
    return items
