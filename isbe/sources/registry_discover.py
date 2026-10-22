"""Same-site discovery: find a site's feeds and listings, trial each, keep them as
candidates.

A candidate already configured for the topic (same URL and type) is an
existing source and keeps its configuration, enabled state included; only a
new source that read at least one entry is enabled, and only when asked.

A discovered source's id is derived from its topic, type and URL. An edit of
those (allowed while it has no material) must not make a later discovery of the
same site insert that id again: the edited row is recognized by its id and kept
as the user left it, an existing source like any other. Nothing is written
until every candidate has been read, then all of it in one transaction.
"""

import sqlite3
from dataclasses import dataclass
from typing import Any

from isbe.core import db, http, jsonfmt
from isbe.core.validators import is_control, trim

from . import reader
from .reader import Candidate, Kind, ReadResult
from .registry_config import Source, insert, message
from .registry_materials import failed, read, record_rows

MAX_CANDIDATES = 8
# The spelling of each kind in the id's hash; stored ids depend on it.
_DEBUG = {Kind.RSS: "Rss", Kind.PAGE: "Page"}


@dataclass
class _Trial:
    source: Source
    is_new: bool
    result: ReadResult | None
    error: Exception | None


def source_id(topic: str, candidate: Candidate) -> str:
    return "src-" + db.sha256(f"{topic}\n{_DEBUG[candidate.kind]}\n{candidate.url}")[:20]


def _resolve(conn: sqlite3.Connection, candidate: Candidate, topic: str) -> tuple[Source, bool]:
    """The configured source this candidate is, or a new disabled one; true if new."""
    row = conn.execute(
        "SELECT config FROM source_definitions WHERE json_extract(config,'$.url')=?1 "
        "AND json_extract(config,'$.kind')=?2 AND json_extract(config,'$.topic')=?3 ORDER BY id LIMIT 1",
        (candidate.url, candidate.kind.value, topic),
    ).fetchone()
    id = source_id(topic, candidate)
    if row is None:
        # An earlier discovery's row for this candidate, edited since: the user's now.
        row = conn.execute("SELECT config FROM source_definitions WHERE id=?1", (id,)).fetchone()
    if row is not None:
        return Source.model_validate(jsonfmt.loads(row[0])), False
    name = "".join(c for c in candidate.name if not is_control(c))[:120]
    source = Source(
        id=id,
        name=name if trim(name) else "Discovered source",
        topic=topic,
        kind=candidate.kind,
        url=candidate.url,
        enabled=False,
        link_pattern="",
        max_items=100,
    ).validated()
    return source, True


def discover(
    conn: sqlite3.Connection,
    seed: str,
    topic: str,
    auto_enable: bool,
    *,
    resolver: http.Resolver | None = None,
) -> dict[str, Any]:
    """Discover up to eight sources on `seed`'s origin for `topic` and trial each once.

    `resolver` replaces the public-only resolver (tests)."""
    candidates = reader.discover(seed, resolver=resolver)
    trials: list[_Trial] = []
    for candidate in candidates[:MAX_CANDIDATES]:
        source, is_new = _resolve(conn, candidate, topic)
        if any(t.source.id == source.id for t in trials):
            continue  # an edited row may now also match another candidate's URL
        try:
            trials.append(_Trial(source, is_new, read(source, resolver), None))
        except Exception as error:
            trials.append(_Trial(source, is_new, None, error))
    enabled = added = updated = unchanged = failures = 0
    outcomes: list[dict[str, Any]] = []
    with db.immediate(conn):
        for trial in trials:
            source = trial.source
            if trial.is_new:
                insert(conn, source, "discovered", seed)
            outcome: dict[str, Any] = {"id": source.id, "name": source.name, "url": source.url}
            if trial.result is None:
                assert trial.error is not None
                failures += 1
                failed(conn, source.id, trial.error)
                state = "candidate" if trial.is_new else "existing"
                outcomes.append(outcome | {"state": state, "entries": 0, "error": message(trial.error)})
                continue
            found = len(trial.result.entries)
            stats = record_rows(conn, source, trial.result)
            added += stats["new"]
            updated += stats["updated"]
            unchanged += stats["unchanged"]
            activate = trial.is_new and auto_enable and found > 0
            if activate:
                active = source.model_copy(update={"enabled": True})
                conn.execute(
                    "UPDATE source_definitions SET config=?2,revision=revision+1 WHERE id=?1",
                    (source.id, active.dumps()),
                )
                enabled += 1
            state = "enabled" if activate else "candidate" if trial.is_new else "existing"
            outcomes.append(outcome | {"state": state, "entries": found, "truncated": stats["truncated"]})
    return {
        "checked": len(trials),
        "enabled": enabled,
        "new": added,
        "updated": updated,
        "unchanged": unchanged,
        "failed": failures,
        "candidates": outcomes,
        "truncated": len(candidates) > MAX_CANDIDATES,
    }
