"""Content refs of paper records, and the SQL functions migration 016 uses to
move stored records into `question_papers` (see `isbe.question.papers`).

They live in core because `db` registers them on every connection it migrates.
"""

import hashlib
import sqlite3
import zlib
from collections.abc import Callable, Mapping
from typing import Any

from isbe.core import jsonfmt

# Hex digits of the SHA-256 kept as a ref: 96 bits.
REF_LENGTH = 24


def ref(paper: Mapping[str, Any]) -> str:
    """The record's ref: the same for the same content, whatever its key order."""
    return hashlib.sha256(jsonfmt.dumps(paper, sort_keys=True).encode()).hexdigest()[:REF_LENGTH]


def slim_item(item: Mapping[str, Any], ref_of: Callable[[Mapping[str, Any]], str] = ref) -> dict[str, Any]:
    """A copy of a set member or event data with its `paper` record replaced by the
    record's `paper_ref`, at the same position among the keys."""
    return {("paper_ref" if k == "paper" else k): (ref_of(v) if k == "paper" else v) for k, v in item.items()}


def has_paper(item: Mapping[str, Any]) -> bool:
    return isinstance(item.get("paper"), dict)


def register(conn: sqlite3.Connection) -> None:
    """The functions migration 016 calls."""

    def paper_ref(text: str) -> str:
        return ref(jsonfmt.loads(text))

    def paper_json(text: str) -> str:
        return jsonfmt.dumps(jsonfmt.loads(text), sort_keys=False)

    def slim_members(text: str) -> str:
        doc = jsonfmt.loads(text)
        doc["members"] = [slim_item(m) if has_paper(m) else m for m in doc["members"]]
        return jsonfmt.dumps(doc, sort_keys=False)

    def slim_event(text: str) -> str:
        data = jsonfmt.loads(text)
        return jsonfmt.dumps(slim_item(data) if has_paper(data) else data, sort_keys=False)

    def deflate(text: str | None) -> bytes | None:
        return None if text is None else zlib.compress(text.encode("utf-8"), 6)

    for name, fn in (("isbe_paper_ref", paper_ref), ("isbe_paper_json", paper_json),
                     ("isbe_slim_members", slim_members), ("isbe_slim_event", slim_event),
                     ("isbe_deflate", deflate)):
        conn.create_function(name, 1, fn, deterministic=True)
