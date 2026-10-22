"""Relevance triage over a topic's window: rule gates on the
title, then the LLM judge, then tiers. Sole writer of `triage`.

A verdict is cached under the hash of the retrieval block and the judge
prompt, so a re-run judges only new items unless one of those changed. A
batch whose judge call fails is kept unjudged (under an empty hash, so the
next run judges it again) and fails the run once every batch is done. A
refused or filtered reply (`core.llm.Refused`) is such a failure: its text is
not read for verdicts.
"""

import sqlite3
from datetime import UTC, datetime
from typing import Any

from isbe.core import assets, db, validators
from isbe.core.errors import describe
from isbe.core.llm import Llm, Prompt, Tier
from isbe.topics import Retrieval, Topic

BATCH = 20
UNJUDGED = "not judged yet (judge failed or skipped it)"


class TriageError(Exception):
    pass


def judge_prompt(topic: Topic) -> str:
    return assets.text("prompts", topic.lang.value, "judge.md")


def contract_sha(topic: Topic, system: str) -> str:
    if topic.retrieval is None:
        return db.sha256("no retrieval block")
    return db.sha256(f"{topic.retrieval.contract_json()}\n{system}")


def run(
    conn: sqlite3.Connection, llm: Llm, run_id: str, topic: Topic, stats: dict[str, Any]
) -> None:
    """Judge the window's untriaged items of `topic`, recording counts in `stats`:
    `window_candidates`, `cached` (+ `cached_<verdict>`), `to_judge`, then
    `kept`, `gate_dropped`, `judge_dropped` and `unjudged` as they occur."""
    system = judge_prompt(topic)
    sha = contract_sha(topic, system)
    since = topic.since(datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        "SELECT i.id, i.title, coalesce(i.summary, ''), t.verdict FROM topic_items ti "
        "JOIN items i ON i.id = ti.item_id "
        "LEFT JOIN triage t ON t.topic_id = ti.topic_id AND t.item_id = i.id AND t.contract_sha = ? "
        "WHERE ti.topic_id = ? AND i.kind != 'repo' AND i.published_at >= ? "
        "ORDER BY i.published_at DESC",
        (sha, topic.id, since),
    ).fetchall()
    stats["window_candidates"] = len(rows)
    todo: list[tuple[str, str, str]] = []
    cached = 0
    for id, title, summary, verdict in rows:
        if verdict is not None:
            cached += 1
            bump(stats, f"cached_{verdict}")
        else:
            todo.append((id, title, summary))
    stats["cached"] = cached
    stats["to_judge"] = len(todo)

    def save(id: str, key: str, verdict: str, tier: str | None, reason: str) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO triage (topic_id, item_id, contract_sha, verdict, tier, "
            "reason, judged_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (topic.id, id, key, verdict, tier, reason, db.now()),
        )

    r = topic.retrieval
    if r is None:
        for id, _, _ in todo:
            save(id, sha, "kept", "core", "no retrieval block: everything is kept")
        stats["kept"] = len(todo)
        return

    judge = []
    for candidate in todo:
        why = gate(r, candidate[1])
        if why is not None:
            save(candidate[0], sha, "gate_dropped", None, why)
            bump(stats, "gate_dropped")
        else:
            judge.append(candidate)

    failures = []
    for start in range(0, len(judge), BATCH):
        batch = judge[start : start + BATCH]
        prompt = Prompt("judge", Tier.FAST, system, judge_input(r, batch), 8000)
        try:
            reply = llm.complete(conn, run_id, prompt)
        except Exception as error:  # any failed call, a refusal included: kept unjudged
            failures.append(describe(error))
            verdicts = {}
        else:
            verdicts = parse_verdicts(reply.text, len(batch), r.facets)
        for i, (id, title, summary) in enumerate(batch):
            found = verdicts.get(i)
            if found is None:
                # Recall-safe: keep it, but store no contract hash so the next run judges it again.
                save(id, "", "kept", tier(r, title, summary), UNJUDGED)
                bump(stats, "unjudged")
            elif found[0]:
                _, facet, why = found
                save(id, sha, "kept", tier(r, title, summary), f"[{facet}] {why}" if facet else why)
                bump(stats, "kept")
            else:
                save(id, sha, "judge_dropped", None, found[2])
                bump(stats, "judge_dropped")
    if failures:
        batches = -(-len(judge) // BATCH)
        raise TriageError(
            f"judge failed on {len(failures)} of {batches} batches; those items are kept and "
            f"re-judged next run: {failures[0]}"
        )


def bump(stats: dict[str, Any], key: str) -> None:
    n = stats.get(key)
    stats[key] = (n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else 0) + 1


def gate(r: Retrieval, title: str) -> str | None:
    """The rule gates, on the title only (an abstract's motivation sentence would
    trip them): an out-of-scope keyword, or none of `require_any`."""
    lowered = title.lower()

    def hit(terms: list[str]) -> str | None:
        return next((t for t in terms if t and t.lower() in lowered), None)

    if (k := hit(r.out_of_scope_keywords)) is not None:
        return f"out_of_scope keyword in title: {k}"
    if r.require_any and hit(r.require_any) is None:
        return "no require_any term in title"
    return None


def tier(r: Retrieval, title: str, summary: str) -> str:
    """Kept items mentioning a `secondary_terms` entry rank after the core."""
    text = f"{title}\n{summary}".lower()
    return "secondary" if any(t and t.lower() in text for t in r.secondary_terms) else "core"


def judge_input(r: Retrieval, batch: list[tuple[str, str, str]]) -> str:
    def listed(name: str, xs: list[str]) -> str:
        return f"{name}:\n" + "\n".join(f"  - {x}" for x in xs) + "\n" if xs else ""

    s = f"intent: {r.intent}\n"
    s += listed("in_scope", r.in_scope)
    s += listed("out_of_scope", r.out_of_scope)
    if r.facets:
        s += f"facets: {', '.join(r.facets)}\n"
    if r.quality_bar:
        s += f"quality_bar: {r.quality_bar}\n"
    for i, (_, title, summary) in enumerate(batch):
        s += f"\n[{i + 1}] {title}\n{summary[:280].replace(chr(10), ' ')}"
    return s


def _lines(text: str) -> list[str]:
    """Split on `\\n`, a trailing `\\r` dropped, no final empty line."""
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line.removesuffix("\r") for line in lines]


