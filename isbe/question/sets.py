"""Paper sets (`paper_set@1`) and the operations between them (`set_op@1`). Sole
writer of `paper_sets` and `set_ops`.

A set is a list of members, each a paper record with every way it reached the
run (`provenance`). Query sets are made by searches; every other set is the
output of an operation, recorded with its inputs and parameters, so `replay`
rebuilds every derived set from the query sets alone.

Identity: a paper's keys are its OpenAlex work, its lower-case DOI and its
arXiv id without version (`sources.identity_keys`), preferred in that order.

- `union` joins its inputs; members with the same preferred `key` become one.
- `intersection` keeps the first input's members that share a key with a member
  of every other input; `difference` keeps those that share none with any.
- `dedup` folds members that share any key. Two different OpenAlex works stay
  two papers (a preprint and its journal version) even when they claim the same
  DOI or arXiv id; the first seen keeps that key. The first-seen record wins,
  gaps in it are filled from the later ones, and provenance is the union.
- `screen` keeps the members whose effective decision is not exclude
  (`question.screen`); it is replayed with the decisions it was made with.

Stored documents hold each member's paper as a `paper_ref` (`question.papers`);
`get`, `replay` and `load` give the full members back.
"""

import copy
import sqlite3
from collections.abc import Callable, Sequence, Set
from dataclasses import dataclass, field
from typing import Any

from isbe.core import db, jsonfmt
from isbe.question import papers
from isbe.question.sources import key_rank

SET = "paper_set@1"
OP = "set_op@1"
OPS = ("union", "intersection", "difference", "dedup", "screen")

type Member = dict[str, Any]


@dataclass
class PaperSet:
    set_id: str
    kind: str
    label: str
    members: list[Member] = field(default_factory=list)
    made_by: str | None = None
    created_at: str = field(default_factory=db.now)

    def doc(self, run_id: str) -> dict[str, Any]:
        return {
            "schema": SET,
            "run_id": run_id,
            "set_id": self.set_id,
            "kind": self.kind,
            "label": self.label,
            "made_by": self.made_by,
            "created_at": self.created_at,
            "members": self.members,
        }

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> PaperSet:
        return cls(doc["set_id"], doc["kind"], doc["label"], doc["members"], doc["made_by"], doc["created_at"])

    def keys(self) -> set[str]:
        return {k for m in self.members for k in m["paper"]["keys"]}


# --- operations --------------------------------------------------------------------------


def union(inputs: Sequence[list[Member]]) -> list[Member]:
    out: list[Member] = []
    by_key: dict[str, Member] = {}
    for members in inputs:
        for member in members:
            key = member["paper"]["key"]
            if key in by_key:
                _fold(by_key[key], member)
            else:
                by_key[key] = copy.deepcopy(member)
                out.append(by_key[key])
    return out


def intersection(inputs: Sequence[list[Member]]) -> list[Member]:
    first, others = inputs[0], [_index(m) for m in inputs[1:]]
    out = []
    for member in first:
        matches = [_match(member, index) for index in others]
        if all(matches):
            kept = copy.deepcopy(member)
            for match in matches:
                _fold(kept, match, keys=False)
            out.append(kept)
    return out


def difference(inputs: Sequence[list[Member]]) -> list[Member]:
    others = [_index(m) for m in inputs[1:]]
    return [copy.deepcopy(m) for m in inputs[0] if not any(_match(m, index) for index in others)]


def dedup(members: list[Member]) -> tuple[list[Member], list[tuple[str, list[str]]]]:
    """The members folded by shared identity, and each fold as (kept key, merged keys)."""
    out: list[Member | None] = []
    owner: dict[str, int] = {}  # identity key -> index in out
    # The keys the folded members had in the input, kept member's first.
    origins: list[list[str]] = []
    for member in members:
        member = copy.deepcopy(member)
        paper = member["paper"]
        target = _join(paper, out, owner, origins)
        # A key another paper already holds stays with that paper.
        free = [k for k in paper["keys"] if owner.get(k, target) == target]
        if not free:
            # Every key is held by papers it cannot join (a repeat of a conflict):
            # it joins the holder of its preferred key.
            free, target = [paper["key"]], owner[paper["key"]]
        origin = paper["key"]
        paper["keys"], paper["key"] = free, free[0]
        if target is None:
            out.append(member)
            origins.append([origin])
            target = len(out) - 1
        else:
            _fold(out[target], member)
            origins[target].append(origin)
        for key in out[target]["paper"]["keys"]:
            owner[key] = target
    kept = [(m, keys) for m, keys in zip(out, origins, strict=True) if m is not None]
    folds = []
    for m, keys in kept:
        final = m["paper"]["key"]
        merged = [k for k in dict.fromkeys(keys) if k != final]
        if len(keys) > 1 and merged:
            folds.append((final, merged))
    return [m for m, _ in kept], folds


