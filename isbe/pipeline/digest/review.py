"""The reviewer: one smart-tier call lists the report's
claims that the evidence does not back. Any flag earns the writer one rewrite.
"""

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from isbe.core import assets, jsonfmt
from isbe.core.errors import describe
from isbe.core.llm import Llm, Prompt, Reply, Tier
from isbe.topics import Lang

from . import parse
from .parse import lines, trim


@dataclass(frozen=True)
class Flag:
    verdict: str  # drift | unsupported
    claim: str
    note: str


@dataclass
class Review:
    """What the reviewer found in the report the digest kept."""

    first: list[Flag] = field(default_factory=list)
    # Flags on the rewrite; None when there was none.
    second: list[Flag] | None = None
    # The report is the rewrite, not the first draft.
    rewritten: bool = False
    error: str | None = None
    # Papers the first draft reviewed and the rewrite left out; the rewrite is
    # then not adopted and not reviewed.
    dropped: list[str] = field(default_factory=list)


class ReviewError(Exception):
    pass


def reviewed(text: str, papers: Sequence[str]) -> set[str]:
    """The papers (bare arXiv ids) that have a block in the reply."""
    blocks = parse.paper_blocks(parse.sections(text).get("papers", ""))
    return {p for p in papers if p in blocks}


def run(
    conn: sqlite3.Connection,
    llm: Llm,
    run_id: str,
    lang: Lang,
    evidence: str,
    write: Callable[[str], Reply],
    first: Reply,
    papers: Sequence[str],
) -> tuple[Reply, Review]:
    """Review `first`; when the reviewer flags claims, `write` the report once more
    with its notes and review that. The rewrite is kept unless it leaves out a
    paper the first draft reviewed (`papers` are the ids the report shows) or
    draws more flags than the first draft. A failed review or rewrite keeps the
    report as it stands and says so in `error`."""
    review = Review()

    def check(text: str) -> list[Flag]:
        return flags(conn, llm, run_id, lang, evidence, checkable(text))

    try:
        found = check(first.text)
    except Exception as e:
        review.error = describe(e)
        return first, review
    if not found:
        return first, review
    review.first = found
    try:
        second = write(evidence + feedback(lang, review.first))
    except Exception as e:
        review.error = f"rewrite: {describe(e)}"
        return first, review
    # Fewer flags is no gain when the rewrite drops whole paper reviews.
    kept = reviewed(second.text, papers)
    review.dropped = [p for p in papers if p in reviewed(first.text, papers) and p not in kept]
    if review.dropped:
        return first, review
    review.rewritten = True
    try:
        again = check(second.text)
    except Exception as e:
        review.error = describe(e)
        return second, review
    review.second = again
    if len(again) > len(review.first):
        review.rewritten = False
        return first, review
    return second, review


def checkable(reply: str) -> str:
    """The reply without its glossary and memory drafts: glossary entries explain
    terms from general knowledge, which no paper in the evidence states."""
    keep = True
    out: list[str] = []
    for line in lines(reply):
        t = trim(line)
        if t.startswith("## "):
            keep = trim(t[3:]) not in ("名词", "Glossary", "蒸馏", "Memory drafts")
        if keep:
            out.append(line + "\n")
    return "".join(out)


def flags(
    conn: sqlite3.Connection, llm: Llm, run_id: str, lang: Lang, evidence: str, report: str
) -> list[Flag]:
    """The claims in `report` that `evidence` does not back. A reply that is not a
    JSON array is asked for again once, then an error: unread is not clean."""
    if lang is Lang.ZH:
        user = f"=== 证据 ===\n{evidence}\n\n=== 报告 ===\n{report}"
    else:
        user = f"=== Evidence ===\n{evidence}\n\n=== Report ===\n{report}"
    prompt = Prompt(
        purpose="reviewer",
        tier=Tier.SMART,
        system=assets.text("prompts", lang.value, "review.md"),
        user=user,
        max_tokens=8000,
    )
    last_call = ""
    for _ in range(2):
        reply = llm.complete(conn, run_id, prompt)
        found = parse_flags(reply.text)
        if found is not None:
            return found
        last_call = reply.call_id
    raise ReviewError(f"the reviewer's reply is not a JSON array twice; see llm_calls {last_call}")


def _text(item: object, key: str) -> str:
    """The trimmed string at `key` of a reviewer item, or "" when absent or not a string."""
    value = item.get(key) if isinstance(item, dict) else None
    return trim(value) if isinstance(value, str) else ""


def parse_flags(reply: str) -> list[Flag] | None:
    start, end = reply.find("["), reply.rfind("]")
    if start < 0 or end < start:
        return None
    try:
        items = jsonfmt.loads(reply[start : end + 1])
    except (ValueError, RecursionError):
        return None
    if not isinstance(items, list):
        return None
    out: list[Flag] = []
    for item in items:
        verdict, claim = _text(item, "verdict"), _text(item, "claim")
        if verdict in ("drift", "unsupported") and claim:
            out.append(Flag(verdict, claim, _text(item, "note")))
    return out


def feedback(lang: Lang, found: list[Flag]) -> str:
    """Appended to the writer's input for the one rewrite."""
    if lang is Lang.ZH:
        s = "\n\n=== 审核员意见 ===\n以下断言未通过证据审核：改写到与证据一致，或删除无据断言，然后重新输出完整六段。\n"
    else:
        s = (
            "\n\n=== Reviewer notes ===\nThese claims failed the evidence check: rewrite them "
            "to match the evidence or drop them, then output all six sections again.\n"
        )
    return s + "".join(f"- [{f.verdict}] {f.claim} — {f.note}\n" for f in found)