def parse_verdicts(
    text: str, n: int, facets: list[str]
) -> dict[int, tuple[bool, str, str]]:
    """`n|IN|facet|reason` lines → {0-based index: (in, facet, reason)}. Lines that
    do not parse, or number an item outside the batch, are ignored."""
    out: dict[int, tuple[bool, str, str]] = {}
    # Small models sometimes write a literal `\n` between verdicts.
    for line in _lines(text.replace("\\n", "\n")):
        parsed = verdict_line(line)
        if parsed is None:
            continue
        idx, keep, rest = parsed
        if not 1 <= idx <= n:
            continue
        # `n|OUT|reason` with the empty facet left out: a lone field is the
        # facet only if it names one.
        fields = [validators.trim(f) for f in rest.split("|")]
        if len(fields) == 1 and fields[0] not in facets:
            fields.insert(0, "")
        why = validators.trim(" ".join(fields[1:]).strip("`"))
        out[idx - 1] = (keep, fields[0], why or ("in scope" if keep else "out of scope"))
    return out


def verdict_line(line: str) -> tuple[int, bool, str] | None:
    """The item number, IN or OUT, and what follows (`facet|reason`). Accepts
    the asked-for `3|IN|…` and what models write instead: `[3] IN | …`,
    `3. OUT …`, `| 3 | IN | … |`."""
    s = validators.trim(line).lstrip("|-*`[ ")
    digits = 0
    while digits < len(s) and "0" <= s[digits] <= "9":
        digits += 1
    number = s[:digits].lstrip("0") or "0"
    if digits == 0 or len(number) > 20 or int(number) > 2**64 - 1:  # fits 64 unsigned bits
        return None
    idx = int(number)
    s = s[digits:].lstrip("]).:|` \t")
    # ASCII case only (no `ı` → `I`).
    head = "".join(c.upper() if "a" <= c <= "z" else c for c in s[:3])
    if head.startswith("OUT"):
        keep, length = False, 3
    elif head.startswith("IN"):
        keep, length = True, 2
    else:
        return None
    rest = s[length:]
    if rest[:1].isalnum():
        return None  # INSIDE, OUTPUT
    return idx, keep, validators.trim(validators.trim(rest).strip("|"))
