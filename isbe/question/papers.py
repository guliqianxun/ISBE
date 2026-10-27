"""The paper records of question runs, each stored once. Sole writer of
`question_papers`.

A record (common#paper) is kept under its `ref`, a digest of its content, so the
same record reached by many queries, sets, events and runs is one row. Hits,
set members and events hold the ref (`slim`) and are given the record back
when read (`full`), so their readers return what was stored before.
Records are never changed; a paper whose record differs (gaps filled by a fold,
another source's fields) is another row. Two copies with the same content in
another key order are one row, read back in the order first stored. Rows are
shared by runs; `question_paper_uses` records which runs use each, so deleting a
run (`runs.delete`) can remove the ones only it used (`forget`, then `prune`)
without scanning the others.

The raw essaies replies of `question_queries` are zlib-compressed (`pack`,
`unpack`): they are kept for audit and replay, and rarely read.
"""

import copy
import sqlite3
import zlib
from collections.abc import Iterable, Mapping
from typing import Any

from isbe.core import db, jsonfmt
from isbe.core.paperrefs import has_paper, ref, slim_item

__all__ = ["forget", "full", "get", "pack", "prune", "put", "ref", "slim", "unpack"]

# Refs looked up per query (SQLite allows 32,766 parameters).
_CHUNK = 500

type Paper = dict[str, Any]


def put(conn: sqlite3.Connection, run_id: str, papers: Iterable[Mapping[str, Any]]) -> list[str]:
    """Store the records not stored yet, used by `run_id`; their refs, in order.
    Joins the caller's transaction when one is open: the caller writes the rows
    that refer to them in the same one."""
    rows = [(ref(p), jsonfmt.dumps(p, sort_keys=False)) for p in papers]
    with db.immediate(conn):
        conn.executemany("INSERT OR IGNORE INTO question_papers (ref, paper) VALUES (?, ?)", rows)
        conn.executemany(
            "INSERT OR IGNORE INTO question_paper_uses (run_id, ref) VALUES (?, ?)", [(run_id, r) for r, _ in rows]
        )
    return [r for r, _ in rows]


def get(conn: sqlite3.Connection, refs: Iterable[str]) -> dict[str, Paper]:
    """The records under `refs`: ref → the record. A ref with no record is a
    `KeyError`."""
    wanted = list(dict.fromkeys(refs))
    out: dict[str, Paper] = {}
    for i in range(0, len(wanted), _CHUNK):
        chunk = wanted[i : i + _CHUNK]
        marks = ",".join("?" * len(chunk))
        for found, text in conn.execute(f"SELECT ref, paper FROM question_papers WHERE ref IN ({marks})", chunk):
            out[found] = jsonfmt.loads(text)
    missing = [r for r in wanted if r not in out]
    if missing:
        raise KeyError(f"no paper record under {missing[0]}")
    return out


def slim(conn: sqlite3.Connection, run_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copies of set members or event data with their `paper` stored and replaced
    by its `paper_ref`, at the same position; items without a paper are left as
    they are."""
    refs = iter(put(conn, run_id, [i["paper"] for i in items if has_paper(i)]))
    return [slim_item(i, lambda _: next(refs)) if has_paper(i) else i for i in items]


def full(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`slim` undone: copies with each `paper_ref` replaced by its record under
    `paper`, at the same position, so the items read as they were written. Each
    item gets its own copy of the record."""
    records = get(conn, [i["paper_ref"] for i in items if "paper_ref" in i])
    return [
        {("paper" if k == "paper_ref" else k): (copy.deepcopy(records[v]) if k == "paper_ref" else v)
         for k, v in i.items()}
        if "paper_ref" in i else i
        for i in items
    ]


def forget(conn: sqlite3.Connection, run_id: str) -> list[str]:
    """Drop `run_id`'s uses of records; the refs it used, for `prune` once the
    run's rows are gone. Joins the caller's transaction when one is open."""
    with db.immediate(conn):
        refs = [r for (r,) in conn.execute("SELECT ref FROM question_paper_uses WHERE run_id = ?", (run_id,))]
        conn.execute("DELETE FROM question_paper_uses WHERE run_id = ?", (run_id,))
    return refs


def prune(conn: sqlite3.Connection, refs: list[str]) -> int:
    """Remove the records among `refs` that no run uses any more; how many. Costs
    the number of refs, not the size of the workspace. Joins the caller's
    transaction when one is open."""
    with db.immediate(conn):
        removed = 0
        for i in range(0, len(refs), _CHUNK):
            chunk = refs[i : i + _CHUNK]
            removed += conn.execute(
                f"DELETE FROM question_papers WHERE ref IN ({','.join('?' * len(chunk))}) AND NOT EXISTS"
                " (SELECT 1 FROM question_paper_uses u WHERE u.ref = question_papers.ref)",
                chunk,
            ).rowcount
        return removed


def pack(text: str | None) -> bytes | None:
    return None if text is None else zlib.compress(text.encode("utf-8"), 6)


def unpack(blob: bytes | None) -> str | None:
    return None if blob is None else zlib.decompress(blob).decode("utf-8")
