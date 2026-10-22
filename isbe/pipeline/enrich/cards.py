"""Evidence cards: one fast-tier call per paper pulls a few facts out of its
full text, each with a quote. A fact is kept only when its quote is in that
text and every number and URL it states is in its quote (see `evidence`); the
rest are named in `rejected`.

Row: `cards (item_id, lang, key, card, created_at)`, `card` being
`{"fields": {name: {"value", "quote"}}, "rejected": [name]}` with fields in
name order and each fact's keys in that order, the layout existing rows and hashes have.
"""

import sqlite3
from typing import Any

from isbe.core import assets, db, jsonfmt
from isbe.core.llm import Llm, Prompt, Tier
from isbe.topics import Lang

from .calls import EnrichError
from .evidence import fold, quoted, squeeze, supported
from .text import WHITESPACE

# What a card asks for, in report order.
FIELDS = ("source", "method", "data", "code", "compute", "results", "limits")
# About 20k tokens: the body and tables of nearly every paper; long appendices are cut.
MAX_INPUT_CHARS = 60_000


def refresh(conn: sqlite3.Connection, llm: Llm, run_id: str, lang: Lang, item_id: str) -> int | None:
    """Build the paper's card unless the stored one came from this prompt and this
    full text. The number of rejected facts when a card was built, None when
    there is no full text or the card is current."""
    code = Lang(lang).value
    system = assets.text("prompts", code, "card.md")
    row = conn.execute(
        "SELECT f.body, c.key FROM fulltext f "
        "LEFT JOIN cards c ON c.item_id = f.item_id AND c.lang = ? WHERE f.item_id = ?",
        (code, item_id),
    ).fetchone()
    if row is None:
        return None
    text, stored = row[0], row[1]
    key = db.sha256(f"{system}\n{text}")
    if stored == key:
        return None
    prompt = Prompt(purpose="cards", tier=Tier.FAST, system=system,
                    user=text[:MAX_INPUT_CHARS], max_tokens=8000)
    card = None
    last_call = ""
    for _ in range(2):
        reply = llm.complete(conn, run_id, prompt)
        raw = json_object(reply.text)
        if raw is not None:
            card = check(raw, text)
            break
        last_call = reply.call_id
    if card is None:
        raise EnrichError(f"the reply is not a JSON object twice; see llm_calls {last_call}")
    conn.execute(
        "INSERT OR REPLACE INTO cards (item_id, lang, key, card, created_at) VALUES (?, ?, ?, ?, ?)",
        (item_id, code, key, jsonfmt.dumps(card, sort_keys=False), db.now()),
    )
    return len(card["rejected"])


def json_object(reply: str) -> dict[str, Any] | None:
    """The outermost `{…}` of a reply, which may come wrapped in a code fence."""
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        value = jsonfmt.loads(reply[start:end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _join(facts: list[Any], key: str, sep: str) -> str:
    parts = (f.get(key) for f in facts if isinstance(f, dict))
    return sep.join(p.strip(WHITESPACE) for p in parts if isinstance(p, str) and p.strip(WHITESPACE))


def check(raw: dict[str, Any], text: str) -> dict[str, Any]:
    """The card of a model reply: `{"fields": {...}, "rejected": [...]}`."""
    source = fold(text)
    hay = squeeze(source)
    fields: dict[str, dict[str, str]] = {}
    rejected: list[str] = []
    for name in FIELDS:
        if name not in raw:
            continue
        # A field may come as a list of facts; it is read as one.
        field = raw[name]
        facts = field if isinstance(field, list) else [field]
        value, quote = _join(facts, "value", "; "), _join(facts, "quote", "\n")
        if value and quoted(quote, hay) and supported(value, quote, source):
            fields[name] = {"value": value, "quote": quote}
        else:
            rejected.append(name)
    return {"fields": dict(sorted(fields.items())), "rejected": rejected}
