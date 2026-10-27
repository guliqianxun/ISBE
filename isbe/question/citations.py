"""The citations between a question run's candidates, from the paper source (essaies,
or the OpenAlex API: `openalex`, whose batch lookup is OpenAlex's reference lists
rather than an archive): the first part of
the expand stage (#50), and what the galaxy map draws its strong-gravity edges and
main routes from. Sole writer of `question_citations`.

Every candidate is looked up in two passes; a paper already looked up in a scope is
not asked again, so a paused or re-run stage picks up where it stopped:

1. `archive`: essaies' batch lookup (`Essaies.lookup_many`), answered from its own
   archive with no OpenAlex call: what each paper cites and is cited by among the
   archive's papers. A thousand candidates take under a minute. A batch that fails
   whole (after its retries) means essaies is down: the stage stops there, skips
   the full pass and says so in one non-fatal `error`.
2. `all`: a full lookup (`scope=all`) of each kept candidate (not excluded), most
   cited first, at most `FULL_LIMIT`: its references and up to `LIMIT` citing works,
   through OpenAlex. OpenAlex holds few references of arXiv preprints, so the citing
   works are what connect most of them. It needs essaies' `full_references`; an
   essaies without it gets a non-fatal `error` asking to upgrade it, and the run keeps
   the archive's citations. Lookups go `WORKERS` at a time. OpenAlex out of quota
   (429/503 after the retries), essaies unreachable, or `STOP_AFTER` other failures
   in a row end the pass with one non-fatal `error` (each failed paper has its own
   too); the papers left are looked up when the stage runs again. `full_limit` 0
   (the run option `citations_full`) skips the pass.

A `citations` event (`edges`, `looked_up`, `scope`) follows each batch of lookups, so
a page can fetch the edges again as they grow.

Edges are not stored: `edges` reads them from the lookups, keeping those whose two
ends are both candidates (matched on any identity key), oriented citing → cited.
"""

import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from isbe.core import db, jsonfmt
from isbe.question import problems, runs, service, sources

SCOPES = ("archive", "all")
# Kept candidates looked up through OpenAlex per run, most cited first.
FULL_LIMIT = 300
# References and citing works asked of each full lookup (essaies' cap per direction).
LIMIT = 100
# Full lookups between two `citations` events.
EVERY = 25
# Full lookups asked at once: each waits ~3 s on OpenAlex.
WORKERS = 4
# Failed full lookups in a row (other than the quota) that end the pass: essaies or
# OpenAlex is down, and every further lookup would wait out its retries too.
STOP_AFTER = 3
_QUOTA = (429, 503)
UPGRADE = (
    "essaies has no full references (capability full_references, essaies API 0.5); "
    "upgrade essaies. The citations come from its archive only."
)


@dataclass
class Result:
    looked_up: int = 0  # lookups made by this call, both scopes
    edges: int = 0
    failed: int = 0  # papers whose lookup failed
    quota: bool = False  # OpenAlex ran out part way through the full pass
    down: bool = False  # a whole archive batch failed: essaies is not answering
    errors: list[str] = field(default_factory=list)
    emit: bool = True  # False: no events (a backfill of a finished run)


def lookup_id(paper: dict[str, Any], kinds: tuple[str, ...] = ("arxiv", "doi")) -> str | None:
    """What the source looks a paper up by (`Source.LOOKUP_KINDS`): for essaies its
    arXiv id, else its DOI; the OpenAlex API takes its work id first."""
    keys = paper.get("keys") or [paper["key"]]
    for kind in kinds:
        for key in keys:
            if key.startswith(kind + ":"):
                return key.partition(":")[2]
    return None


def run(
    conn: sqlite3.Connection,
    source: sources.Source,
    run_id: str,
    *,
    check: Callable[[], None] = lambda: None,
    full_limit: int = FULL_LIMIT,
    events: bool = True,
) -> Result:
    """Look up the run's candidates in both scopes (the flow frames this with the
    expand stage's events). `check` runs before each batch or lookup and raises to
    stop. Without `events` nothing is added to the run's stream (`isbe
    question-citations` on a finished run); the problems are in `Result.errors`."""
    out = Result(emit=events)
    members = service.papers(conn, run_id)
    _archive(conn, source, run_id, members, check, out)
    if full_limit <= 0 or out.down:
        return out
    try:
        full = "full_references" in source.features()
    except sources.SourceError as error:
        _error(conn, run_id, out, f"{source.NAME} did not say what it can do ({error}), so the full lookups were skipped; "
               "they are looked up when the stage runs again", code=problems.source_code(error))
        return out
    if full:
        _full(conn, source, run_id, members, check, out, full_limit)
    else:
        _error(conn, run_id, out, UPGRADE, code=problems.ESSAIES_OLD)
    return out


