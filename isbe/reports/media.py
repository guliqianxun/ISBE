"""Immutable, deterministic media-week drafts from saved search candidates.

No model calls and no network. `run` freezes a saved search plan's collected
materials into `reports/<topic>/<period>.sources.json` and renders the week's
draft from that snapshot; `create_briefing` freezes the cited subset of such a
report, with the original text already in the article cache, under a new
period and renders an editor's briefing from it. Reports are write-once: a
period that any report (of any kind) already uses, or whose files exist, is
skipped for the next version (`-v2`, `-v3`, ...); the files are created new
and removed again when the `reports` row is not committed.

Public API:

- `run(conn, data, plan_id, topic, week_end, *, get_plan=None) -> dict`
- `create_briefing(conn, data, topic, source_period, input) -> dict`
- `articles(conn, data, topic, period) -> list[{"id", "url"}]`
- `original(conn, data, topic, period, id) -> dict | None`

User-actionable failures raise `core.errors.HttpError` (400, or the plan
lookup's 404); a report or snapshot that no longer verifies raises
`MediaError`, which the web layer maps to its own status.
"""

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from isbe import articles as article_cache
from isbe.core import db, jsonfmt, validators
from isbe.core.errors import HttpError
from isbe.reports import briefing
from isbe.reports import media_dates as dates
from isbe.reports.media_files import (
    MAX_SNAPSHOT,
    Created,
    MediaError,
    absent,
    identifier,
    no_links,
    os_error,
    read_text,
    snapshot,
    try_exists,
)
from isbe.reports.media_render import Window, get, render, source

__all__ = ["GetPlan", "MediaError", "articles", "create_briefing", "original", "run"]

MAX_INPUT = 1024 * 1024
MAX_VERSIONS = 10_000

# isbe.search.get(conn, id): the saved plan as search's `detail` builds it,
# {"config": {...} | None, "revision": str, "last_checked", "last_status",
# "item_count": int}; raises HttpError(404, "Search plan not found").
type GetPlan = Callable[[sqlite3.Connection, str], dict[str, Any]]


def articles(
    conn: sqlite3.Connection, data: Path, topic: str, period: str
) -> list[dict[str, Any]]:
    """The frozen materials of a media report as `[{"id", "url"}]`; empty for
    any other report or none. Raises like `media_files.snapshot`."""
    frozen = snapshot(conn, data, topic, period)
    if frozen is None:
        return []
    return [{"id": item["id"], "url": item["url"]} for item in frozen["materials"]]


def original(
    conn: sqlite3.Connection, data: Path, topic: str, period: str, id: str
) -> dict[str, Any] | None:
    """The exact original text a briefing's editor used for material `id`.

    That material's frozen `content.original` object, the article-cache record
    `{"cached": True, "fetched_at", "final_url", "paragraphs": [str],
    "title"?}` as it was when the briefing was created; None when the report is
    not a media report, has no such material, or the material carries no
    original (every plain media week). Raises like `media_files.snapshot`.
    """
    frozen = snapshot(conn, data, topic, period)
    if frozen is None:
        return None
    item = next((item for item in frozen["materials"] if item["id"] == id), None)
    value = get(item, "content", "original")
    return value if isinstance(value, dict) else None


def _content(item: dict[str, Any], key: str) -> dict[str, Any]:
    """`item["content"]`, ready for assigning `key`: null becomes `{}`."""
    value = item.get("content")
    if value is None:
        value = item["content"] = {}
    if not isinstance(value, dict):
        # Note: a `content` that is not an object cannot take a key. The job fails
        # with a MediaError; aborting would leave its `runs` row running.
        kind = {str: "string", bool: "boolean", list: "array"}.get(type(value), "number")
        raise MediaError(f'cannot access key "{key}" in JSON {kind}')
    return value


def _cited(editorial: Any) -> set[str]:
    """Every source id the draft cites: its items' `source_ids` and its schedule rows."""
    cited: set[str] = set()
    sections = get(editorial, "sections")
    for section in sections if isinstance(sections, list) else []:
        items = get(section, "items")
        for item in items if isinstance(items, list) else []:
            ids = get(item, "source_ids")
            if isinstance(ids, list):
                cited.update(id for id in ids if isinstance(id, str))
    schedule = get(editorial, "schedule")
    for row in schedule if isinstance(schedule, list) else []:
        id = get(row, "source_id")
        if isinstance(id, str):
            cited.add(id)
    return cited


def _strip_www(host: str) -> str:
    # `trim_start_matches("www.")` removes every leading repetition.
    while host.startswith("www."):
        host = host[4:]
    return host


