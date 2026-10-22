"""The report library: every report with its cover, the reader view and the
file lookup behind `/api/reports`, `/api/report`, `/api/report.md`, the
overview and report email.

Results are plain dicts and lists that serialize (with `core.jsonfmt`) to the
JSON the console serves.

Two seams keep this module free of the report kinds that live elsewhere:

- `report(..., articles=...)` takes the media briefing's source list as a
  callable with the signature of `media.articles(conn, data, topic, period)`
 . The web layer passes `isbe.reports.media.articles`;
  without it (or when it raises) the list is empty. `research.evidence` / `research.original` are not used here: they
  serve their own routes.
- Topic labels come from `isbe.topics.scan_dir`, so one broken topic file no
  longer drops every label.
"""

import logging
import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from isbe import topics
from isbe.core import jsonfmt
from isbe.core.errors import describe
from isbe.core.markdown import render_markdown

from .library_cover import cover, lines, trim_start

__all__ = [
    "Articles",
    "ReportMissing",
    "cover",
    "download_name",
    "find_report",
    "latest",
    "library",
    "papers_in",
    "recent",
    "report",
    "report_file",
    "rows",
]

log = logging.getLogger(__name__)

type Row = dict[str, Any]
# media.articles(conn, data, topic, period) -> [{"id": ..., "url": ...}]
type Articles = Callable[[sqlite3.Connection, Path, str, str], list[dict[str, Any]]]


class ReportMissing(LookupError):
    """The newest matching report row points at a file that does not exist."""


def rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[Row]:
    """Rows of `sql` as dicts keyed by column name; blobs read as None."""
    cursor = conn.execute(sql, tuple(params))
    names = [d[0] for d in cursor.description or ()]
    return [
        {n: (None if isinstance(v, bytes) else v) for n, v in zip(names, row, strict=True)}
        for row in cursor
    ]


def _text(value: Any) -> str:
    """The string, or "" for anything else."""
    return value if isinstance(value, str) else ""


def _read(data: Path, path: str) -> str:
    """A report file listed in our own table, or "" when it is unreadable."""
    if not path.startswith("reports/") or ".." in path:
        return ""
    try:
        # Bytes, not read_text: newlines stay exactly as written.
        return (data / path).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def papers_in(data: Path, path: str) -> int:
    """Paper reviews in a report file (its `### [id]` headings), for lists of
    reports: imported ones have no fingerprint to count from."""
    return sum(1 for line in lines(_read(data, path)) if trim_start(line).startswith("### ["))


def _fingerprint(value: Any) -> Any:
    """The parsed fingerprint, or None when it is missing or not JSON."""
    if not isinstance(value, str):
        return None
    try:
        return jsonfmt.loads(value)
    except ValueError:
        return None


def _media(fingerprint: Any) -> bool:
    return isinstance(fingerprint, dict) and fingerprint.get("mode") == "media-weekly"


def _materials(fingerprint: Any) -> int:
    materials = fingerprint.get("materials") if isinstance(fingerprint, dict) else None
    return len(materials) if isinstance(materials, list) else 0


def _topics(data: Path) -> list[topics.Topic]:
    try:
        return topics.scan_dir(data / "topics")[0]
    except (topics.TopicError, OSError):
        return []


def library(conn: sqlite3.Connection, data: Path) -> list[Row]:
    """Every report, each topic's newest first, with what its cover shows (`/api/reports`)."""
    labels = {t.id: t.label for t in _topics(data)}
    out = []
    for r in rows(
        conn,
        "SELECT topic_id, period, kind, path, created_at, fingerprint FROM reports "
        "ORDER BY topic_id, created_at DESC, period DESC",
    ):
        c = cover(_read(data, _text(r["path"])))
        topic = _text(r["topic_id"])
        if topic in labels:
            c["label"] = labels[topic]
        if not isinstance(c["label"], str) or not c["label"]:
            c["label"] = topic
        c["show_period"] = _text(r["period"]) not in c["label"]
        c["topic_id"] = r["topic_id"]
        c["period"] = r["period"]
        c["kind"] = r["kind"]
        fingerprint = _fingerprint(r["fingerprint"])
        # A fingerprint that is not JSON has no material count at all (null), unlike `recent`.
        c["materials"] = None if fingerprint is None else _materials(fingerprint)
        c["media"] = _media(fingerprint)
        c["created_at"] = r["created_at"]
        c["imported"] = '"imported_from"' in _text(r["fingerprint"])
        out.append(c)
    return out


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def find_report(
    conn: sqlite3.Connection, data: Path, topic: str, period: str
) -> tuple[Row, str] | None:
    """The report's row and markdown: the given topic and period, else the
    newest report of the topic, else the newest of all.

    None when there is no such row or its path is unsafe (outside `reports/`,
    also after following links). Raises `ReportMissing` when the row's file
    (or the reports directory) does not exist, `OSError` or
    `UnicodeDecodeError` when it cannot be read.
    """
    found = rows(
        conn,
        "SELECT topic_id, period, kind, path, created_at FROM reports "
        "WHERE (?1 = '' OR topic_id = ?1) AND (?2 = '' OR period = ?2) "
        "ORDER BY created_at DESC LIMIT 1",
        (topic, period),
    )
    if not found:
        return None
    meta = found[-1]
    # The path comes from our own table, but stays inside reports/ all the same.
    path = _text(meta["path"])
    if not path.startswith("reports/") or ".." in path or "\\" in path or ":" in path:
        return None
    try:
        root = data.resolve(strict=True)
        reports = (data / "reports").resolve(strict=True)
        file = (data / path).resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise ReportMissing(path) from error
    if not _inside(reports, root) or not _inside(file, reports):
        return None
    return meta, file.read_bytes().decode("utf-8")


