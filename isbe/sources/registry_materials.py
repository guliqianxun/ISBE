"""Collected leads: versioned materials per source and the combined material list
.

Every fetch body is kept by hash; a material keeps every distinct version of
its entry, so a changed title or summary never overwrites what was seen. The
list marks materials that share an article identity (`registry_timeline`).
"""

import dataclasses
import hashlib
import sqlite3
from typing import Any

from isbe.core import db, http, jsonfmt
from isbe.core.errors import HttpError
from isbe.core.validators import byte_len

from . import reader
from .identity import Identity, identity
from .reader import ReadResult
from .registry_config import Source, get, loads, message
from .registry_timeline import groups

I64_MAX = (1 << 63) - 1
DATE_STATUSES = ("", "in_range", "out_of_range", "unknown", "conflict")


def material(source: Source, entry: reader.Entry) -> str:
    """The stored version of an entry: its fields plus the source kind, keys sorted."""
    return jsonfmt.dumps(dataclasses.asdict(entry) | {"kind": source.kind.value})


def record_rows(conn: sqlite3.Connection, source: Source, result: ReadResult) -> dict[str, Any]:
    """Store one successful read; the caller holds the transaction."""
    now = db.now()
    fetch_hash = hashlib.sha256(result.raw).hexdigest()
    conn.execute(
        "INSERT OR IGNORE INTO source_fetches(source_id,hash,response_url,content_type,body,collected_at) "
        "VALUES (?1,?2,?3,?4,?5,?6)",
        (source.id, fetch_hash, result.response_url, result.content_type, result.raw, now),
    )
    added = updated = unchanged = 0
    for entry in result.entries:
        id = db.sha256(f"{source.id}\n{entry.url}")
        content = material(source, entry)
        hash = db.sha256(content)
        old = conn.execute("SELECT current_hash FROM source_materials WHERE id=?1", (id,)).fetchone()
        if old is None:
            added += 1
        elif old[0] == hash:
            unchanged += 1
        else:
            updated += 1
        conn.execute(
            "INSERT INTO source_materials(id,source_id,url,current_hash,first_seen,last_seen) "
            "VALUES (?1,?2,?3,?4,?5,?5) ON CONFLICT(id) DO UPDATE SET "
            "current_hash=excluded.current_hash,last_seen=excluded.last_seen",
            (id, source.id, entry.url, hash, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO source_material_versions(material_id,hash,content,fetch_hash,collected_at) "
            "VALUES (?1,?2,?3,?4,?5)",
            (id, hash, content, fetch_hash, now),
        )
    conn.execute(
        "UPDATE source_definitions SET last_checked=?2,last_status=?3,last_error=NULL WHERE id=?1",
        (source.id, now, "ok" if result.entries else "empty"),
    )
    return {
        "source_id": source.id,
        "found": len(result.entries),
        "new": added,
        "updated": updated,
        "unchanged": unchanged,
        "truncated": result.truncated,
    }


def record(conn: sqlite3.Connection, source: Source, result: ReadResult) -> dict[str, Any]:
    with db.immediate(conn):
        return record_rows(conn, source, result)


def failed(conn: sqlite3.Connection, id: str, error: BaseException) -> None:
    conn.execute(
        "UPDATE source_definitions SET last_checked=?2,last_status='failed',last_error=?3 WHERE id=?1",
        (id, db.now(), message(error)[:500]),
    )


def read(source: Source, resolver: http.Resolver | None = None) -> ReadResult:
    return reader.read(source.url, source.kind, source.link_pattern, source.max_items, resolver=resolver)


def collect(
    conn: sqlite3.Connection, id: str, *, resolver: http.Resolver | None = None
) -> dict[str, Any]:
    """Read source `id` once and record what it lists; a failure is recorded, then raised.

    `resolver` replaces the public-only resolver (tests)."""
    source = Source.model_validate(get(conn, id)["config"])
    try:
        result = read(source, resolver)
    except Exception as error:
        failed(conn, id, error)
        raise
    return record(conn, source, result)


_MATERIALS = """WITH material_index AS (
        SELECT id,source_id,content,first_seen,last_seen,url FROM source_materials m
        JOIN source_material_versions v ON v.material_id=m.id AND v.hash=m.current_hash
        UNION ALL
        SELECT id,'search:'||plan_id,content,first_seen,last_seen,url FROM search_materials
    ), filtered AS (
        SELECT * FROM material_index WHERE (?1='' OR source_id=?1)
        AND (?2='' OR coalesce(json_extract(content,'$.date_status'),'unknown')=?2)
    )"""
_PAGE = f"""{_MATERIALS}
        SELECT m.id,m.source_id,d.config,m.url,m.first_seen,m.last_seen,m.content,
        (SELECT count(*) FROM source_material_versions WHERE material_id=m.id),
        (SELECT json_group_array(query) FROM (SELECT DISTINCT r.query FROM search_hits h JOIN search_requests r ON r.run_id=h.run_id AND r.query_index=h.query_index WHERE h.material_id=m.id ORDER BY r.query)),
        (SELECT count(*) FROM search_hits WHERE material_id=m.id)
        FROM (SELECT * FROM filtered ORDER BY first_seen DESC,id LIMIT 50 OFFSET ?3) m
        LEFT JOIN source_definitions d ON d.id=m.source_id ORDER BY m.first_seen DESC,m.id"""
_COUNTS = f"""{_MATERIALS}
        SELECT f.url,(SELECT count(*) FROM search_hits WHERE material_id=f.id),
        (SELECT count(*) FROM source_material_versions WHERE material_id=f.id) FROM filtered f"""


def _item(row: sqlite3.Row) -> dict[str, Any]:
    config = loads(row[2])
    item = loads(row[6])
    if not isinstance(item, dict):
        item = {}
    item["id"] = row[0]
    item["source_id"] = row[1]
    if config is not None:
        fields = config if isinstance(config, dict) else {}
        item["source_name"] = fields.get("name")
        item["topic"] = fields.get("topic")
    item["url"] = row[3]
    item["first_seen"] = row[4]
    item["last_seen"] = row[5]
    item["version_count"] = max(row[7], 1)
    if item.get("kind") == "web_search":
        queries = loads(row[8])
        item["search_queries"] = [] if queries is None else queries
        item["hit_count"] = row[9]
    return item


def materials_filtered(
    conn: sqlite3.Connection, source: str, offset: int, date_status: str
) -> dict[str, Any]:
    """Fifty materials, newest first, of one source (`""`: all; `search:<plan>` for a
    plan's web results) and date status, with the total count.

    Each item carries its article `identity` and how many other materials, in any
    plan or source, share it (`duplicates`); `counts` gives the filter's search
    hits, materials, stored versions and distinct articles, which differ."""
    if byte_len(source) > 160 or not 0 <= offset <= I64_MAX or date_status not in DATE_STATUSES:
        raise HttpError(400, "Invalid material filter")
    total = conn.execute(f"{_MATERIALS} SELECT count(*) FROM filtered", (source, date_status)).fetchone()[0]
    items = [_item(r) for r in conn.execute(_PAGE, (source, date_status, offset))]
    sizes = {key: len(ids) for key, ids in groups(conn).items()}
    for item in items:
        key = identity(item["url"])
        item["identity"] = key.to_json()
        item["duplicates"] = sizes.get(key, 1) - 1
    hits = versions = 0
    articles: set[Identity] = set()
    for url, hit_count, version_count in conn.execute(_COUNTS, (source, date_status)):
        hits += hit_count
        versions += max(version_count, 1)
        articles.add(identity(url))
    counts = {"hits": hits, "materials": total, "versions": versions, "articles": len(articles)}
    return {"items": items, "total": total, "offset": offset, "limit": 50, "counts": counts}