def _freeze_original(conn: sqlite3.Connection, item: dict[str, Any]) -> None:
    """Attach the cached original text and name the publisher of one cited material."""
    cached = article_cache.load(conn, item["url"])
    if cached is not None:
        title = cached.get("title")
        if isinstance(title, str) and validators.trim(title):
            _content(item, "title")["title"] = title
        _content(item, "original")["original"] = cached
    try:
        url = validators.whatwg(item["url"])
    except ValueError:
        return
    host = url.hostname
    publisher = get(item, "content", "publisher")
    publisher = publisher if isinstance(publisher, str) else ""
    if not publisher or _strip_www(publisher) == _strip_www(host):
        _content(item, "publisher")["publisher"] = source(host)


def _free_period(
    conn: sqlite3.Connection,
    dir: Path,
    topic: str,
    base: str,
    free: Callable[[Path], bool],
    full: str,
) -> tuple[str, Path, Path]:
    """The first version of `base` that no report row (of any kind) holds and
    whose two files are `free`."""
    for version in range(1, MAX_VERSIONS + 1):
        period = base if version == 1 else f"{base}-v{version}"
        path, manifest = dir / f"{period}.md", dir / f"{period}.sources.json"
        taken = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM reports WHERE topic_id=?1 AND period=?2)", (topic, period)
        ).fetchone()[0]
        if not taken and free(path) and free(manifest):
            return period, path, manifest
    raise MediaError(full)


def _insert(conn: sqlite3.Connection, row: tuple[str, str, str, str, str, str]) -> None:
    conn.execute(
        "INSERT INTO reports(id,topic_id,period,kind,path,fingerprint,created_at) "
        "VALUES(?1,?2,?3,'weekly',?4,?5,?6)",
        row,
    )


def _committed[T](conn: sqlite3.Connection, write: Callable[[Created], T]) -> T:
    """`write(created)` inside one `BEGIN IMMEDIATE` transaction; the files it
    created are removed again unless the transaction commits."""
    created = Created()
    try:
        with db.immediate(conn):
            result = write(created)
        created.committed = True
        return result
    finally:
        created.discard()


def create_briefing(
    conn: sqlite3.Connection, data: Path, topic: str, source_period: str, input: Path
) -> dict[str, Any]:
    """Save an editor-prepared factual briefing; source collection remains a separate operation.

    `input` is the editorial JSON (`briefing.render`'s draft, at most 1 MiB).
    The briefing freezes only the parent media report's cited materials, each
    with its cached original text, as `<ISO year>-W<week>-briefing[-vN]`; the
    parent report is left unchanged. Returns `{"topic", "period", "path",
    "url"}` (the stats the CLI prints). Raises `MediaError("Media report not
    found")` without such a report, `BriefingError`/`DecodeError` for a draft
    that does not verify against the frozen originals, and nothing is written.
    """
    text = read_text(input, MAX_INPUT, "Briefing input is too large")
    editorial = jsonfmt.loads(text)
    return _committed(
        conn, lambda created: _briefing(conn, data, topic, source_period, editorial, created)
    )


def _briefing(
    conn: sqlite3.Connection,
    data: Path,
    topic: str,
    parent_period: str,
    editorial: Any,
    created: Created,
) -> dict[str, Any]:
    frozen = snapshot(conn, data, topic, parent_period)
    if frozen is None:
        raise MediaError("Media report not found")
    # The parent retains the full search collection; this issue freezes only its citations.
    cited = _cited(editorial)
    frozen["materials"] = [item for item in frozen["materials"] if item["id"] in cited]
    for item in frozen["materials"]:
        _freeze_original(conn, item)
    week_end = frozen.get("week_end")
    if not isinstance(week_end, str):
        raise MediaError("Missing week ending")
    year, week = dates.iso_week(dates.date(week_end))
    dir = data / "reports" / topic
    no_links(dir)
    # Note: unlike `run`, a dangling link at a candidate name counts as
    # free here (`try_exists` follows links), so `Created.write` then refuses it
    # and that week's briefing cannot be created until the link is removed.
    period, path, manifest_path = _free_period(
        conn, dir, topic, f"{year}-W{week:02}-briefing",
        lambda p: not try_exists(p), "Too many briefing versions",
    )
    frozen["period"] = period
    frozen["parent_period"] = parent_period
    frozen["presentation"] = "industry-briefing"
    frozen["created_at"] = db.now()
    frozen["editorial"] = editorial
    report = briefing.render(editorial, frozen)
    manifest = jsonfmt.dumps_pretty(frozen)
    if len(manifest.encode()) > MAX_SNAPSHOT:
        raise MediaError("Briefing snapshot is too large")
    rel = f"reports/{topic}/{period}.md"
    manifest_rel = f"reports/{topic}/{period}.sources.json"
    fingerprint = {
        "mode": "media-weekly",
        "presentation": "industry-briefing",
        "materials": [item["id"] for item in frozen["materials"]],
        "parent_period": parent_period,
        "snapshot_path": manifest_rel,
        "snapshot_sha256": db.sha256(manifest),
    }
    created.write(manifest_path, manifest.encode())
    created.write(path, report.encode())
    row = (f"{topic}/{period}/weekly", topic, period, rel, jsonfmt.dumps(fingerprint), db.now())
    _insert(conn, row)
    return {
        "topic": topic,
        "period": period,
        "path": rel,
        "url": f"/reports?topic={topic}&period={period}",
    }