def report_file(
    conn: sqlite3.Connection, data: Path, topic: str, period: str
) -> tuple[Row, str] | None:
    """`find_report` for the console: a row whose file is gone is "not found", not a server error."""
    try:
        return find_report(conn, data, topic, period)
    except ReportMissing:
        return None


def download_name(meta: Row) -> str:
    """The `/api/report.md` attachment name; topic ids and periods are ASCII, so it is too."""
    topic = meta["topic_id"] if isinstance(meta.get("topic_id"), str) else "report"
    name = f"{topic}-{_text(meta.get('period'))}.md"
    return "".join(c for c in name if "!" <= c <= "~" and c != '"')


def report(
    conn: sqlite3.Connection,
    data: Path,
    topic: str,
    period: str,
    *,
    articles: Articles | None = None,
) -> Row | None:
    """The reader view (`/api/report`), or None for 404."""
    found = report_file(conn, data, topic, period)
    if found is None:
        return None
    meta, md = found
    topic_id = _text(meta["topic_id"])
    label: str | None = next((t.label for t in _topics(data) if t.id == topic_id), None)
    if label is None:
        label = cover(md)["label"]
    if not label:
        label = topic_id
    issues = [
        {
            "period": r["period"],
            "kind": r["kind"],
            "media": _media(fingerprint := _fingerprint(r["fingerprint"])),
            "materials": _materials(fingerprint),
            "papers": papers_in(data, _text(r["path"])),
        }
        for r in rows(
            conn,
            "SELECT period, kind, created_at, path, fingerprint FROM reports WHERE topic_id = ?1 "
            "ORDER BY created_at DESC, period DESC",
            (topic_id,),
        )
    ]
    return {
        "topic": topic_id,
        "show_period": _text(meta["period"]) not in label,
        "label": label,
        "period": meta["period"],
        "kind": meta["kind"],
        "path": meta["path"],
        "created_at": meta["created_at"],
        "html": render_markdown(md),
        "articles": _articles(articles, conn, data, topic_id, _text(meta["period"])),
        "issues": issues,
        "topics": rows(conn, "SELECT DISTINCT topic_id FROM reports ORDER BY topic_id"),
    }


def _articles(
    articles: Articles | None, conn: sqlite3.Connection, data: Path, topic: str, period: str
) -> list[dict[str, Any]]:
    if articles is None:
        return []
    try:
        return articles(conn, data, topic, period)
    except Exception as error:  # the report still opens without its source list
        log.warning("media articles for %s %s: %s", topic, period, describe(error))
        return []


def recent(conn: sqlite3.Connection, data: Path, limit: int = 6) -> list[Row]:
    """The overview's newest reports: their rows (without the fingerprint) plus
    paper, media and material counts."""
    out = []
    for r in rows(
        conn,
        "SELECT topic_id, period, kind, created_at, path, fingerprint FROM reports "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ):
        fingerprint = _fingerprint(r.pop("fingerprint"))
        r["papers"] = papers_in(data, _text(r["path"]))
        r["media"] = _media(fingerprint)
        r["materials"] = _materials(fingerprint)
        out.append(r)
    return out


def latest(conn: sqlite3.Connection, data: Path, topic: str) -> Row | None:
    """The overview's per-topic `report`: the topic's newest report, or None."""
    found = rows(
        conn,
        "SELECT period, kind, created_at, path FROM reports WHERE topic_id = ?1 "
        "ORDER BY created_at DESC LIMIT 1",
        (topic,),
    )
    if not found:
        return None
    r = found[-1]
    return {
        "period": r["period"],
        "kind": r["kind"],
        "created_at": r["created_at"],
        "papers": papers_in(data, _text(r["path"])),
    }
