"""The search stage of a question run: the plan's queries against the paper source → the
run's candidate set. Sole writer of `question_queries` and `question_hits`
(whose paper records go to `question.papers`).

Queries run in priority order, a priority level at a time. Once the run has
`enough` distinct papers, lower levels are skipped; a level that brings too few
leads to the next one. Each query's raw replies and its hits are stored, and its
hits become a query set (`paper_set@1`, kind `query`). A failed query is a
non-fatal `error` event and the others go on; only when every query fails does
the search fail (a fatal error, the run `failed`). When the source is gone
(`sources.Unreachable`: it could not be reached, or failed three requests in a
row) the queries left are not asked: with no query answered that is the fatal
error, with the reason; after some answered, one non-fatal `error` says how
many were not asked, and the run goes on with what it found.

The query sets are joined (`union`) and deduplicated (`dedup`) into the
candidate set; both operations are recorded (`set_op@1`). Every hit is a `hit`
event (`new: false` when the run already had a paper under that key), every fold
a `dedup` event, and the stage is framed by `stage` begin and end events.
The plan's years go to the source with the search; papers outside them that still
come back (an essaies that does not filter) are left out of the hits but stay in
the raw reply. Papers without a year stay in.

A run can be searched again (an edited plan, or the same one); each search is
numbered, and its sets are named after it: `s1.q1`, `s1.union`, `s1.candidates`.

`check` is called before each query; whatever it raises stops the search there
(a pause or a cancel), with every finished query kept. `resume` continues the
run's latest search from where it stopped: its finished queries are not asked
again and its stage `begin` event is not repeated.
"""

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from isbe.core import db, jsonfmt
from isbe.core.errors import HttpError
from isbe.question import papers, problems, runs, sets, sources
from isbe.question import plan as plans

# Distinct papers after which lower-priority queries are skipped: high enough
# that a composed plan's broad tier (priority 2) runs after its narrow one.
ENOUGH = 1500
# Works asked for per query.
PER_QUERY = 100


class SearchFailed(HttpError):
    """Every query of the plan failed; the search has ended the run."""


@dataclass
class Result:
    search: int
    plan_version: int
    candidates: str = ""  # the candidate set's id
    candidates_size: int = 0
    hits: int = 0  # papers the queries returned, repeats included
    merged: int = 0
    out_of_years: int = 0
    queries_run: list[str] = field(default_factory=list)
    queries_failed: list[str] = field(default_factory=list)
    queries_skipped: list[str] = field(default_factory=list)
    query_sets: list[str] = field(default_factory=list)
    ms: int = 0


