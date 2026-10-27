"""What the question API and the CLI do with runs, above the stages: ask, rerun,
edit the plan under review, and the read models (a run's summary, its papers, the
CSV export). The HTTP shapes are in docs/question/api.md.

`root` is the service's data directory (the queue), `conn` the owner's workspace
database (the run). Errors the user can act on are `HttpError`.
"""

import csv
import io
import logging
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from isbe.core import jsonfmt
from isbe.core.errors import HttpError, describe
from isbe.question import compose, queue, runs, screen, sets, sources
from isbe.question import papers as records
from isbe.question import plan as plans

log = logging.getLogger(__name__)

STAGES = ("plan", "search", "screen", "expand", "graph")
# The expand stage's run options (`expansion_options`).
_EXPANSION = ("expand", "citations_full", "expand_per_paper", "expand_total")
_ASK = {"question", "lang", "year_from", "year_to", "review", "engine", "request_id", *_EXPANSION}


def ask(root: Path, conn: sqlite3.Connection, owner: str, body: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """Queue a new run for `body` (see docs/question/api.md); its summary and whether
    it is new (false: the request id was seen before)."""
    unknown = sorted(set(body) - _ASK)
    if unknown:
        raise HttpError(400, f"Unknown fields: {', '.join(unknown)}")
    request_id = queue.check_request(body.get("request_id"))
    earlier = queue.requested(root, owner, request_id)
    if earlier is not None:
        return summary(root, conn, earlier), False
    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        raise HttpError(400, "Ask a question")
    if len(question) > plans.MAX_QUESTION:
        raise HttpError(400, f"A question has at most {plans.MAX_QUESTION} characters")
    lang = body.get("lang", "zh")
    if lang not in plans.LANGS:
        raise HttpError(400, "The question language must be zh or en")
    years = [body.get("year_from"), body.get("year_to")]
    for year in years:
        if year is not None and (not isinstance(year, int) or isinstance(year, bool) or not 1900 <= year <= 2100):
            raise HttpError(400, "A year is a whole number between 1900 and 2100")
    if None not in years and years[0] > years[1]:
        raise HttpError(400, "The year range starts after it ends")
    review = body.get("review", True)
    if not isinstance(review, bool):
        raise HttpError(400, "review is true or false")
    expansion = expansion_options(body)
    engine = body.get("engine", compose.DEFAULT)
    if engine not in compose.QUESTION_ENGINES:
        raise HttpError(400, "The search engine is openalex or arxiv")
    if engine not in sources.engines():
        raise HttpError(400, "The arXiv engine searches essaies' arXiv index, and the paper source is the OpenAlex "
                             "API (ISBE_PAPER_SOURCE); search OpenAlex")
    options = {"year_from": years[0], "year_to": years[1], "review": review, "engine": engine, **expansion}
    # The run is created first and queued second, so the worker never meets a task
    # without its run; when the queue refuses (the limit) or the request id turns out
    # taken by a concurrent ask, the run is deleted again.
    run_id = runs.create(conn, " ".join(question.split()), lang)
    try:
        task = queue.add(root, owner, run_id, options, request_id=request_id)
    except BaseException:
        _undo(conn, run_id)
        raise
    if task.run_id != run_id:
        runs.delete(conn, run_id)
        return summary(root, conn, task.run_id), False
    return summary(root, conn, run_id), True


def rerun(
    root: Path, conn: sqlite3.Connection, owner: str, run_id: str, actor: str, body: Mapping[str, Any]
) -> dict[str, Any]:
    """A new run from a copy of `run_id`'s plan (version `body.version`, the latest
    when absent) with `body.changes` applied; the old run is left as it is. Its
    overrides in force carry over to the papers the new run finds."""
    unknown = sorted(set(body) - {"version", "changes", "review"})
    if unknown:
        raise HttpError(400, f"Unknown fields: {', '.join(unknown)}")
    old = runs.get(conn, run_id)
    version = body.get("version")
    if version is not None and (not isinstance(version, int) or isinstance(version, bool)):
        raise HttpError(400, "version is a plan version number")
    changes = body.get("changes") or {}
    if not isinstance(changes, dict):
        raise HttpError(400, "changes is an object of plan fields")
    parent = plans.get(conn, run_id, version)
    carried = [
        {"key": key, "decision": d["decision"], "actor": d["override"]["actor"], "note": d["override"]["note"]}
        for key, d in screen.decisions(conn, run_id).items() if d["source"] == "override"
    ]
    expansion = expansion_options(task.options if (task := queue.find(root, run_id)) else {})
    # As in `ask`: the new run is complete in the workspace before it is queued, and
    # deleted again when the queue refuses it.
    new_id = runs.create(conn, old["question"], old["lang"])
    try:
        plan = plans.adopt(conn, new_id, parent, changes)
        runs.set_status(conn, new_id, "running")
        runs.emit(conn, new_id, [("stage", {"stage": "plan", "state": "begin"}), ("plan", {"plan": plan})])
        options = {
            "year_from": plan.get("year_from"), "year_to": plan.get("year_to"), "review": False,
            "engine": plan.get("engine", compose.OPENALEX.name), "rerun_of": run_id, "overrides": carried,
            **expansion,
        }
        queue.add(root, owner, new_id, options, step="search")
    except BaseException:
        _undo(conn, new_id)
        raise
    return summary(root, conn, new_id)


def edit_plan(root: Path, conn: sqlite3.Connection, run_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """Run edited plan: save `body.changes` to version `body.version` (the latest when
    absent) as a new version and end the review. Only while the plan is under review;
    later, rerun instead."""
    unknown = sorted(set(body) - {"version", "changes"})
    if unknown:
        raise HttpError(400, f"Unknown fields: {', '.join(unknown)}")
    task = queue.get(root, run_id)
    if not task.reviewing:
        raise HttpError(409, "The plan can only be edited under review; rerun with an edited plan instead")
    changes = body.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise HttpError(400, "changes is an object of plan fields")
    version = body.get("version") or plans.get(conn, run_id)["version"]
    plan = plans.edit(conn, run_id, version, changes)
    runs.emit(conn, run_id, [("plan", {"plan": plan})])
    queue.start(root, run_id)
    return plan


def _undo(conn: sqlite3.Connection, run_id: str) -> None:
    """Delete a run the queue did not take. A failure here is logged, not raised, so
    the caller's own error (the 429) is what the client sees; the run is then left
    without a queue row, which can be cancelled and deleted (docs/question/api.md)."""
    try:
        runs.delete(conn, run_id)
    except Exception as error:
        log.error("question run %s: could not delete it after the queue refused it: %s", run_id, describe(error))


def delete(root: Path, conn: sqlite3.Connection, owner: str, run_id: str) -> None:
    """Delete a run that is not working on a step, with all its rows: off the queue
    first, so the worker cannot claim it while its workspace rows go.

    The worker writes the root database before the workspace (flow.Step), so a task
    can read `finished` or `paused` while the worker is still writing the run's last
    events. Until those are written (`done`, or the `paused` status), the run is
    still stopping and the delete waits for it, for `queue.SETTLE` seconds at most:
    a task that has not changed for that long has no writer left (a worker killed
    between its two writes, or a workspace write that failed), and the run goes."""
    run = runs.get(conn, run_id)

    def stopping(task: queue.Task) -> bool:
        behind = (task.state == "finished" and runs.ended(conn, run_id) is None) or (
            task.state == "paused" and run["status"] != "paused"
        )
        return behind and not task.settled

    queue.remove(root, run_id, stopping)
    runs.delete(conn, run_id)
    log.info("question run %s of %s deleted", run_id, owner)


def expansion_options(body: Mapping[str, Any]) -> dict[str, Any]:
    """The expand stage's options from an ask (or an earlier run's options), with
    their defaults: `expand` true or false, the others whole numbers from 0 (none)
    up to their limit: `citations_full` kept papers looked up through OpenAlex,
    `expand_per_paper` and `expand_total` the papers added per included paper and
    per run."""
    from isbe.question import citations, expand  # they read the run's papers here

    out: dict[str, Any] = {"expand": body.get("expand", True)}
    if not isinstance(out["expand"], bool):
        raise HttpError(400, "expand is true or false")
    limits = {
        "citations_full": (citations.FULL_LIMIT, 1000),
        "expand_per_paper": (expand.PER_PAPER, 100),
        "expand_total": (expand.TOTAL, 1000),
    }
    for name, (default, most) in limits.items():
        value = body.get(name, default)
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= most:
            raise HttpError(400, f"{name} is a whole number from 0 to {most}")
        out[name] = value
    return out


# --- reading ---------------------------------------------------------------------------------


def summary(root: Path, conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    """The run header: status, stage, problems, the plan review and the counts."""
    run = runs.get(conn, run_id)
    task = queue.find(root, run_id)
    stages: dict[str, str] = {}
    problems = warnings = 0
    fatal: dict[str, Any] | None = None
    first: dict[str, Any] | None = None  # the first non-fatal error with a code
    for kind, data in conn.execute(
        "SELECT type, data FROM question_events WHERE run_id = ? AND type IN ('stage', 'error') ORDER BY seq",
        (run_id,),
    ):
        event = jsonfmt.loads(data)
        if kind == "stage":
            stages[event["stage"]] = event["state"]
        else:
            problems += 1
            warnings += not event["fatal"]
            if event["fatal"]:
                fatal = event
            elif first is None and event.get("code"):
                first = event
    last = conn.execute("SELECT coalesce(max(seq), 0) FROM question_events WHERE run_id = ?", (run_id,)).fetchone()[0]
    stage = next((s for s in STAGES if stages.get(s) == "begin"), None)
    plan_row = conn.execute(
        "SELECT max(version) FROM question_plans WHERE run_id = ?", (run_id,)
    ).fetchone()
    options = task.options if task else {}
    reviewing = task is not None and task.reviewing
    return {
        "run_id": run_id,
        "question": run["question"],
        "lang": run["lang"],
        "status": run["status"],
        "stage": stage if run["status"] in ("running", "paused") else None,
        "degraded": warnings > 0,
        "problems": problems,
        # Why a failed run failed: its fatal error's stage, code and message.
        "failure": (
            {"stage": fatal["stage"], "code": fatal.get("code"), "message": fatal["message"],
             "detail": fatal.get("detail")}
            if fatal is not None and run["status"] == "failed" else None
        ),
        # What first went wrong in a run that went on and ended partial, the same way.
        "problem": (
            {"stage": first["stage"], "code": first["code"], "message": first["message"],
             "detail": first.get("detail")}
            if first is not None and run["status"] == "done" else None
        ),
        "plan_version": plan_row[0],
        "review": {
            "on": bool(options.get("review", True)),
            "active": reviewing,
            # None while held (the plan is being edited) or when no review is running.
            "ends_at": task.ready_at if reviewing and task else None,
        },
        "options": {k: options.get(k) for k in ("year_from", "year_to", "engine", *_EXPANSION, "rerun_of") if k in options},
        "pausing": bool(task and task.state == "running" and task.control == "pause"),
        "cancelling": bool(task and task.state == "running" and task.control == "cancel"),
        "counts": counts(conn, run_id),
        "last_seq": last,
        "created_at": run["created_at"],
        "updated_at": run["updated_at"],
    }


def listing(root: Path, conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every run of the workspace, newest first, as summaries."""
    ids = [r[0] for r in conn.execute("SELECT run_id FROM question_runs ORDER BY created_at DESC, rowid DESC")]
    return [summary(root, conn, run_id) for run_id in ids]


def candidates_set(conn: sqlite3.Connection, run_id: str) -> str | None:
    """The id of the run's latest candidate set (after dedup), if any."""
    row = conn.execute(
        "SELECT set_id FROM paper_sets WHERE run_id = ? AND kind = 'candidates' AND set_id LIKE '%.candidates%' "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    return row[0] if row else None


def counts(conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
    hits = conn.execute("SELECT count(*) FROM question_hits WHERE run_id = ?", (run_id,)).fetchone()[0]
    set_id = candidates_set(conn, run_id)
    size = 0
    if set_id is not None:
        size = conn.execute(
            "SELECT size FROM paper_sets WHERE run_id = ? AND set_id = ?", (run_id, set_id)
        ).fetchone()[0]
    out = {"hits": hits, "candidates": size, "included": 0, "excluded": 0, "unscreened": 0, "overridden": 0}
    for d in screen.decisions(conn, run_id).values():
        name = {"include": "included", "exclude": "excluded", "unscreened": "unscreened"}.get(d["decision"])
        if name:
            out[name] += 1
        if d["source"] == "override":
            out["overridden"] += 1
    return out


def papers(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    """The run's candidates with their provenance and effective decision. Before the
    search has finished, the papers its hits brought so far."""
    runs.get(conn, run_id)
    set_id = candidates_set(conn, run_id)
    members: list[dict[str, Any]]
    if set_id is not None:
        doc = conn.execute(
            "SELECT doc FROM paper_sets WHERE run_id = ? AND set_id = ?", (run_id, set_id)
        ).fetchone()[0]
        members = sets.load(conn, doc)["members"]
    else:
        found: dict[str, dict[str, Any]] = {}
        for query_id, rank, key, ref in conn.execute(
            "SELECT query_id, rank, key, paper_ref FROM question_hits WHERE run_id = ? ORDER BY rowid", (run_id,)
        ):
            entry = found.setdefault(key, {"paper_ref": ref, "provenance": []})
            entry["provenance"].append({"via": "search", "query_id": query_id, "rank": rank})
        members = records.full(conn, list(found.values()))
    decided = screen.decisions(conn, run_id)
    out = []
    for m in members:
        d = decided.get(m["paper"]["key"])
        out.append({
            **m,
            "decision": d["decision"] if d else None,
            "decided_by": d["source"] if d else None,
            "verdict": d["verdict"] if d else None,
            "override": d["override"] if d else None,
        })
    return out


CSV_COLUMNS = (
    "key", "title", "year", "venue", "authors", "cited_by_count", "doi", "arxiv", "openalex", "url",
    "decision", "decided_by", "reason", "found_by",
)


def export_csv(conn: sqlite3.Connection, run_id: str) -> str:
    """The papers as CSV, overrides applied; unscreened papers count as kept."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for item in papers(conn, run_id):
        p = item["paper"]
        keys = {k.split(":", 1)[0]: k.split(":", 1)[1] for k in reversed(p["keys"])}
        reason = (item["override"] or {}).get("note") if item["decided_by"] == "override" else (
            (item["verdict"] or {}).get("reason"))
        found_by = "; ".join(
            f"{e['query_id']}#{e['rank']}" if e["via"] == "search" else f"{e['via']} {e.get('from_key', '')}"
            for e in item["provenance"]
        )
        writer.writerow([_cell(v) for v in (
            p["key"], p["title"], p.get("year"), p.get("venue"), "; ".join(p.get("authors") or []),
            p.get("cited_by_count"), keys.get("doi"), keys.get("arxiv"), keys.get("openalex"), p.get("url"),
            item["decision"], item["decided_by"], reason, found_by,
        )])
    return out.getvalue()


def _cell(value: Any) -> str:
    """A CSV cell; text a spreadsheet would run as a formula is prefixed with a quote."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text
