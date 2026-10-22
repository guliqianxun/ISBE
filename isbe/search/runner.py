"""One run of a saved search plan.

Every query's raw provider response and every hit are kept; materials are
deduplicated per plan and URL. At most twenty distinct URLs per run have
their original page read for a publication date; the rest stay unchecked.
Each query is sent as `query.compile` writes it; the constraints the provider
does not enforce are recorded per query and for the run.
"""

import sqlite3
from collections.abc import Callable
from typing import Any

from isbe import articles
from isbe.core import db, jsonfmt
from isbe.sources.registry_config import message

from . import capabilities, providers
from . import query as query_syntax
from .evidence import merge_dates
from .plans import SearchPlan
from .results import parse

MAX_CHECKS = 20

type Fetch = Callable[[SearchPlan, str], bytes]
type Verify = Callable[[str, str, str], dict[str, Any]]


def snapshot(conn: sqlite3.Connection, id: str, plan: SearchPlan) -> None:
    """Record the plan run `id` executes; the run reads only this copy, so later edits
    cannot change a run in progress. Called inside `registry.start`'s transaction."""
    conn.execute(
        "INSERT INTO search_runs(id,plan_id,config,created_at) VALUES (?1,?2,?3,?4)",
        (id, plan.id, plan.dumps(), db.now()),
    )


def verify_date(url: str, start: str, end: str) -> dict[str, Any]:
    """The original page's date check, plus whether the page could be read."""
    check = articles.verify(url, start, end)
    value = check.to_json()
    value["read_status"] = "failed" if check.date_error is not None else "read"
    return value


def record(
    conn: sqlite3.Connection,
    run_id: str,
    plan: SearchPlan,
    index: int,
    query: str,
    hits: list[dict[str, Any]],
) -> int:
    """Store one query's hits; returns how many materials are new to the plan."""
    added = 0
    with db.immediate(conn):
        now = db.now()
        for hit in hits:
            url: str = hit["url"]
            id = db.sha256(f"search:{plan.id}\n{url}")
            content = dict(hit)
            content.update(
                kind="web_search",
                provider=plan.provider.value,
                query=query,
                plan_id=plan.id,
                source_name=plan.name,
                topic=plan.topic,
                start_date=plan.start_date,
                end_date=plan.end_date,
                run_id=run_id,
            )
            observed = dict(content)
            row = conn.execute("SELECT content FROM search_materials WHERE id=?1", (id,)).fetchone()
            previous = None if row is None else jsonfmt.loads(row[0])
            merge_dates(content, previous, observed, plan, run_id, now)
            text = jsonfmt.dumps(content)
            added += conn.execute(
                "INSERT OR IGNORE INTO search_materials(id,plan_id,url,content,first_seen,last_seen) "
                "VALUES (?1,?2,?3,?4,?5,?5)",
                (id, plan.id, url, text, now),
            ).rowcount
            conn.execute(
                "UPDATE search_materials SET content=?2,last_seen=?3 WHERE id=?1", (id, text, now)
            )
            conn.execute(
                "INSERT INTO search_hits(run_id,query_index,rank,material_id,content) VALUES (?1,?2,?3,?4,?5)",
                (run_id, index, hit["rank"], id, jsonfmt.dumps(observed)),
            )
    return added


def run(conn: sqlite3.Connection, id: str, plan: SearchPlan) -> dict[str, Any]:
    """Run the snapshotted `plan` as job `id`; the plan is marked failed if the run raises."""
    try:
        return run_with(conn, id, plan, providers.request, verify_date)
    except Exception:
        conn.execute(
            "UPDATE search_plans SET last_checked=?2,last_status='failed' WHERE id=?1",
            (plan.id, db.now()),
        )
        raise


def run_with(
    conn: sqlite3.Connection, id: str, plan: SearchPlan, fetch: Fetch, verify: Verify
) -> dict[str, Any]:
    checked: dict[str, Any] = {}
    unchecked: set[str] = set()
    outcomes: list[dict[str, Any]] = []
    added = found = failed = rejected = truncated = partial = 0
    limited = False
    described = capabilities.describe(plan.provider.value)
    unexecuted: set[str] = set()
    for index, query in enumerate(plan.queries):
        conn.execute(
            "INSERT INTO search_requests(run_id,query_index,query,state) VALUES (?1,?2,?3,'running')",
            (id, index, query),
        )
        # search_requests keeps the line as written; the outcome records what was sent.
        sent: dict[str, Any] = {}
        try:
            sent["sent"] = query_syntax.compile(query, plan.region, plan.institution)
            sent["constraints"] = capabilities.route(
                described, query_syntax.constraints(query, plan.region, plan.institution)
            )
            unexecuted.update(sent["constraints"]["deferred"])
            raw = fetch(plan, sent["sent"])
            conn.execute(
                "UPDATE search_requests SET raw=?3 WHERE run_id=?1 AND query_index=?2", (id, index, raw)
            )
            parsed = parse(plan, raw)
        except Exception as error:
            failed += 1
            text = message(error)[:500]
            conn.execute(
                "UPDATE search_requests SET state='failed',error=?3 WHERE run_id=?1 AND query_index=?2",
                (id, index, text),
            )
            outcomes.append({"query": query, "state": "failed", "error": text, **sent})
            continue
        limited |= parsed.limited
        if parsed.unresponsive_engines > 0:
            partial += 1
        for hit in parsed.hits:
            url: str = hit["url"]
            if url not in checked and len(checked) < MAX_CHECKS:
                checked[url] = verify(url, plan.start_date, plan.end_date)
            fields = checked.get(url)
            if isinstance(fields, dict):
                hit.update(fields)
            else:
                unchecked.add(url)
        added += record(conn, id, plan, index, query, parsed.hits)
        found += len(parsed.hits)
        rejected += parsed.rejected
        truncated += parsed.omitted
        state = "partial" if parsed.unresponsive_engines > 0 else "succeeded"
        conn.execute(
            "UPDATE search_requests SET state=?3 WHERE run_id=?1 AND query_index=?2", (id, index, state)
        )
        outcomes.append(
            {
                "query": query,
                "state": state,
                "found": len(parsed.hits),
                "rejected": parsed.rejected,
                "truncated": parsed.omitted,
                "limited": parsed.limited,
                "page": 1,
                "unresponsive_engines": parsed.unresponsive_engines,
                **sent,
            }
        )
    state = (
        "failed"
        if failed == len(plan.queries)
        else "partial"
        if failed > 0 or partial > 0
        else "succeeded"
    )
    conn.execute(
        "UPDATE search_plans SET last_checked=?2,last_status=?3 WHERE id=?1", (plan.id, db.now(), state)
    )
    hint = providers.current_time_range(plan)
    if hint is not None:
        date_filter = "provider_coarse_hint_and_original_date"
    else:
        date_filter = "original_date_only"
    return {
        "plan_id": plan.id,
        "state": state,
        "queries": outcomes,
        "found": found,
        "new": added,
        "failed": failed,
        "partial": partial,
        "rejected": rejected,
        "truncated": truncated,
        "limited": limited,
        "page": 1,
        "checked": len(checked),
        "unchecked": len(unchecked),
        "provider_time_range": hint,
        "date_filter": date_filter,
        "unexecuted": [name for name in capabilities.CONSTRAINTS if name in unexecuted],
    }
