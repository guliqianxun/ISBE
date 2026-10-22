"""Materials grouped by article identity, and one article's timeline.

Nothing is merged in storage: every material keeps its id, its search hits and
its versions, so a repeated or overlapping discovery never hides a record.
These reads group, across search plans and fixed sources, the materials whose
addresses share an `identity.identity` key, and count hits, materials and
versions apart: several hits of one article are not several sources.
"""

import sqlite3
from collections import defaultdict
from typing import Any

from isbe.core.errors import HttpError

from .identity import Identity, identity
from .registry_config import loads

_ALL = "SELECT id,url FROM source_materials UNION ALL SELECT id,url FROM search_materials"
_SEARCH = "SELECT id,'search:'||plan_id,url,first_seen,last_seen FROM search_materials WHERE id=?1"
_SOURCE = """SELECT m.id,m.source_id,m.url,m.first_seen,m.last_seen,d.config FROM source_materials m
        LEFT JOIN source_definitions d ON d.id=m.source_id WHERE m.id=?1"""
_HITS = """SELECT s.created_at,s.plan_id,s.config,r.query,h.rank,h.run_id,h.content FROM search_hits h
        JOIN search_requests r ON r.run_id=h.run_id AND r.query_index=h.query_index
        JOIN search_runs s ON s.id=h.run_id WHERE h.material_id=?1"""
_VERSIONS = "SELECT collected_at,hash,content FROM source_material_versions WHERE material_id=?1"


def groups(conn: sqlite3.Connection) -> dict[Identity, list[str]]:
    """Every material id, by the identity of its address."""
    found: dict[Identity, list[str]] = defaultdict(list)
    for id, url in conn.execute(_ALL):
        found[identity(url)].append(id)
    return found


def _field(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _search_events(conn: sqlite3.Connection, row: sqlite3.Row) -> list[dict[str, Any]]:
    id, source_id, url, first_seen, _ = row
    events = [
        {
            "kind": "search_hit",
            "at": created_at,
            "material_id": id,
            "url": url,
            "title": _field(loads(content), "title"),
            "plan_id": plan_id,
            "plan_name": _field(loads(config), "name"),
            "query": query,
            "rank": rank,
            "run_id": run_id,
        }
        for created_at, plan_id, config, query, rank, run_id, content in conn.execute(_HITS, (id,))
    ]
    # Every recorded search material has hits; one without is still shown, when first seen.
    plan_id = source_id.removeprefix("search:")
    return events or [
        {"kind": "search_material", "at": first_seen, "material_id": id, "url": url, "plan_id": plan_id}
    ]


def _source_events(conn: sqlite3.Connection, row: sqlite3.Row) -> list[dict[str, Any]]:
    id, source_id, url, _, _, config = row
    return [
        {
            "kind": "source_version",
            "at": collected_at,
            "material_id": id,
            "url": url,
            "title": _field(loads(content), "title"),
            "source_id": source_id,
            "source_name": _field(loads(config), "name"),
            "hash": hash,
        }
        for collected_at, hash, content in conn.execute(_VERSIONS, (id,))
    ]


def timeline(conn: sqlite3.Connection, id: str) -> dict[str, Any]:
    """Material `id` with every material sharing its identity: each search hit and
    each fixed-source version as an event, newest first; 404 if `id` is unknown."""
    row = conn.execute(
        "SELECT url FROM source_materials WHERE id=?1 UNION ALL SELECT url FROM search_materials WHERE id=?1",
        (id,),
    ).fetchone()
    if row is None:
        raise HttpError(404, "Material not found")
    key = identity(row[0])
    materials: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    versions = 0
    for member in sorted(set(groups(conn)[key])):
        if (found := conn.execute(_SEARCH, (member,)).fetchone()) is not None:
            added = _search_events(conn, found)
            versions += 1
        elif (found := conn.execute(_SOURCE, (member,)).fetchone()) is not None:
            added = _source_events(conn, found)
            versions += max(1, len(added))
        else:
            continue
        materials.append(dict(zip(("id", "source_id", "url", "first_seen", "last_seen"), found[:5], strict=True)))
        events += added
    materials.sort(key=lambda m: (m["last_seen"] or "", m["id"]), reverse=True)
    events.sort(key=lambda e: (e["material_id"], e.get("run_id") or "", e.get("rank") or 0, e.get("hash") or ""))
    events.sort(key=lambda e: e["at"] or "", reverse=True)
    return {
        "identity": key.to_json(),
        "materials": materials,
        "events": events,
        "counts": {
            "hits": sum(e["kind"] == "search_hit" for e in events),
            "materials": len(materials),
            "versions": versions,
        },
    }