def _archive(
    conn: sqlite3.Connection,
    source: sources.Source,
    run_id: str,
    members: list[dict[str, Any]],
    check: Callable[[], None],
    out: Result,
) -> None:
    done = _done(conn, run_id, "archive")
    todo = {
        ident: m["paper"]["key"]
        for m in members
        if m["paper"]["key"] not in done and (ident := lookup_id(m["paper"], source.LOOKUP_KINDS))
    }
    ids = list(todo)
    for start in range(0, len(ids), sources.BATCH_SIZE):
        check()
        chunk = ids[start : start + sources.BATCH_SIZE]
        found, missing, failed = source.lookup_many(chunk)
        rows = [(todo[i], found[i]) for i in chunk if i in found] + [(todo[i], None) for i in missing]
        _save(conn, run_id, "archive", rows)
        out.looked_up += len(rows)
        if failed:
            out.failed += len(failed)
            why = next(iter(failed.values()))
            if len(failed) < len(chunk):
                _error(conn, run_id, out, f"Citation lookup failed for {len(failed)} papers: {why}",
                       code=problems.CITATIONS_FAILED)
        _progress(conn, run_id, "archive", out)
        if len(failed) == len(chunk):
            # A whole batch failed after its retries: essaies is down, and every
            # further batch and full lookup would wait out its retries too.
            out.down = True
            failure = source.last_error
            left = len(ids) - start - len(chunk)
            _error(conn, run_id, out, (
                f"{source.NAME} is not answering citation lookups ({why}), so {len(chunk) + left} papers were not "
                "looked up and the full lookups were skipped; they are looked up when the stage runs again"),
                code=problems.source_code(failure) if failure is not None else (
                    problems.ESSAIES_DOWN if source.NAME == "essaies" else problems.OPENALEX_DOWN))
            return


def _full(
    conn: sqlite3.Connection,
    source: sources.Source,
    run_id: str,
    members: list[dict[str, Any]],
    check: Callable[[], None],
    out: Result,
    limit: int,
) -> None:
    kept = [m["paper"] for m in members if m.get("decision") != "exclude" and lookup_id(m["paper"], source.LOOKUP_KINDS)]
    kept.sort(key=lambda p: -(p.get("cited_by_count") or 0))
    cap = getattr(source, "full_cap", None)
    kept = kept[: limit if cap is None else min(limit, cap)]
    done = _done(conn, run_id, "all")
    todo = [p for p in kept if p["key"] not in done]
    fetched, failures, since = len(kept) - len(todo), 0, 0
    stop: str | None = None
    stop_code = problems.CITATIONS_FAILED

    def ask(paper: dict[str, Any]) -> tuple[dict[str, Any] | None, sources.SourceError | None]:
        try:
            return source.lookup(lookup_id(paper, source.LOOKUP_KINDS) or "", scope="all", limit=LIMIT), None
        except sources.SourceError as error:
            return None, error

    with ThreadPoolExecutor(WORKERS) as pool:
        for start in range(0, len(todo), WORKERS):
            check()
            chunk = todo[start : start + WORKERS]
            for paper, (record, error) in zip(chunk, pool.map(ask, chunk), strict=True):
                if error is None:
                    _save(conn, run_id, "all", [(paper["key"], record)])
                    out.looked_up += 1
                    fetched += 1
                    failures = 0
                    since += 1
                elif error.status in _QUOTA:
                    out.quota = True
                    if stop is None:
                        stop, stop_code = "OpenAlex is out of quota or rate-limited", problems.OPENALEX_QUOTA
                else:
                    out.failed += 1
                    failures += 1
                    _error(conn, run_id, out, f"Full citation lookup failed: {error}", key=paper["key"],
                           code=problems.source_code(error))
                    if stop is None and (error.status is None or isinstance(error, sources.Unreachable)):
                        stop, stop_code = str(error), problems.source_code(error)
                    elif stop is None and failures >= STOP_AFTER:
                        stop = f"{source.NAME} failed {failures} full citation lookups in a row ({error})"
                        stop_code = problems.source_code(error)
            if since >= EVERY:
                _progress(conn, run_id, "all", out)
                since = 0
            if stop:
                _error(conn, run_id, out, (
                    f"{stop}: full citations fetched for {fetched} of {len(kept)} kept papers; "
                    "the rest are looked up when the stage runs again"), code=stop_code)
                break
    if todo:
        _progress(conn, run_id, "all", out)