def _join(
    paper: dict[str, Any], out: list[Member | None], owner: dict[str, int], origins: list[list[str]]
) -> int | None:
    """The member `paper` is the same paper as, or None: the first one sharing a key
    whose OpenAlex work does not differ. Further members it shares keys with are
    folded into that one too, so a record that bridges two (a DOI and an arXiv id)
    makes them one."""
    target: int | None = None
    work = _openalex(paper)
    for index in dict.fromkeys(owner[k] for k in paper["keys"] if k in owner):
        theirs = _openalex(out[index]["paper"])
        if work is not None and theirs is not None and work != theirs:
            continue
        work = work or theirs
        if target is None:
            target = index
            continue
        _fold(out[target], out[index])
        origins[target] += origins[index]
        for key in out[index]["paper"]["keys"]:
            owner[key] = target
        out[index] = None
    return target


def _openalex(paper: dict[str, Any]) -> str | None:
    return next((k for k in paper["keys"] if k.startswith("openalex:")), None)


def _fold(kept: Member, other: Member, *, keys: bool = True) -> None:
    """`other` into `kept`: provenance joined, keys joined, gaps filled."""
    for entry in other["provenance"]:
        if entry not in kept["provenance"]:
            kept["provenance"].append(copy.deepcopy(entry))
    paper, extra = kept["paper"], other["paper"]
    if keys:
        joined = sorted(dict.fromkeys(paper["keys"] + extra["keys"]), key=key_rank)
        paper["keys"] = joined
        paper["key"] = joined[0]
    for name, value in extra.items():
        if name in ("key", "keys"):
            continue
        if paper.get(name) in (None, [], "") and value not in (None, [], ""):
            paper[name] = copy.deepcopy(value)


def _index(members: list[Member]) -> dict[str, Member]:
    return {k: m for m in members for k in m["paper"]["keys"]}


def _match(member: Member, index: dict[str, Member]) -> Member | None:
    return next((index[k] for k in member["paper"]["keys"] if k in index), None)


def apply(
    op: str, inputs: Sequence[list[Member]], excluded: Set[str] = frozenset()
) -> tuple[list[Member], list[tuple[str, list[str]]]]:
    """The output members of `op` over `inputs`, and dedup's folds (empty for the rest).
    `screen` leaves out the members whose key is in `excluded`."""
    if op not in OPS:
        raise ValueError(f"unknown set operation {op!r}")
    if not inputs or (op in ("intersection", "difference") and len(inputs) < 2):
        raise ValueError(f"{op} needs {'two or more inputs' if op != 'union' else 'an input'}")
    if op in ("dedup", "screen") and len(inputs) != 1:
        raise ValueError(f"{op} takes one input")
    if op == "dedup":
        return dedup(inputs[0])
    if op == "screen":
        return [copy.deepcopy(m) for m in inputs[0] if m["paper"]["key"] not in excluded], []
    return {"union": union, "intersection": intersection, "difference": difference}[op](inputs), []


# --- the store ---------------------------------------------------------------------------


