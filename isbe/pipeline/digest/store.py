"""The report file and its `reports` row, written together.

Every report kind lives at `reports/<topic>/<period>.md`, so a digest or an
imported report could land on a file another kind owns, such as a write-once
research report. The research and media writers refuse a
period that any report already uses; the digest and the importer may replace
only their own report of that kind, so they refuse a path that a report of
another kind holds, or a file there that no report row explains.
"""

import os
import sqlite3
from pathlib import Path

from isbe.core import db, paths

_UPSERT = """
INSERT INTO reports (id, topic_id, period, kind, path, fingerprint, created_at)
VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
ON CONFLICT (topic_id, period, kind) DO UPDATE SET
  path = excluded.path, fingerprint = excluded.fingerprint, created_at = excluded.created_at
"""


class ReportTaken(Exception):
    """The report's path belongs to another report; nothing was written."""


def relative(topic: str, period: str) -> str:
    return f"reports/{topic}/{period}.md"


def check_free(conn: sqlite3.Connection, data: Path, topic: str, period: str, kind: str) -> str:
    """The report's path relative to `data`, if a `kind` report of `period` may be
    written there; raises `ReportTaken` otherwise."""
    rel = relative(topic, period)
    rows = conn.execute(
        "SELECT id, topic_id, period, kind FROM reports "
        "WHERE (topic_id = ?1 AND period = ?2) OR path = ?3 ORDER BY id",
        (topic, period, rel),
    ).fetchall()
    for row in rows:
        if (row["topic_id"], row["period"], row["kind"]) != (topic, period, kind):
            raise ReportTaken(
                f"{rel} belongs to the {row['kind']} report {row['id']}; refusing to overwrite it"
            )
    if not rows and os.path.lexists(data / rel):
        raise ReportTaken(f"{rel} exists but no report owns it; refusing to overwrite it")
    return rel


def save(
    conn: sqlite3.Connection,
    data: Path,
    *,
    report_id: str,
    topic: str,
    period: str,
    kind: str,
    body: str,
    fingerprint: str,
    created_at: str,
) -> str:
    """Check the path, upsert the row and replace the file atomically, in one
    write transaction: a refused or failed write leaves both as they were.
    Returns the path relative to `data`."""
    with db.immediate(conn):
        rel = check_free(conn, data, topic, period, kind)
        conn.execute(_UPSERT, (report_id, topic, period, kind, rel, fingerprint, created_at))
        path = data / rel
        for directory in (data / "reports", path.parent):
            paths.check_path(directory)
            directory.mkdir(parents=True, exist_ok=True)
        # 0644: the usual mode for a report under the default umask.
        paths.atomic_write(path, body, mode=0o644)
    return rel
