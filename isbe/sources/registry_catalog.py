"""The source catalog: paged search with global facets, export, import and the
enable/disable batch.
"""

import sqlite3
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from isbe.core import db, jsonfmt
from isbe.core.errors import HttpError
from isbe.core.validators import byte_len, is_control

from .registry_config import DETAIL_COLUMNS, Source, checked_id, detail, idle, insert

PAGE = 50
I64_MAX = (1 << 63) - 1
STATUSES = ("", "enabled", "disabled", "failed", "empty", "never")

# Canonical HTTP(S) URLs always contain a slash after the authority.
CATALOG = """WITH source_index AS (
    SELECT id,json_extract(config,'$.name') AS name,json_extract(config,'$.url') AS url,
        json_extract(config,'$.topic') AS topic,json_extract(config,'$.enabled') AS enabled,
        last_status,last_checked,
        substr(json_extract(config,'$.url'),instr(json_extract(config,'$.url'),'://')+3) AS authority
    FROM source_definitions
), catalog AS (
    SELECT *,substr(authority,1,instr(authority,'/')-1) AS site FROM source_index
)"""
FILTER = """(?1='' OR instr(lower(id),lower(?1))>0 OR instr(lower(name),lower(?1))>0 OR instr(lower(url),lower(?1))>0 OR instr(lower(topic),lower(?1))>0)
    AND (?2='' OR site=?2) AND (?3='' OR topic=?3)
    AND (?4='' OR (?4='enabled' AND enabled=1) OR (?4='disabled' AND enabled=0)
        OR (?4='failed' AND last_status='failed') OR (?4='empty' AND last_status='empty')
        OR (?4='never' AND last_checked IS NULL))"""


def export(conn: sqlite3.Connection) -> dict[str, Any]:
    """`{"sources": [config, ...]}` by id: the input `import_` accepts."""
    rows = conn.execute("SELECT config FROM source_definitions ORDER BY id")
    return {"sources": [jsonfmt.loads(r[0]) for r in rows]}


def import_sources(conn: sqlite3.Connection, sources: list[Source]) -> dict[str, Any]:
    """Add 1–100 manual sources atomically; any existing or repeated id adds none."""
    if not sources or len(sources) > 100:
        raise HttpError(400, "Import between 1 and 100 sources")
    sources = [s.validated() for s in sources]
    with db.immediate(conn):
        idle(conn)
        ids: set[str] = set()
        for source in sources:
            exists = conn.execute("SELECT 1 FROM source_definitions WHERE id=?1", (source.id,)).fetchone()
            if source.id in ids or exists:
                raise HttpError(409, f"Source ID {source.id} already exists; no sources were imported")
            ids.add(source.id)
            insert(conn, source, "manual", None)
    return {"imported": len(sources)}


@dataclass(frozen=True)
class CatalogQuery:
    q: str = ""
    site: str = ""
    topic: str = ""
    status: str = ""
    offset: int = 0


_EVERYTHING = CatalogQuery()


def catalog(conn: sqlite3.Connection, query: CatalogQuery = _EVERYTHING) -> dict[str, Any]:
    """Fifty sources from `offset` matching the filter, the total match count, and the
    summary and site/topic facets of the whole registry."""
    texts = (query.q, query.site, query.topic)
    if (
        len(query.q) > 200
        or byte_len(query.site) > 320
        or len(query.topic) > 120
        or any(is_control(c) for text in texts for c in text)
        or query.status not in STATUSES
        or not 0 <= query.offset <= I64_MAX
    ):
        raise HttpError(400, "Invalid source filter")
    parameters = (query.q, query.site, query.topic, query.status)
    total = conn.execute(f"{CATALOG} SELECT count(*) FROM catalog WHERE {FILTER}", parameters).fetchone()[0]
    page = f"""{CATALOG}, page AS MATERIALIZED (
        SELECT id FROM catalog WHERE {FILTER} ORDER BY id LIMIT {PAGE} OFFSET ?5
    ) SELECT {DETAIL_COLUMNS} FROM page JOIN source_definitions d ON d.id=page.id ORDER BY d.id"""
    sources = [detail(r) for r in conn.execute(page, (*parameters, query.offset))]
    row = conn.execute(
        f"""{CATALOG} SELECT count(*),
        coalesce(sum(enabled=1),0),coalesce(sum(enabled=0),0),
        coalesce(sum(last_status='failed'),0),coalesce(sum(last_status='empty'),0),
        coalesce(sum(last_checked IS NULL),0) FROM catalog"""
    ).fetchone()
    summary = dict(zip(("total", "enabled", "disabled", "failed", "empty", "never"), row, strict=True))
    facets: dict[str, Any] = {}
    for column, key in (("site", "sites"), ("topic", "topics")):
        facets[key] = [
            {"value": r[0], "count": r[1]}
            for r in conn.execute(
                f"{CATALOG} SELECT {column},count(*) FROM catalog GROUP BY {column} "
                f"ORDER BY count(*) DESC,{column}"
            )
        ]
    return {
        "sources": sources,
        "total": total,
        "offset": query.offset,
        "limit": PAGE,
        "summary": summary,
        "facets": facets,
    }


class SourceRevision(BaseModel):
    """One selected source of a batch, at the revision the user saw."""

    model_config = ConfigDict(extra="forbid")

    id: str
    revision: str


def batch(conn: sqlite3.Connection, sources: list[SourceRevision], enabled: bool) -> dict[str, Any]:
    """Enable or disable 1–50 sources atomically; one stale or missing source changes none."""
    if not sources or len(sources) > 50:
        raise HttpError(400, "Select between 1 and 50 sources")
    ids: set[str] = set()
    for source in sources:
        checked_id(source.id)
        if source.id in ids:
            raise HttpError(400, "Duplicate source ID; no sources were changed")
        ids.add(source.id)
    with db.immediate(conn):
        idle(conn)
        for source in sources:
            row = conn.execute(
                "SELECT revision FROM source_definitions WHERE id=?1", (source.id,)
            ).fetchone()
            if row is None:
                raise HttpError(404, "Source not found; no sources were changed")
            if str(row[0]) != source.revision:
                raise HttpError(
                    409, "A selected source changed; reload before saving. No sources were changed."
                )
            conn.execute(
                "UPDATE source_definitions SET config=json_set(config,'$.enabled',json(?2)),"
                "revision=revision+1 WHERE id=?1",
                (source.id, "true" if enabled else "false"),
            )
    return {"updated": len(sources)}
