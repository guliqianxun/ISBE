"""Serving frozen research evidence: a quotation in its saved text, and the saved original.

Only a bundle registered in this workspace's `reports` table is served, and
only while its manifest still hashes to the fingerprint recorded with the
report and every file still hashes to the manifest. A request for something
unknown answers None; a bundle that changed after it was frozen raises
`ResearchError`, which the web layer reports as an internal error.
"""

import sqlite3
from pathlib import Path
from typing import Any

from isbe.core import jsonfmt
from isbe.reports import research_compat as compat
from isbe.reports.research_files import (
    MAX_MANIFEST,
    MAX_ORIGINAL,
    MAX_TEXT,
    ResearchError,
    child,
    identifier,
    read,
    sha256,
)
from isbe.reports.research_schema import BUNDLE, Bundle


def _registered(
    conn: sqlite3.Connection, data: Path, topic: str, period: str
) -> tuple[Bundle, Path] | None:
    if not identifier(topic) or not identifier(period):
        return None
    row = conn.execute(
        "SELECT fingerprint FROM reports WHERE topic_id=? AND period=? AND kind='research'",
        (topic, period),
    ).fetchone()
    if row is None:
        return None
    fingerprint = jsonfmt.loads(row[0])
    root = child(data, f"research/{topic}/{period}")
    raw = read(child(root, "manifest.json"), MAX_MANIFEST)
    recorded = fingerprint.get("research_manifest_sha256") if isinstance(fingerprint, dict) else None
    if recorded != sha256(raw):
        raise ResearchError("frozen research manifest changed")
    bundle: Bundle = compat.decode(BUNDLE, compat.loads(compat.utf8(raw)), streamed=True)
    if bundle.topic != topic or bundle.period != period:
        raise ResearchError("research identity mismatch")
    return bundle, root


def _boundary(data: bytes, index: int) -> bool:
    """Whether `index` falls between UTF-8 characters, or at the end."""
    return index == len(data) or (0 <= index < len(data) and data[index] & 0xC0 != 0x80)


def evidence(
    conn: sqlite3.Connection, data: Path, topic: str, period: str, id: str
) -> dict[str, Any] | None:
    """The excerpt `id` of the research report `topic`/`period`, for the evidence page.

    Returns None when the report, bundle or excerpt is unknown, otherwise:
    `{"source_id", "title", "publisher", "published_on" (str | None), "url",
    "material_type", "reading_depth", "coverage", "limitations" (list[str]),
    "location", "quote", "text" (the whole saved source text), "start", "end"
    (UTF-8 byte offsets of `quote` in `text`, end exclusive), "sha256" (of the
    original), "text_sha256"}`; serialize with `jsonfmt.dumps` for the stored key
    order. Raises `ResearchError` (or `DecodeError`) when frozen content changed.
    """
    found = _registered(conn, data, topic, period)
    if found is None:
        return None
    bundle, root = found
    for frozen in bundle.sources:
        source = frozen.source
        excerpt = next((e for e in source.excerpts if e.id == id), None)
        if excerpt is None:
            continue
        position = next((p for p in frozen.positions if p.id == id), None)
        if position is None:
            raise ResearchError("missing frozen position")
        raw = read(child(root, source.text_file), MAX_TEXT)
        if sha256(raw) != frozen.text_sha256:
            raise ResearchError("frozen research text changed")
        text = compat.utf8(raw)
        start, end = position.start, position.end
        if not (
            start <= end
            and _boundary(raw, start)
            and _boundary(raw, end)
            and raw[start:end] == excerpt.quote.encode()
        ):
            raise ResearchError("frozen quote position changed")
        return {
            "source_id": source.id,
            "title": source.title,
            "publisher": source.publisher,
            "published_on": source.published_on,
            "url": source.url,
            "material_type": source.material_type,
            "reading_depth": source.reading_depth,
            "coverage": source.coverage,
            "limitations": source.limitations,
            "location": excerpt.location,
            "quote": excerpt.quote,
            "text": text,
            "start": start,
            "end": end,
            "sha256": source.sha256,
            "text_sha256": frozen.text_sha256,
        }
    return None


def original(
    conn: sqlite3.Connection, data: Path, topic: str, period: str, source_id: str
) -> tuple[str, bytes] | None:
    """`(mime, bytes)` of the frozen original of `source_id`, or None when unknown.

    `mime` is `application/pdf`, `text/html; charset=utf-8` or
    `text/plain; charset=utf-8`. Raises `ResearchError` when the file changed.
    """
    found = _registered(conn, data, topic, period)
    if found is None:
        return None
    bundle, root = found
    frozen = next((s for s in bundle.sources if s.source.id == source_id), None)
    if frozen is None:
        return None
    raw = read(child(root, frozen.source.original_file), MAX_ORIGINAL)
    if sha256(raw) != frozen.source.sha256:
        raise ResearchError("frozen research original changed")
    return frozen.mime, raw
