"""One hop of citations from the included papers (#50): the second half of the expand
stage, after `citations` has looked the candidates up. It makes up for what the
keyword search missed: the papers the included ones cite or are cited by.

The seeds are the included papers, unscreened ones counting as included (recall
first, as everywhere). Each neighbour of a seed that is not already a candidate
(matched on any identity key) and has a title is a pick; picks are ranked by how
many seeds they neighbour, then by their citation count, and each seed adds at most
`PER_PAPER`, the run at most `TOTAL`. The picks are the expansion set
(`<tag>.expansion`, kind expansion), each with a provenance entry per seed it came
from (`via` cites: a reference of that seed; cited_by: it cites the seed); the new
candidate set is the old one and the expansion, deduplicated. Each pick is an
`expand` event; then the screening judges the picks alone with the plan's criteria
(`screen.run` without its `stage` events, inside the expand stage).

Lookups carry no abstracts, so a pick is judged on its title, year and venue.
The picks' citation edges come from their seeds' lookups, so the map links them
without asking essaies again.

Picking happens once, in one transaction with the new candidate set and the
`expand` events: a stage that runs again (a resumed pause) finds the expansion
set and only finishes its screening. A rerun's carried overrides are applied to
the picks once they are candidates (`flow`).
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from isbe.core import db, jsonfmt
from isbe.core.llm import Llm
from isbe.question import runs, screen, service, sets

PER_PAPER = 20
TOTAL = 200
LABEL = "One hop of citations from the included papers"


@dataclass
class Result:
    seeds: int = 0
    added: int = 0
    screened: bool = False


def pick(conn: sqlite3.Connection, run_id: str, *, per_paper: int = PER_PAPER, total: int = TOTAL) -> list[sets.Member]:
    """The neighbours of the run's included papers worth adding, best first, as set
    members with their provenance."""
    members = service.papers(conn, run_id)
    known = {k for m in members for k in m["paper"]["keys"]}
    owner = {k: m["paper"]["key"] for m in members for k in m["paper"]["keys"]}
    seeds = {m["paper"]["key"] for m in members if m["decision"] in ("include", screen.UNSCREENED)}
    found: list[dict[str, Any]] = []  # {paper, from: [(seed, via)], seeds: set}
    index: dict[str, dict[str, Any]] = {}
    for key, cites, cited_by in conn.execute(
        "SELECT key, cites, cited_by FROM question_citations WHERE run_id = ? ORDER BY rowid", (run_id,)
    ):
        seed = owner.get(key)
        if seed not in seeds:
            continue
        for via, works in (("cites", cites), ("cited_by", cited_by)):
            for work in jsonfmt.loads(works):
                if not work.get("title") or any(k in known for k in work["keys"]):
                    continue
                entry = next((index[k] for k in work["keys"] if k in index), None)
                if entry is None:
                    entry = {"paper": work, "from": [], "seeds": set()}
                    found.append(entry)
                for k in work["keys"]:
                    index.setdefault(k, entry)
                if (seed, via) not in entry["from"]:
                    entry["from"].append((seed, via))
                    entry["seeds"].add(seed)
    found.sort(key=lambda e: (-len(e["seeds"]), -(e["paper"].get("cited_by_count") or 0), e["paper"]["key"]))
    used: dict[str, int] = {}
    out: list[sets.Member] = []
    for entry in found:
        if len(out) >= total:
            break
        seed = next((s for s, _ in entry["from"] if used.get(s, 0) < per_paper), None)
        if seed is None:
            continue
        used[seed] = used.get(seed, 0) + 1
        provenance = [{"via": via, "from_key": s} for s, via in entry["from"]]
        out.append({"paper": entry["paper"], "provenance": provenance})
    return out


def run(
    conn: sqlite3.Connection,
    llm: Llm | None,
    run_id: str,
    *,
    unavailable: str = "no model is configured",
    check: Callable[[], None] = lambda: None,
    per_paper: int = PER_PAPER,
    total: int = TOTAL,
    screening: dict[str, Any] | None = None,
) -> Result:
    """Add the picks as candidates and screen them; nothing when no paper is
    included or none is left to add."""
    out = Result()
    store = sets.Store(conn, run_id)
    base = service.candidates_set(conn, run_id)
    if base is None:
        return out
    tag = base.split(".", 1)[0]
    expansion = f"{tag}.expansion"
    if not store.has(expansion):
        picks = pick(conn, run_id, per_paper=per_paper, total=total)
        out.seeds = len({p["from_key"] for m in picks for p in m["provenance"]})
        if not picks:
            return out
        # One transaction: an interrupted stage never keeps the picks without their
        # candidate set, which would skip them for good on resume.
        with db.immediate(conn):
            store.add(sets.PaperSet(expansion, "expansion", LABEL, picks))
            union, _, _ = store.run("union", [base, expansion], store.free_id(f"{tag}.union"), "candidates",
                                    "Candidates and their expansion", {"per_paper": per_paper, "total": total})
            store.run("dedup", [union.set_id], store.free_id(f"{tag}.candidates"), "candidates",
                      "Candidates after expansion and deduplication")
            runs.emit(conn, run_id, [
                ("expand", {"from_key": m["provenance"][0]["from_key"], "via": m["provenance"][0]["via"],
                            "paper": m["paper"], "new": True})
                for m in picks
            ])
        out.added = len(picks)
        resume = False
    else:
        out.added = len(store.get(expansion).members)
        resume = True
    check()
    screen.run(conn, llm, run_id, expansion, unavailable=unavailable, check=check, resume=resume, frame=False,
               **(screening or {}))
    out.screened = True
    return out

