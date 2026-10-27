"""The console's read-only views over a workspace database: the overview, the
triage list, the run ledger and one model call.

Topic files are read with `topics.scan_dir`, so one broken file no longer
fails the overview and triage pages with a 500; the broken file is logged and
its topic left out.
"""

import logging
import re
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from isbe import memory, topics
from isbe.core import db, jsonfmt, settings
from isbe.reports.library import latest, recent, rows

log = logging.getLogger(__name__)

_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _rfc3339(moment: datetime) -> str:
    """RFC 3339 in UTC to the second, with a `Z` suffix."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _since(t: topics.Topic, now: datetime) -> str:
    return _rfc3339(t.since(now))


def week_ago(now: datetime) -> str:
    return _rfc3339(now - timedelta(days=7))


def _topics(data: Path) -> list[topics.Topic]:
    found, errors = topics.scan_dir(data / "topics")
    for message in errors:
        log.warning("topic skipped: %s", message)
    return found


def _count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def _loads(text: Any, default: str) -> Any:
    """`text` (or `default` when it is not a string) parsed as JSON; None if it does not parse."""
    try:
        return jsonfmt.loads(text if isinstance(text, str) else default)
    except ValueError:
        return None


def _topic_stats(
    conn: sqlite3.Connection, data: Path, t: topics.Topic, now: datetime, pending: list[str]
) -> dict[str, Any]:
    since = _since(t, now)
    verdicts = dict(
        conn.execute(
            "SELECT t.verdict, count(*) FROM triage t JOIN items i ON i.id = t.item_id "
            "WHERE t.topic_id = ?1 AND i.kind != 'repo' AND i.published_at >= ?2 GROUP BY t.verdict",
            (t.id, since),
        ).fetchall()
    )
    collected = _count(
        conn,
        "SELECT count(*) FROM topic_items ti JOIN items i ON i.id = ti.item_id "
        "WHERE ti.topic_id = ?1 AND i.kind != 'repo' AND i.published_at >= ?2",
        (t.id, since),
    )
    fulltext = _count(
        conn,
        "SELECT count(*) FROM triage t JOIN items i ON i.id = t.item_id "
        "JOIN fulltext f ON f.item_id = t.item_id "
        "WHERE t.topic_id = ?1 AND t.verdict = 'kept' AND i.published_at >= ?2",
        (t.id, since),
    )
    # Items per day over the last seven, oldest first.
    first_day = now.astimezone(UTC).date() - timedelta(days=6)
    daily = [0] * 7
    for d in rows(
        conn,
        "SELECT substr(i.published_at, 1, 10) AS day, count(*) AS n "
        "FROM topic_items ti JOIN items i ON i.id = ti.item_id "
        "WHERE ti.topic_id = ?1 AND i.kind != 'repo' AND i.published_at >= ?2 GROUP BY day",
        (t.id, first_day.isoformat()),
    ):
        day = d["day"]
        if not (isinstance(day, str) and _DAY.fullmatch(day)):
            continue
        try:
            i = (date.fromisoformat(day) - first_day).days
        except ValueError:
            continue
        if 0 <= i < 7:
            daily[i] = d["n"] if isinstance(d["n"], int) else 0
    last_run = rows(
        conn,
        "SELECT job, status, started_at, finished_at, error FROM runs WHERE topic_id = ?1 "
        "ORDER BY started_at DESC LIMIT 1",
        (t.id,),
    )
    return {
        "id": t.id,
        "label": t.label,
        "cadence": t.cadence.value,
        "flavor": t.flavor().value,
        "active": t.active,
        "since": since,
        "collected": collected,
        "gate_dropped": verdicts.get("gate_dropped", 0),
        "judge_dropped": verdicts.get("judge_dropped", 0),
        "kept": verdicts.get("kept", 0),
        "fulltext": fulltext,
        "daily": daily,
        "report": latest(conn, data, t.id),
        "last_run": last_run[-1] if last_run else None,
        "drafts": sum(1 for p in pending if t.id in p),
    }


def overview(conn: sqlite3.Connection, data: Path, settings_root: Path) -> dict[str, Any]:
    """`/api/overview` for workspace `data`.

    Service status comes from the settings of `settings_root`, the service
    root: a workspace has no settings of its own (reading them there would show a
    second admin or `?owner=` every service as not configured).
    """
    now = datetime.now(UTC)
    pending = memory.pending(data / "memory")
    out = [_topic_stats(conn, data, t, now, pending) for t in _topics(data)]
    llm = rows(
        conn,
        "SELECT count(*) AS calls, coalesce(sum(tokens_in), 0) AS tokens_in, "
        "coalesce(sum(tokens_out), 0) AS tokens_out FROM llm_calls WHERE created_at >= ?1",
        (week_ago(now),),
    )
    snapshot = settings.snapshot(settings_root)
    values = snapshot["values"]
    provider = values.get("ISBE_LLM_PROVIDER")
    provider = provider if isinstance(provider, str) else "anthropic"
    return {
        "now": db.now(),
        "topics": out,
        "pending": pending,
        "llm": llm[-1] if llm else None,
        "reports": recent(conn, data, 6),
        "runs": rows(
            conn,
            "SELECT job, topic_id, status, started_at, finished_at, stats, error "
            "FROM runs ORDER BY started_at DESC LIMIT 8",
        ),
        "services": {
            "papers": _papers(values),
            "metrail": bool(values.get("METRAIL_API_URL")),
            "llm": provider if snapshot["llm"]["ready"] is True else None,
            "llm_status": snapshot["llm"],
        },
    }


def _papers(values: dict[str, str]) -> dict[str, Any]:
    """The paper source of the settings and whether it is ready: the OpenAlex API
    needs nothing set up, essaies its URL."""
    source = values.get("ISBE_PAPER_SOURCE") or "openalex"
    return {"source": source, "ready": source == "openalex" or bool(values.get("ESSAIES_URL"))}


def home(conn: sqlite3.Connection, data: Path, settings_root: Path, limit: int = 3) -> dict[str, Any]:
    """`/api/home`: what the React Home shows besides the galaxies, without the
    overview's per-topic statistics: the newest `limit` report covers, topic
    labels for them, and whether the services a first run needs are set up."""
    snapshot = settings.snapshot(settings_root)
    values = snapshot["values"]
    provider = values.get("ISBE_LLM_PROVIDER")
    return {
        "topics": [{"id": t.id, "label": t.label} for t in _topics(data)],
        "reports": recent(conn, data, limit),
        "services": {
            "papers": _papers(values),
            "llm": (provider if isinstance(provider, str) else "anthropic") if snapshot["llm"]["ready"] is True else None,
        },
    }


def triage(conn: sqlite3.Connection, data: Path, topic: str) -> dict[str, Any] | None:
    """`/api/triage`: the window's items of `topic` (default: the first active
    topic, else the first), or None when there is no such topic."""
    found = _topics(data)
    if topic:
        t = next((t for t in found if t.id == topic), None)
    else:
        t = next((t for t in found if t.active), found[0] if found else None)
    if t is None:
        return None
    since = _since(t, datetime.now(UTC))
    items = rows(
        conn,
        "SELECT i.id, i.title, i.url, i.source, i.published_at, i.extra, "
        "t.verdict, t.tier, t.reason, t.judged_at, t.contract_sha, "
        "f.item_id IS NOT NULL AS fulltext "
        "FROM topic_items ti JOIN items i ON i.id = ti.item_id "
        "LEFT JOIN triage t ON t.topic_id = ti.topic_id AND t.item_id = i.id "
        "LEFT JOIN fulltext f ON f.item_id = i.id "
        "WHERE ti.topic_id = ?1 AND i.kind != 'repo' AND i.published_at >= ?2 "
        "ORDER BY CASE t.verdict WHEN 'kept' THEN 0 WHEN 'judge_dropped' THEN 1 "
        "WHEN 'gate_dropped' THEN 2 ELSE 3 END, "
        "t.tier = 'secondary', i.published_at DESC",
        (t.id, since),
    )
    for r in items:
        # Categories are all the list needs from `extra`.
        extra = _loads(r.pop("extra"), "{}")
        r["categories"] = extra.get("categories") if isinstance(extra, dict) else None
    return {
        "topic": t.id,
        "label": t.label,
        "since": since,
        "topics": [x.id for x in found],
        "items": items,
    }


def runs(conn: sqlite3.Connection) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "runs": rows(
            conn,
            "SELECT id, job, topic_id, status, started_at, finished_at, stats, error "
            "FROM runs ORDER BY started_at DESC LIMIT 200",
        ),
        "purposes": rows(
            conn,
            "SELECT purpose, count(*) AS calls, "
            "coalesce(sum(tokens_in), 0) + coalesce(sum(tokens_out), 0) AS tokens "
            "FROM llm_calls WHERE created_at >= ?1 GROUP BY purpose ORDER BY tokens DESC",
            (week_ago(now),),
        ),
        "calls": rows(
            conn,
            "SELECT c.id, c.purpose, c.model, c.tokens_in, c.tokens_out, c.ms, "
            "c.created_at, substr(c.prompt_sha, 1, 7) AS sha, r.topic_id "
            "FROM llm_calls c LEFT JOIN runs r ON r.id = c.run_id "
            "ORDER BY c.created_at DESC LIMIT 30",
        ),
    }


def call(conn: sqlite3.Connection, id: str) -> dict[str, Any] | None:
    found = rows(
        conn,
        "SELECT id, purpose, model, prompt, response, tokens_in, tokens_out, ms, created_at "
        "FROM llm_calls WHERE id = ?1",
        (id,),
    )
    if not found:
        return None
    c = found[-1]
    # The prompt is stored as JSON {system, user, max_tokens}.
    c["prompt"] = _loads(c["prompt"], "{}")
    return c