class Store:
    """The sets and operations of one run: what `run` does is saved as it happens."""

    def __init__(self, conn: sqlite3.Connection, run_id: str) -> None:
        self.conn = conn
        self.run_id = run_id

    def free_id(self, base: str) -> str:
        """`base`, or `base-2`, `base-3`, … when the run already has a set by that id."""
        taken = {r[0] for r in self.conn.execute(
            "SELECT set_id FROM paper_sets WHERE run_id = ? AND (set_id = ? OR set_id LIKE ?)",
            (self.run_id, base, base + "-%"),
        )}
        n, candidate = 1, base
        while candidate in taken:
            n += 1
            candidate = f"{base}-{n}"
        return candidate

    def add(self, paper_set: PaperSet) -> PaperSet:
        with db.immediate(self.conn):
            self._insert(paper_set)
        return paper_set

    def has(self, set_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM paper_sets WHERE run_id = ? AND set_id = ?", (self.run_id, set_id)
        ).fetchone() is not None

    def get(self, set_id: str) -> PaperSet:
        row = self.conn.execute(
            "SELECT doc FROM paper_sets WHERE run_id = ? AND set_id = ?", (self.run_id, set_id)
        ).fetchone()
        if row is None:
            raise KeyError(set_id)
        return PaperSet.from_doc(load(self.conn, row[0]))

    def run(
        self, op: str, inputs: Sequence[str], output: str, kind: str, label: str,
        params: dict[str, Any] | None = None, excluded: Set[str] = frozenset(),
    ) -> tuple[PaperSet, dict[str, Any], list[tuple[str, list[str]]]]:
        """Apply `op` to the sets `inputs`, save the output set `output` and the
        `set_op@1`; the set, the op and dedup's folds."""
        sources = [self.get(i) for i in inputs]
        members, folds = apply(op, [s.members for s in sources], excluded)
        with db.immediate(self.conn):
            position = self.conn.execute(
                "SELECT coalesce(max(position), 0) + 1 FROM set_ops WHERE run_id = ?", (self.run_id,)
            ).fetchone()[0]
            op_id = f"op-{position}"
            result = PaperSet(output, kind, label, members, op_id)
            doc = {
                "schema": OP,
                "run_id": self.run_id,
                "op_id": op_id,
                "op": op,
                "inputs": list(inputs),
                "params": params or {},
                "output": output,
                "created_at": result.created_at,
                "stats": {
                    "input_sizes": [len(s.members) for s in sources],
                    "output_size": len(members),
                    **({"merged": len(sources[0].members) - len(members)} if op == "dedup" else {}),
                },
            }
            self.conn.execute(
                "INSERT INTO set_ops (run_id, op_id, position, op, output, doc, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (self.run_id, op_id, position, op, output, jsonfmt.dumps(doc, sort_keys=False), doc["created_at"]),
            )
            self._insert(result)
        return result, doc, folds

    def ops(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT doc FROM set_ops WHERE run_id = ? ORDER BY position", (self.run_id,)
        ).fetchall()
        return [jsonfmt.loads(r[0]) for r in rows]

    def replay(self, excluded: Callable[[dict[str, Any]], Set[str]] | None = None) -> dict[str, list[Member]]:
        """Every set rebuilt from the stored query sets (`made_by` null) by running the
        recorded operations again in order: set id → members. A `screen` operation
        needs `excluded`, which gives the keys it left out (`question.screen.replay`)."""
        rows = self.conn.execute(
            "SELECT set_id, doc FROM paper_sets WHERE run_id = ? AND made_by IS NULL", (self.run_id,)
        ).fetchall()
        sets = {set_id: load(self.conn, doc)["members"] for set_id, doc in rows}
        for op in self.ops():
            if op["op"] == "screen" and excluded is None:
                raise ValueError("a screen operation replays with its decisions: use question.screen.replay")
            left_out = excluded(op) if op["op"] == "screen" and excluded else frozenset()
            sets[op["output"]], _ = apply(op["op"], [sets[i] for i in op["inputs"]], left_out)
        return sets

    def _insert(self, paper_set: PaperSet) -> None:
        doc = paper_set.doc(self.run_id)
        doc["members"] = papers.slim(self.conn, self.run_id, doc["members"])
        self.conn.execute(
            "INSERT INTO paper_sets (run_id, set_id, kind, made_by, size, doc, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, paper_set.set_id, paper_set.kind, paper_set.made_by, len(paper_set.members),
             jsonfmt.dumps(doc, sort_keys=False), paper_set.created_at),
        )


def load(conn: sqlite3.Connection, text: str) -> dict[str, Any]:
    """A stored `paper_set@1` document with its members' papers put back."""
    doc = jsonfmt.loads(text)
    doc["members"] = papers.full(conn, doc["members"])
    return doc