def run(
    conn: sqlite3.Connection,
    source: sources.Source,
    run_id: str,
    plan_version: int | None = None,
    *,
    enough: int = ENOUGH,
    per_query: int = PER_QUERY,
    clock: Callable[[], float] = time.monotonic,
    check: Callable[[], None] | None = None,
    resume: bool = False,
) -> Result:
    """Search with version `plan_version` of the run's plan (the latest when None)."""
    runs.get(conn, run_id)
    started = clock()
    latest = conn.execute(
        "SELECT max(search), max(plan_version) FROM question_queries WHERE run_id = ?", (run_id,)
    ).fetchone()
    resume = resume and latest[0] is not None
    if resume:
        number, plan_version = latest[0], conn.execute(
            "SELECT plan_version FROM question_queries WHERE run_id = ? AND search = ? LIMIT 1",
            (run_id, latest[0]),
        ).fetchone()[0]
    else:
        number = (latest[0] or 0) + 1
    plan = plans.get(conn, run_id, plan_version)
    s = _Search(conn, source, run_id, plan, number, per_query, clock)
    done = s.restore() if resume else set()
    if not resume:
        runs.emit(conn, run_id, [("stage", {"stage": "search", "state": "begin"})])

    levels: dict[int, list[dict[str, Any]]] = {}
    for query in plan["queries"]:
        levels.setdefault(query["priority"], []).append(query)
    gone: sources.Unreachable | None = None
    for priority in sorted(levels):
        if len(s.seen) >= enough or gone is not None:
            s.result.queries_skipped += [q["id"] for q in levels[priority] if q["id"] not in done]
            continue
        for query in levels[priority]:
            if query["id"] in done:
                continue
            if gone is not None:
                s.result.queries_skipped.append(query["id"])
                continue
            if check is not None:
                check()
            gone = s.query(query)

    result = s.result
    if not result.query_sets:
        last = gone or (s.errors[-1] if s.errors else None)
        # Queries that all failed the same way on essaies' side (5xx, quota) say essaies
        # is the trouble, not the plan.
        codes = {problems.source_code(e) for e in s.errors}
        if gone is not None:
            code, message = problems.source_code(gone), (
                f"{gone}. No search query was answered; {problems.advice(gone)}, then run the question again")
        elif len(codes) == 1 and codes <= {problems.ESSAIES_DOWN, problems.OPENALEX_DOWN, problems.OPENALEX_QUOTA}:
            code, message = codes.pop(), f"Every search query failed on {source.NAME}{"'" if source.NAME.endswith("s") else "'s"} side (the last: {last})"
        else:
            code, message = problems.SEARCH_FAILED, (
                f"Every search query failed (the last: {_last_error(conn, run_id, number)})")
        data = {"stage": "search", "message": message, "fatal": True, "code": code}
        if last is not None:
            data["detail"] = str(last)
        runs.emit(conn, run_id, [("error", data)])
        runs.set_status(conn, run_id, "failed")
        runs.emit(conn, run_id, [("done", {"status": "failed"})])
        raise SearchFailed(502, message)
    if gone is not None:
        runs.emit(conn, run_id, [("error", {
            "stage": "search", "fatal": False, "code": problems.source_code(gone), "detail": str(gone),
            "message": (f"{gone}. {len(result.queries_skipped)} more queries were not asked; the run goes on "
                        f"with the {len(s.seen)} papers found so far"),
        })])

    joined, _, _ = s.store.run(
        "union", result.query_sets, s.store.free_id(f"{s.tag}.union"), "candidates",
        "All query results", {"plan_version": plan["version"]},
    )
    candidates, _, folds = s.store.run(
        "dedup", [joined.set_id], s.store.free_id(f"{s.tag}.candidates"), "candidates",
        "Candidates after deduplication", {"order": list(sources.ORDER)},
    )
    runs.emit(
        conn, run_id,
        [("dedup", {"kept": kept, "merged": merged}) for kept, merged in folds]
        + [("stage", {"stage": "search", "state": "end"})],
    )
    result.candidates = candidates.set_id
    result.candidates_size = len(candidates.members)
    result.merged = len(joined.members) - len(candidates.members)
    result.ms = int((clock() - started) * 1000)
    return result


