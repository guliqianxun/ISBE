"""Date rechecks of saved search materials, twenty per job
.

A batch is frozen when the job starts: the plan's current window and the
materials after the cursor, each with its content before the check, so a
retry or a later batch never re-reads a page another batch already did.
"""

import sqlite3
from typing import Any

from isbe.core import db, jsonfmt
from isbe.core.errors import HttpError
from isbe.sources.registry_jobs import job
from isbe.topics.model import TopicError, check_id

from .evidence import merge_dates
from .plans import SearchPlan, count, get
from .runner import Verify, snapshot, verify_date

BATCH = 20
_HEX = frozenset("0123456789abcdefABCDEF")


def validate_recheck(conn: sqlite3.Connection, plan_id: str, after: str | None) -> None:
    """The plan must exist and `after`, if given, be one of its material ids."""
    try:
        check_id(plan_id)
    except TopicError as error:
        raise HttpError(400, str(error)) from None
    get(conn, plan_id)
    if after is not None:
        if len(after) != 64 or not all(c in _HEX for c in after):
            raise HttpError(400, "Invalid date-recheck cursor")
        belongs = conn.execute(
            "SELECT 1 FROM search_materials WHERE id=?1 AND plan_id=?2", (after, plan_id)
        ).fetchone()
        if belongs is None:
            raise HttpError(400, "Date-recheck cursor does not belong to this search plan")


def snapshot_recheck(conn: sqlite3.Connection, run_id: str, plan_id: str, after: str | None) -> None:
    """Freeze job `run_id`'s batch and write its initial stats; inside `registry.start`'s transaction."""
    validate_recheck(conn, plan_id, after)
    plan = SearchPlan.model_validate(get(conn, plan_id)["config"])
    snapshot(conn, run_id, plan)
    rows = conn.execute(
        f"SELECT id,content FROM search_materials WHERE plan_id=?1 AND id>?2 ORDER BY id LIMIT {BATCH}",
        (plan_id, after or ""),
    ).fetchall()
    for id, content in rows:
        conn.execute(
            "INSERT INTO search_date_checks(run_id,material_id,previous_content) VALUES (?1,?2,?3)",
            (run_id, id, content),
        )
    total = conn.execute("SELECT count(*) FROM search_materials WHERE plan_id=?1", (plan_id,)).fetchone()[0]
    last = rows[-1][0] if rows else after or ""
    remaining = conn.execute(
        "SELECT count(*) FROM search_materials WHERE plan_id=?1 AND id>?2", (plan_id, last)
    ).fetchone()[0]
    stats = {
        "plan_id": plan_id,
        "after": after,
        "start_date": plan.start_date,
        "end_date": plan.end_date,
        "total": total,
        "batch_size": len(rows),
        "checked": 0,
        "failed": 0,
        "retained": 0,
        "in_range": 0,
        "out_of_range": 0,
        "unknown": 0,
        "conflict": 0,
        "remaining": remaining,
        "next_cursor": last if remaining > 0 else None,
        "state": "running",
    }
    conn.execute("UPDATE source_jobs SET stats=?2 WHERE id=?1", (run_id, jsonfmt.dumps(stats)))


def recheck(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    return recheck_with(conn, run_id, verify_date)


def recheck_with(conn: sqlite3.Connection, run_id: str, verify: Verify) -> dict[str, Any]:
    """Check the batch's unchecked materials; the job's stats are saved after each one."""
    row = conn.execute("SELECT config FROM search_runs WHERE id=?1", (run_id,)).fetchone()
    if row is None:
        raise LookupError("Query returned no rows")  # rusqlite's message
    plan = SearchPlan.model_validate(jsonfmt.loads(row[0]))
    rows = conn.execute(
        "SELECT c.material_id,c.previous_content,m.url FROM search_date_checks c "
        "JOIN search_materials m ON m.id=c.material_id "
        "WHERE c.run_id=?1 AND c.checked_at IS NULL ORDER BY c.material_id",
        (run_id,),
    ).fetchall()
    stats = job(conn, run_id)["stats"]
    if not isinstance(stats, dict):
        stats = {}  # A job whose stats are still null starts a fresh object
    for id, previous_text, url in rows:
        previous = jsonfmt.loads(previous_text)
        observed = verify(url, plan.start_date, plan.end_date)
        now = db.now()
        content = dict(previous) if isinstance(previous, dict) else {}
        retained = merge_dates(content, previous, observed, plan, run_id, now)
        status = content.get("date_status")
        for key in ("checked", status if isinstance(status, str) else "unknown"):
            stats[key] = count(stats.get(key)) + 1
        if content.get("date_check_status") == "failed":
            stats["failed"] = count(stats.get("failed")) + 1
        if retained:
            stats["retained"] = count(stats.get("retained")) + 1
        with db.immediate(conn):
            conn.execute(
                "UPDATE search_materials SET content=?2 WHERE id=?1", (id, jsonfmt.dumps(content))
            )
            conn.execute(
                "UPDATE search_date_checks SET result=?3,checked_at=?4,retained=?5 "
                "WHERE run_id=?1 AND material_id=?2",
                (run_id, id, jsonfmt.dumps(observed), now, retained),
            )
            conn.execute("UPDATE source_jobs SET stats=?2 WHERE id=?1", (run_id, jsonfmt.dumps(stats)))
    failed = count(stats.get("failed"))
    if failed > 0 and failed == count(stats.get("checked")):
        stats["state"] = "failed"
    elif failed > 0:
        stats["state"] = "partial"
    else:
        stats["state"] = "succeeded"
    return stats