def _plan_getter(get_plan: GetPlan | None) -> GetPlan:
    if get_plan is not None:
        return get_plan
    # Imported when needed, so this module does not depend on `search` at import time.
    from isbe.search import get

    found: GetPlan = get
    return found


def run(
    conn: sqlite3.Connection,
    data: Path,
    plan_id: str,
    topic: str,
    week_end: str,
    *,
    get_plan: GetPlan | None = None,
) -> dict[str, Any]:
    """Freeze plan `plan_id`'s collected materials into the media draft of the
    week ending on Sunday `week_end` (YYYY-MM-DD, a completed week in Shanghai).

    The caller holds the shared generation execution lock. This function never
    fetches or calls a model. `get_plan` reads the saved plan (default
    `isbe.search.get`). Returns the stats the CLI prints: the group counts
    (`weekly`, `background`, `newer`, `unknown`, `conflict`, `outside`) plus
    `topic`, `period` (`<ISO year>-W<week>-media[-vN]`), `kind` ("weekly"),
    `path`, `materials` (count) and `url`. Raises `HttpError(400)` for an
    invalid topic or week or a plan without materials, the plan lookup's
    `HttpError(404)`, and `MediaError`/`DecodeError` for an unusable saved window.
    """
    if not identifier(topic):
        raise HttpError(400, "Invalid report topic identifier")
    try:
        end = dates.date(week_end)
    except ValueError:
        raise HttpError(400, "Invalid week ending date") from None
    if dates.weekday(end) != 6:
        raise HttpError(400, "The report week must end on Sunday")
    if end >= dates.today():
        raise HttpError(400, "Choose a completed week")
    start = dates.week_start(end)
    reader = _plan_getter(get_plan)
    return _committed(
        conn,
        lambda created: _weekly(
            conn, data, reader(conn, plan_id), plan_id, topic, week_end, start, end, created
        ),
    )


def _saved_day(plan: Any, key: str, missing: str) -> int:
    value = get(plan, "config", key)
    if not isinstance(value, str):
        raise MediaError(missing)
    return dates.date(value)


def _weekly(
    conn: sqlite3.Connection,
    data: Path,
    plan: dict[str, Any],
    plan_id: str,
    topic: str,
    week_end: str,
    start: int,
    end: int,
    created: Created,
) -> dict[str, Any]:
    collection_start = _saved_day(plan, "start_date", "Missing search start date")
    collection_end = _saved_day(plan, "end_date", "Missing search end date")
    if collection_start > collection_end:
        raise MediaError("Invalid saved search window")
    window = Window(start, end, collection_start, collection_end)
    rows = conn.execute(
        "SELECT id,url,content,first_seen,last_seen FROM search_materials "
        "WHERE plan_id=?1 ORDER BY id",
        (plan_id,),
    ).fetchall()
    materials = [
        {"id": id, "url": url, "content": jsonfmt.loads(content), "first_seen": first,
         "last_seen": last}
        for id, url, content, first, last in rows
    ]
    if not materials:
        raise HttpError(400, "The search plan has no collected materials")
    created_at = db.now()
    year, week = dates.iso_week(end)
    dir = data / "reports" / topic
    no_links(dir)
    try:
        os.makedirs(dir, exist_ok=True)
    except OSError as error:
        raise os_error(error) from None
    no_links(dir)
    period, path, manifest_path = _free_period(
        conn, dir, topic, f"{year}-W{week:02}-media", absent,
        "Too many report versions for this week",
    )
    week_start = dates.show(start)
    frozen = {
        "mode": "media-weekly",
        "topic": topic,
        "period": period,
        "created_at": created_at,
        "week_start": week_start,
        "week_end": week_end,
        "timezone": "Asia/Shanghai",
        "plan": plan,
        "materials": materials,
    }
    manifest = jsonfmt.dumps_pretty(frozen)
    report, counts = render(frozen, window)
    rel = f"reports/{topic}/{period}.md"
    fingerprint = {
        "mode": "media-weekly",
        "materials": [item["id"] for item in materials],
        "counts": counts,
        "plan_id": plan_id,
        "week_start": week_start,
        "week_end": week_end,
        "snapshot_path": f"reports/{topic}/{period}.sources.json",
        "snapshot_sha256": db.sha256(manifest),
    }
    created.write(manifest_path, manifest.encode())
    created.write(path, report.encode())
    row = (f"{topic}/{period}/weekly", topic, period, rel, jsonfmt.dumps(fingerprint), created_at)
    _insert(conn, row)
    return {
        **counts,
        "topic": topic,
        "period": period,
        "kind": "weekly",
        "path": rel,
        "materials": len(materials),
        "url": f"/reports?topic={topic}&period={period}",
    }