class _Search:
    def __init__(
        self, conn: sqlite3.Connection, source: sources.Source, run_id: str, plan: dict[str, Any],
        number: int, per_query: int, clock: Callable[[], float],
    ) -> None:
        self.conn = conn
        self.source = source
        self.run_id = run_id
        self.plan = plan
        self.number = number
        self.tag = f"s{number}"
        self.per_query = per_query
        self.clock = clock
        self.store = sets.Store(conn, run_id)
        self.seen: set[str] = set()
        self.errors: list[sources.SourceError] = []  # of the queries that failed, in order
        self.result = Result(search=number, plan_version=plan["version"])

    def restore(self) -> set[str]:
        """Pick up this search where it stopped: the ids of the queries it already asked."""
        done = set()
        for query_id, error in self.conn.execute(
            "SELECT query_id, error FROM question_queries WHERE run_id = ? AND search = ? ORDER BY rowid",
            (self.run_id, self.number),
        ):
            done.add(query_id)
            self.result.queries_run.append(query_id)
            if error is not None:
                self.result.queries_failed.append(query_id)
            elif self.store.has(f"{self.tag}.{query_id}"):
                self.result.query_sets.append(f"{self.tag}.{query_id}")
        for key, in self.conn.execute(
            "SELECT key FROM question_hits WHERE run_id = ? AND search = ?", (self.run_id, self.number)
        ):
            self.seen.add(key)
            self.result.hits += 1
        return done

    def query(self, query: dict[str, Any]) -> sources.Unreachable | None:
        """Run one query: its row, hits, query set and events; the error when essaies
        is gone, so the search stops asking."""
        started = self.clock()
        query_id = query["id"]
        self.result.queries_run.append(query_id)
        try:
            found = self.source.search(
                query["query"], limit=query.get("limit", self.per_query),
                year_from=self.plan.get("year_from"), year_to=self.plan.get("year_to"),
            )
        except sources.SourceError as error:
            with db.immediate(self.conn):
                self._save(query, error.status, str(error), None, 0, started)
            self.result.queries_failed.append(query_id)
            self.errors.append(error)
            runs.emit(self.conn, self.run_id, [("error", {
                "stage": "search", "message": f"Query “{query['query']}” failed: {error}",
                "fatal": False, "query_id": query_id, "code": problems.source_code(error), "detail": str(error),
            })])
            return error if isinstance(error, sources.Unreachable) else None

        members: list[sets.Member] = []
        for rank, work in enumerate(found.works, 1):
            paper = sources.paper(work)
            if paper is None:
                continue
            if not _in_years(paper, self.plan.get("year_from"), self.plan.get("year_to")):
                self.result.out_of_years += 1
                continue
            members.append({"paper": paper, "provenance": [{"via": "search", "query_id": query_id, "rank": rank}]})
        self.result.hits += len(members)
        # A paper the query returned twice is one hit, at its first rank.
        query_set = sets.union([members])
        rows: list[list[Any]] = []
        events = []
        for member in query_set:
            paper = member["paper"]
            rank = member["provenance"][0]["rank"]
            new = paper["key"] not in self.seen
            self.seen.add(paper["key"])
            rows.append([self.run_id, self.number, query_id, rank, paper["key"], int(new), paper])
            events.append(("hit", {"query_id": query_id, "rank": rank, "paper": paper, "new": new}))

        response = jsonfmt.dumps(found.bodies[0] if len(found.bodies) == 1 else found.bodies, sort_keys=False)
        with db.immediate(self.conn):
            self._save(query, found.status, None, response, len(rows), started)
            for row, ref in zip(rows, papers.put(self.conn, self.run_id, [r[-1] for r in rows]), strict=True):
                row[-1] = ref
            self.conn.executemany(
                "INSERT INTO question_hits (run_id, search, query_id, rank, key, new, paper_ref) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        set_id = self.store.free_id(f"{self.tag}.{query_id}")
        self.store.add(sets.PaperSet(set_id, "query", f"{query_id}: {query['query']}", query_set))
        self.result.query_sets.append(set_id)
        runs.emit(self.conn, self.run_id, events)
        return None

    def _save(
        self, query: dict[str, Any], status: int | None, error: str | None, response: str | None,
        hits: int, started: float,
    ) -> None:
        self.conn.execute(
            "INSERT INTO question_queries (run_id, search, query_id, plan_version, query, status, error, "
            "response_z, hits, ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, self.number, query["id"], self.plan["version"], query["query"], status, error,
             papers.pack(response), hits, int((self.clock() - started) * 1000), db.now()),
        )


def response(conn: sqlite3.Connection, run_id: str, search: int, query_id: str) -> str | None:
    """A query's raw essaies reply as it came back, or None (failed, or no such query)."""
    row = conn.execute(
        "SELECT response, response_z FROM question_queries WHERE run_id = ? AND search = ? AND query_id = ?",
        (run_id, search, query_id),
    ).fetchone()
    if row is None:
        return None
    return row[0] if row[1] is None else papers.unpack(row[1])


def _last_error(conn: sqlite3.Connection, run_id: str, number: int) -> str:
    row = conn.execute(
        "SELECT error FROM question_queries WHERE run_id = ? AND search = ? AND error IS NOT NULL "
        "ORDER BY rowid DESC LIMIT 1", (run_id, number),
    ).fetchone()
    return row[0] if row else "no query to ask"


def _in_years(paper: dict[str, Any], year_from: int | None, year_to: int | None) -> bool:
    year = paper.get("year")
    if year is None:
        return True
    return (year_from is None or year >= year_from) and (year_to is None or year <= year_to)
