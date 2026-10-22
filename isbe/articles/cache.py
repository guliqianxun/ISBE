"""Persist successful original reads without changing collected materials or reports.

Rows of `article_text` hold the validated article record as compact JSON
text (sorted keys), keyed by the URL the caller asked for. Only a valid, complete read is stored; a stored
row is validated again on every load and never silently replaced.
"""

import sqlite3
from collections.abc import Callable
from typing import Any

from isbe.articles import datetimes, fetch
from isbe.core import jsonfmt, validators
from isbe.core.validators import byte_len, trim

MAX_JSON = 1 << 20
MAX_TEXT = 256 << 10
MAX_PARAGRAPHS = 512
MAX_TITLE = 2048

type Article = dict[str, Any]


class FetchFailed(Exception):
    """The upstream read failed (`article_cache::FetchFailed`), as opposed to local
    persistence; raised `from` the read's error."""

    def __init__(self) -> None:
        super().__init__("original article could not be read")


def caused_by[E: BaseException](error: BaseException, kind: type[E]) -> E | None:
    """The first `kind` in `error`'s `__cause__` chain, or None."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, kind):
            return current
        seen.add(id(current))
        current = current.__cause__
    return None


def validated(value: Any) -> Article:
    """The article fields of `value` that may be stored or shown; raises ValueError."""
    record = value if isinstance(value, dict) else {}
    paragraphs = record.get("paragraphs")
    if not isinstance(paragraphs, list):
        raise ValueError("article paragraphs are missing")
    if not paragraphs or len(paragraphs) > MAX_PARAGRAPHS:
        raise ValueError("invalid article paragraph count")
    size = 0
    for paragraph in paragraphs:
        if not isinstance(paragraph, str):
            raise ValueError("article paragraph is not text")
        if not trim(paragraph):
            raise ValueError("article paragraph is empty")
        size += byte_len(paragraph)
        if size > MAX_TEXT:
            raise ValueError("article text exceeds size limit")
    final_url = record.get("final_url")
    if not isinstance(final_url, str):
        raise ValueError("article URL is missing")
    try:
        validators.validate_url(final_url)
    except validators.Invalid as error:
        # An invalid stored URL is a plain error (HTTP 500), not a 400.
        raise ValueError(error.message) from None
    fetched_at = record.get("fetched_at")
    if not isinstance(fetched_at, str):
        raise ValueError("article retrieval time is missing")
    if datetimes.rfc3339(fetched_at) is None:
        raise ValueError("invalid article retrieval time")
    article: Article = {"fetched_at": fetched_at, "final_url": final_url, "paragraphs": paragraphs}
    title = record.get("title")
    if title is not None:
        if not isinstance(title, str):
            raise ValueError("article title is not text")
        if not trim(title) or byte_len(title) > MAX_TITLE:
            raise ValueError("invalid article title")
        article["title"] = title
    return article


def load(conn: sqlite3.Connection, url: str) -> Article | None:
    """The stored article for `url` with `"cached": True`, or None. Read only:
    suitable for freezing already collected text into a new report."""
    row = conn.execute("SELECT content FROM article_text WHERE url = ?1", (url,)).fetchone()
    if row is None:
        return None
    saved = row[0]
    if byte_len(saved) > MAX_JSON:
        raise ValueError("cached article exceeds size limit")
    return {"cached": True, **validated(jsonfmt.loads(saved))}


def get_or_fetch(conn: sqlite3.Connection, url: str) -> Article:
    """The stored article, else one original read (`fetch.fetch_article`) stored first."""
    return get_or_fetch_with(conn, url, lambda: fetch.fetch_article(url))


def get_or_fetch_with(
    conn: sqlite3.Connection, url: str, read: Callable[[], Any]
) -> Article:
    """`get_or_fetch` with the read supplied; a read failure raises `FetchFailed`
    from it, and an invalid read is never stored."""
    cached = load(conn, url)
    if cached is not None:
        return cached
    try:
        value = read()
    except Exception as error:
        raise FetchFailed() from error
    content = jsonfmt.dumps(validated(value))
    if byte_len(content) > MAX_JSON:
        raise ValueError("article exceeds persistence size limit")
    # Preserve the first successful read if another request completed meanwhile.
    cursor = conn.execute(
        "INSERT INTO article_text (url, content) VALUES (?1, ?2) ON CONFLICT(url) DO NOTHING",
        (url, content),
    )
    inserted = cursor.rowcount
    saved = load(conn, url)
    if saved is None:
        raise ValueError("saved article is missing")
    saved["cached"] = inserted == 0
    return saved
