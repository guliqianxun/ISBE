"""Collected items and the `items` / `topic_items` tables.

Collection is the sole writer of both tables. Rows hold JSON in a fixed
layout: `authors` a compact array, `extra` an object with sorted keys.
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from isbe.core import db, http, jsonfmt, validators


class CollectError(Exception):
    """A source that failed; the message is shown in the run's error."""


@dataclass
class Item:
    id: str  # `arxiv:<id>` | `rss:<url>` | `gh:<owner/name>`
    kind: str  # paper | article | repo
    source: str
    title: str
    summary: str | None
    url: str
    authors: list[str]
    lang: str
    published_at: str
    extra: dict[str, Any] = field(default_factory=dict)


def split_whitespace(value: str) -> list[str]:
    """Words between runs of Unicode White_Space
    (which differs from `str.split()` on a few control characters)."""
    words: list[str] = []
    current: list[str] = []
    for c in value:
        if validators.is_space(c):
            if current:
                words.append("".join(current))
                current = []
        else:
            current.append(c)
    if current:
        words.append("".join(current))
    return words


def squash(value: str) -> str:
    """Collapse runs of whitespace (arXiv titles carry line breaks)."""
    return " ".join(split_whitespace(value))


def checked(response: http.Response) -> http.Response:
    """A 4xx or 5xx answer is an error (`http status: 404`)."""
    if response.status >= 400:
        raise CollectError(f"http status: {response.status}")
    return response


def store(conn: sqlite3.Connection, topic: str, items: list[Item]) -> int:
    """Upsert `items` and link them to `topic`; returns how many are new to the topic."""
    seen = db.now()
    new = 0
    with db.immediate(conn):
        for item in items:
            # published_at keeps its first value: an undated RSS entry is stamped
            # "now" and must not look fresh again on every fetch.
            conn.execute(
                "INSERT INTO items (id, kind, source, title, summary, url, authors, lang, "
                "published_at, extra) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET kind = excluded.kind, source = excluded.source, "
                "title = excluded.title, summary = excluded.summary, url = excluded.url, "
                "authors = excluded.authors, lang = excluded.lang, extra = excluded.extra",
                (
                    item.id, item.kind, item.source, item.title, item.summary, item.url,
                    jsonfmt.dumps(item.authors), item.lang, item.published_at,
                    jsonfmt.dumps(item.extra),
                ),
            )
            new += conn.execute(
                "INSERT OR IGNORE INTO topic_items (topic_id, item_id, first_seen) VALUES (?, ?, ?)",
                (topic, item.id, seen),
            ).rowcount
    return new
