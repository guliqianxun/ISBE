"""What the report reads from the database: kept papers with the full text,
card and figure `enrich` stored, tracked repos and the triage funnel. Plain
SQL over the tables, so this module needs nothing from the stages that write them.
"""

import sqlite3
from typing import Any

from isbe.core import jsonfmt
from isbe.topics import Lang, Topic

from .model import Card, Paper, Repo, get

_PAPERS = """
SELECT i.id, i.title, coalesce(i.summary, ''), i.url, coalesce(i.authors, '[]'), i.extra,
       i.published_at, t.tier, f.body, c.card, f.figure
FROM triage t JOIN items i ON i.id = t.item_id
LEFT JOIN fulltext f ON f.item_id = i.id
LEFT JOIN cards c ON c.item_id = i.id AND c.lang = ?3
WHERE t.topic_id = ?1 AND t.verdict = 'kept' AND i.kind = 'paper' AND i.published_at >= ?2
ORDER BY t.tier = 'secondary', i.published_at DESC, i.id
"""


def papers(conn: sqlite3.Connection, topic: Topic, since: str) -> list[Paper]:
    """Kept papers in the window: core before secondary, newest first."""
    lang = "zh" if topic.lang is Lang.ZH else "en"
    out: list[Paper] = []
    for row in conn.execute(_PAPERS, (topic.id, since, lang)):
        id, title, summary, url, authors, extra, published, tier, body, card, figure = tuple(row)
        authors = jsonfmt.loads(authors)
        if not isinstance(authors, list) or not all(isinstance(a, str) for a in authors):
            raise ValueError(f"{id}: authors must be a JSON array of strings")
        categories = get(jsonfmt.loads(extra), "categories")
        if not isinstance(categories, list) or not all(isinstance(c, str) for c in categories):
            categories = []
        out.append(
            Paper(
                arxiv_id=id.removeprefix("arxiv:"),
                title=title,
                summary=summary,
                url=url,
                authors=authors,
                categories=categories,
                date=published[:10],
                secondary=tier == "secondary",
                fulltext=body,
                card=None if card is None else Card.from_json(card),
                figure=figure_panels(jsonfmt.loads(figure if figure is not None else "{}")),
            )
        )
    return out


_IMAGE_TYPES = frozenset({"png", "jpg", "jpeg", "webp"})
_NAME = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _local_figure(file: str) -> bool:
    # Match the console's local figure route; paths also enter Markdown directly.
    if not file.startswith("figures/"):
        return False
    name = file[len("figures/") :]
    return (
        bool(name)
        and not name.startswith(".")
        and all(c in _NAME for c in name)
        and name.rsplit(".", 1)[-1] in _IMAGE_TYPES
    )


def figure_panels(figure: Any) -> tuple[list[str], str] | None:
    """`fulltext.figure`: a `files` group or a legacy single `file`, local images only."""
    group = get(figure, "files")
    if isinstance(group, list):
        files = [f for f in group if isinstance(f, str)]
    else:
        single = get(figure, "file")
        files = [single] if isinstance(single, str) else []
    files = [f for f in files if _local_figure(f)]
    if not files:
        return None
    caption = get(figure, "caption")
    return files, caption if isinstance(caption, str) else ""


def _i64(value: Any) -> int | None:
    """An integer (not a float or bool) that fits in 64 signed bits, else None."""
    if isinstance(value, int) and not isinstance(value, bool) and -(1 << 63) <= value < 1 << 63:
        return value
    return None


def repos(conn: sqlite3.Connection, topic: Topic, since: str) -> list[Repo]:
    """The topic's configured repos, as last collected; never-collected ones are skipped."""
    out: list[Repo] = []
    for repo in topic.github.repos if topic.github is not None else []:
        row = conn.execute(
            "SELECT title, url, extra FROM items WHERE id = ?1", (f"gh:{repo}",)
        ).fetchone()
        if row is None:
            continue
        name, url, extra = tuple(row)
        extra = jsonfmt.loads(extra)
        pushed = get(extra, "pushed_at")
        pushed = pushed if isinstance(pushed, str) else None
        out.append(
            Repo(
                name=name,
                url=url,
                stars=_i64(get(extra, "stars")),
                pushed=None if pushed is None else pushed[:10],
                active=pushed is not None and pushed >= since,
            )
        )
    return out


def funnel(conn: sqlite3.Connection, topic: str, since: str) -> dict[str, int]:
    """Triage verdict counts over the window's items."""
    rows = conn.execute(
        "SELECT t.verdict, count(*) FROM triage t JOIN items i ON i.id = t.item_id "
        "WHERE t.topic_id = ?1 AND i.kind != 'repo' AND i.published_at >= ?2 GROUP BY t.verdict",
        (topic, since),
    )
    return dict(rows)