def _done(conn: sqlite3.Connection, run_id: str, scope: str) -> set[str]:
    rows = conn.execute("SELECT key FROM question_citations WHERE run_id = ? AND scope = ?", (run_id, scope))
    return {r[0] for r in rows}


def _save(
    conn: sqlite3.Connection, run_id: str, scope: str, rows: list[tuple[str, dict[str, Any] | None]]
) -> None:
    at = db.now()
    with db.immediate(conn):
        for key, record in rows:
            conn.execute(
                "INSERT OR IGNORE INTO question_citations (run_id, key, scope, found, cites, cited_by, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, key, scope, int(record is not None), jsonfmt.dumps(_neighbours(record, "cites")),
                 jsonfmt.dumps(_neighbours(record, "cited_by")), at),
            )


# What a stored neighbour keeps of its paper record: enough to add it as a candidate.
_KEPT = ("key", "keys", "title", "year", "authors", "venue", "type", "cited_by_count", "url", "in_archive")


def _neighbours(record: dict[str, Any] | None, field: str) -> list[dict[str, Any]]:
    """The works of `record[field]` as paper records (`sources.paper`, without abstract
    and keywords, which lookups do not carry), or just their `keys` when essaies gave
    no title; a work without an identity is left out."""
    out = []
    for work in (record or {}).get(field) or []:
        if not isinstance(work, dict) or not (keys := sources.identity_keys(work)):
            continue
        p = sources.paper(work)
        out.append({k: p[k] for k in _KEPT if k in p} if p else {"keys": keys})
    return out


def _progress(conn: sqlite3.Connection, run_id: str, scope: str, out: Result) -> None:
    out.edges = len(edges(conn, run_id)["citations"])
    _emit(conn, run_id, out, "citations", {"scope": scope, "looked_up": out.looked_up, "edges": out.edges})


def progress(conn: sqlite3.Connection, run_id: str, scope: str, looked_up: int) -> None:
    """A `citations` event with the edges known now (after the expansion added papers)."""
    _progress(conn, run_id, scope, Result(looked_up=looked_up))


def _error(
    conn: sqlite3.Connection, run_id: str, out: Result, message: str, key: str | None = None,
    *, code: str = problems.CITATIONS_FAILED,
) -> None:
    out.errors.append(message if key is None else f"{key}: {message}")
    data: dict[str, Any] = {"stage": "expand", "message": message, "fatal": False, "code": code}
    if key is not None:
        data["key"] = key
    _emit(conn, run_id, out, "error", data)


def _emit(conn: sqlite3.Connection, run_id: str, out: Result, kind: str, data: dict[str, Any]) -> None:
    if out.emit:
        runs.emit(conn, run_id, [(kind, data)])


# --- reading ---------------------------------------------------------------------------


def edges(
    conn: sqlite3.Connection, run_id: str, members: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """The run's citation edges between candidates as `[citing key, cited key]` pairs
    (the papers' preferred keys, as `/papers` gives them), sorted, and when the last
    lookup was saved (None before any). `members` is `service.papers` when the
    caller has it already."""
    index: dict[str, str] = {}
    for m in service.papers(conn, run_id) if members is None else members:
        for key in m["paper"]["keys"]:
            index.setdefault(key, m["paper"]["key"])
    found: set[tuple[str, str]] = set()
    last = None
    for key, cites, cited_by, at in conn.execute(
        "SELECT key, cites, cited_by, fetched_at FROM question_citations WHERE run_id = ?", (run_id,)
    ):
        last = max(last or at, at)
        a = index.get(key)
        if a is None:
            continue
        for work in jsonfmt.loads(cites):
            if (b := _match(work, index)) and b != a:
                found.add((a, b))
        for work in jsonfmt.loads(cited_by):
            if (b := _match(work, index)) and b != a:
                found.add((b, a))
    return {"citations": [list(e) for e in sorted(found)], "fetched_at": last}


def _match(work: dict[str, Any], index: dict[str, str]) -> str | None:
    return next((index[k] for k in work["keys"] if k in index), None)
