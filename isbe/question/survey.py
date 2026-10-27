"""Survey reports of question runs: the papers a run's screening included → their
full text and evidence cards → one Smart call → `reports/<run>/<date>.md`, kind
`survey`, on the shelf beside the topic reports. Sole writer of `survey` reports.

Reuses the topic pipeline rather than a path of its own:

- Each included paper is registered as an `items` row (`kind='paper'`,
  `source='question'`, no `topic_items` or `triage` row, so no topic page shows
  it): its arXiv id when it has one (`arxiv:<id>`, the id collected papers
  already use, so their stored full text and cards are reused), else its
  preferred key (`openalex:W…`, `doi:…`). An existing row is left as it is.
- Full text is arXiv HTML through `enrich.stage.fetch_html` (same spacing and
  text extraction; method figures are not fetched). A paper without an arXiv
  id, or whose HTML is unavailable, is read from its abstract only and the
  report says so.
- Facts come from `enrich.cards.refresh`: the card prompt and its quote checks.
- The prompt's paper blocks follow the digest's: verified facts and the tables
  of the full text (`digest.run.tables`).

The model writes the survey sections and cites papers as `[P<n>]`; a citation
of a number not in the input is marked `[P?]` and counted. The title, the
paper list and the per-paper verified facts with their quotes are written here,
not by the model.
"""

import logging
import re
import sqlite3
import time
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from isbe.core import assets, db, errors, http, jsonfmt, settings
from isbe.core.llm import Llm, LlmError, Prompt, ProviderError, Refused, Tier
from isbe.pipeline.digest import store
from isbe.pipeline.digest.model import FACTS, Card
from isbe.pipeline.digest.run import tables
from isbe.pipeline.enrich import arxiv_html, cards
from isbe.pipeline.enrich.calls import EnrichError, StatusError
from isbe.pipeline.enrich.stage import HTML_TIMEOUT, Load, extractor, fetch_html, spaced
from isbe.question import runs, screen, service
from isbe.topics import Lang

log = logging.getLogger(__name__)

KIND = "survey"
# Papers the model reviews; the other included papers are listed only.
MAX_PAPERS = 20
# The prompt's share of full-text tables, per paper and in all (the digest's defaults).
TABLE_CHARS = 3000
TABLE_TOTAL = 24_000
ABSTRACT_CHARS = 2500
# arXiv outages in a row (`_arxiv_down`) before the remaining fetches are skipped.
ARXIV_DOWN = 2
_CITE = re.compile(r"\[P(\d+)\]")
# `[P1, P3]` and the like, written as `[P1][P3]`.
_CITE_LIST = re.compile(r"\[P\d+(?:\s*[,，、;；]\s*P\d+)+\]")
# A heading of the model's that the shelf would count as a paper block.
_PAPER_HEADING = re.compile(r"^(\s*###\s*)\[", re.M)
# A `*` or `+` bullet; the shelf's cover reads TL;DR bullets written with `-`.
_BULLET = re.compile(r"^(\s*)[*+]\s+", re.M)


class SurveyError(Exception):
    pass


@dataclass
class Source:
    """One reviewed paper: its number in the prompt and report, its record and what was read."""

    n: int
    item_id: str
    paper: dict[str, Any]
    fulltext: str | None = None
    card: Card | None = None


def included(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    """The run's papers whose effective decision (overrides included) is include,
    most relevant first: a person's include, then the screening score, then the
    citation count, then the key."""

    def rank(m: dict[str, Any]) -> tuple[float, int, str]:
        score = 2.0 if m["decided_by"] == "override" else (m["verdict"] or {}).get("score") or 0.0
        return -score, -(m["paper"].get("cited_by_count") or 0), m["paper"]["key"]

    return [m["paper"] for m in sorted(
        (m for m in service.papers(conn, run_id) if m["decision"] == "include"), key=rank,
    )]


def item_id(paper: dict[str, Any]) -> str:
    """The `items` id of a question paper: its arXiv key if any, else its preferred key."""
    return next((k for k in paper.get("keys") or [] if k.startswith("arxiv:")), paper["key"])


def register(conn: sqlite3.Connection, papers: Sequence[dict[str, Any]]) -> None:
    """An `items` row for each paper that has none."""
    rows = []
    for p in papers:
        year = p.get("year")
        rows.append((
            item_id(p), p["title"], p.get("abstract"), p.get("url") or "",
            jsonfmt.dumps(p.get("authors") or []),
            f"{year:04d}-01-01T00:00:00Z" if isinstance(year, int) else "1970-01-01T00:00:00Z",
            jsonfmt.dumps({"keys": p.get("keys") or [p["key"]]}),
        ))
    conn.executemany(
        "INSERT OR IGNORE INTO items (id, kind, source, title, summary, url, authors, lang, published_at, extra) "
        "VALUES (?, 'paper', 'question', ?, ?, ?, ?, 'en', ?, ?)",
        rows,
    )


def run(
    conn: sqlite3.Connection,
    llm: Llm,
    ledger: str,
    data: Path,
    run_id: str,
    stats: dict[str, Any],
    *,
    max_papers: int = MAX_PAPERS,
    fulltext: bool = True,
    load: Load | None = None,
    sleep: Callable[[float], None] = time.sleep,
    today: str | None = None,
) -> None:
    """Write the survey report of question run `run_id` and record it in `reports`.

    The run must have ended (done, failed or cancelled); the report says how.

    Called inside `db.job(conn, "survey", None, ...)`; `ledger` is that job's run
    id, which the model calls are logged under. Fills `stats` with `report` (the
    data-relative path), `included`, `reviewed`, `fulltext`, `abstract_only`,
    `cards`, `cited`, `bad_citations`, `llm_call`, and `abstract_fallback` /
    `failed` (paper → reason) when HTML or a card could not be had; neither stops
    the report. Two arXiv outages in a row (no answer or a 5xx; a 404 for a paper
    without HTML is an answer) skip the remaining fetches (`arxiv_down`); a card whose model call fails (not its
    reply) stops the survey with `SurveyError` before the survey call. `load`
    replaces the arXiv HTML fetch and `sleep` the spacing (tests);
    `fulltext=False` or `ISBE_FULLTEXT_EXTRACTOR=off` fetches no new text, and
    any other extractor setting reads arXiv HTML. `today` (YYYY-MM-DD) names the
    report; it defaults to today in UTC.
    """
    meta = runs.get(conn, run_id)
    if meta["status"] not in runs.ENDED:
        raise SurveyError(f"the run is {meta['status']}; survey it once it has ended")
    lang = Lang.ZH if meta["lang"] == "zh" else Lang.EN
    if max_papers < 1:
        raise SurveyError("max_papers must be at least 1")
    papers = included(conn, run_id)
    if not papers:
        raise SurveyError("the run has no included papers to survey")
    period = today or datetime.now(UTC).date().isoformat()
    # Checked again when writing; refusing here spares the fetches and the model calls.
    store.check_free(conn, data, run_id, period, KIND)
    shown, listed = papers[:max_papers], papers[max_papers:]
    register(conn, shown)
    sources = [Source(n, item_id(p), p) for n, p in enumerate(shown, 1)]
    unscreened = sum(1 for d in screen.decisions(conn, run_id).values() if d["decision"] == screen.UNSCREENED)
    stats.update(included=len(papers), reviewed=len(sources), unscreened=unscreened)
    log.info("survey of %s: %d included, %d surveyed", run_id, len(papers), len(sources))

    mode = extractor(settings.get("ISBE_FULLTEXT_EXTRACTOR"), False)
    if fulltext and mode != "off":
        have = {r[0] for r in conn.execute(
            f"SELECT item_id FROM fulltext WHERE item_id IN ({','.join('?' * len(sources))})",
            [s.item_id for s in sources],
        )}
        todo = [(s.item_id, False) for s in sources if s.item_id.startswith("arxiv:") and s.item_id not in have]
        fallback: dict[str, Any] = {}
        with http.Http(HTML_TIMEOUT) if load is None else nullcontext(None) as web:
            fetch = load or (lambda id: arxiv_html.fetch(web, id))
            guarded = _Breaker(spaced(fetch, sleep))
            # No method figures: a NULL figure lets a topic's enrich still fetch one later.
            fetch_html(conn, todo, guarded, lambda _: None, fallback, {})
        if fallback:
            stats["abstract_fallback"] = fallback
        if guarded.down:
            stats["arxiv_down"] = True
            log.warning("survey of %s: arXiv unreachable; the rest are read from their abstracts", run_id)

    failed: dict[str, str] = {}
    for s in sources:
        try:
            cards.refresh(conn, llm, ledger, lang, s.item_id)
        except Exception as error:
            failed[s.item_id] = f"card: {errors.describe(error)}"
            if _model_down(error):
                stats["failed"] = failed
                raise SurveyError("the model call failed; no survey was written") from error
        row = conn.execute(
            "SELECT f.body, c.card FROM fulltext f LEFT JOIN cards c ON c.item_id = f.item_id AND c.lang = ? "
            "WHERE f.item_id = ?",
            (lang.value, s.item_id),
        ).fetchone()
        if row is not None:
            s.fulltext = row[0]
            s.card = None if row[1] is None else Card.from_json(row[1])
    if failed:
        stats["failed"] = failed

    reply = llm.complete(
        conn,
        ledger,
        Prompt(
            purpose=KIND,
            tier=Tier.SMART,
            system=assets.text("prompts", lang.value, "survey.md"),
            user=prompt_input(meta["question"], lang, sources, len(papers)),
            max_tokens=12_000,
        ),
    )
    abstract_only = {s.n for s in sources if s.fulltext is None}
    body, cited, bad = citations(reply.text, len(sources), abstract_only)
    generated_at = db.now()
    md = render(meta, lang, period, sources, listed, body, reply.call_id, generated_at, unscreened)
    fingerprint = {
        "llm_call": reply.call_id,
        "run_id": run_id,
        "papers": [s.paper["key"] for s in sources],
        "listed": [p["key"] for p in listed],
        "fulltext": [s.paper["key"] for s in sources if s.fulltext is not None],
    }
    rel = store.save(
        conn, data, report_id=f"{run_id}/{period}/{KIND}", topic=run_id, period=period, kind=KIND,
        body=md, fingerprint=jsonfmt.dumps(fingerprint), created_at=generated_at,
    )
    read = sum(1 for s in sources if s.fulltext is not None)
    stats.update(
        report=rel, fulltext=read, abstract_only=len(sources) - read,
        cards=sum(1 for s in sources if s.card is not None and s.card.fields),
        cited=len(cited), bad_citations=bad, llm_call=reply.call_id,
    )
    log.info("survey of %s: %s (%d full text, %d cited, %d bad citations)", run_id, rel, read, len(cited), bad)


class _Breaker:
    """`load` until `ARXIV_DOWN` outages in a row (`_arxiv_down`); then every call
    fails at once. arXiv answering, even "no HTML" (a 404), resets the count."""

    def __init__(self, load: Load) -> None:
        self.load, self.strikes, self.down = load, 0, False

    def __call__(self, id: str) -> arxiv_html.Paper:
        if self.down:
            raise EnrichError("skipped: arXiv did not answer the papers before")
        try:
            paper = self.load(id)
        except Exception as error:
            self.strikes = self.strikes + 1 if _arxiv_down(error) else 0
            self.down = self.strikes >= ARXIV_DOWN
            raise
        self.strikes = 0
        return paper


def _arxiv_down(error: BaseException) -> bool:
    """arXiv did not answer: a network error, a timeout or a 5xx anywhere in the
    cause chain (`enrich.calls.step` wraps them), not a 4xx or a page that is not HTML."""
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, httpx.TransportError) or str(seen) == "timeout: global":
            return True
        if isinstance(seen, StatusError):
            status = re.search(r"\d+", str(seen))
            return status is not None and int(status[0]) >= 500
        seen = seen.__cause__ or seen.__context__
    return False


def _model_down(error: BaseException) -> bool:
    """A card failed because the model could not be reached or is failing (network,
    auth, rate limit, 5xx), not because of this paper's prompt (another 4xx) or reply."""
    if not isinstance(error, LlmError) or isinstance(error, Refused) or " call to " not in str(error):
        return False
    cause = error.__cause__
    if isinstance(cause, ProviderError):
        status = re.match(r"HTTP (\d+)", str(cause))
        return status is None or status[1] not in ("400", "413", "422")
    return True


def _depth(s: Source, zh: bool) -> str:
    if s.fulltext is not None:
        return "全文" if zh else "full text"
    return "仅摘要" if zh else "abstract only"


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def prompt_input(question: str, lang: Lang, sources: Sequence[Source], included: int) -> str:
    zh = lang is Lang.ZH
    out = [
        f"研究问题：{question}\n纳入论文 {included} 篇，以下 {len(sources)} 篇供综述：\n\n"
        if zh else
        f"Research question: {question}\n{included} papers included; these {len(sources)} are for the survey:\n\n"
    ]
    budget = TABLE_TOTAL
    for s in sources:
        p = s.paper
        names = p.get("authors") or []
        authors = "; ".join(names[:6])
        if len(names) > 6:
            authors += f" 等 {len(names)} 人" if zh else f" et al. ({len(names)})"
        meta = ", ".join(str(v) for v in (p.get("year"), p.get("venue")) if v)
        out.append(f"[P{s.n}] {p['title']}" + (f" ({meta})" if meta else "") + "\n")
        out.append(f"  {'作者' if zh else 'Authors'}: {authors or '-'}\n")
        out.append(f"  {'阅读深度' if zh else 'Reading depth'}: {_depth(s, zh)}\n")
        if p.get("abstract"):
            out.append(f"  {'摘要' if zh else 'Abstract'}: {_clip(p['abstract'], ABSTRACT_CHARS)}\n")
        if s.card is not None:
            facts = [f"{zl if zh else el}={s.card.fields[k].value}" for k, zl, el in FACTS if k in s.card.fields]
            if facts:
                out.append(f"  {'全文事实（已核验）' if zh else 'Full-text facts (verified)'}: {' | '.join(facts)}\n")
        t = tables(s.fulltext or "", min(TABLE_CHARS, budget))
        if t:
            budget -= len(t)
            out.append("  全文表格:\n" if zh else "  Tables from the full text:\n")
            out.append(t)
        out.append("\n")
    return "".join(out)


def citations(
    text: str, count: int, abstract_only: frozenset[int] | set[int] = frozenset(),
) -> tuple[str, set[int], int]:
    """The model's sections cleaned up: level-1 headings dropped, bullets written
    `- `, `### [` headings escaped (the shelf counts those as papers), `[P1, P3]`
    split into `[P1][P3]`, citations of unknown numbers turned into `[P?]` and
    citations of `abstract_only` papers marked `[P3]†`. Returns the text, the
    numbers cited and how many citations were bad."""
    cited: set[int] = set()
    bad = 0

    def check(m: re.Match[str]) -> str:
        nonlocal bad
        n = int(m[1])
        if 1 <= n <= count:
            cited.add(n)
            return m[0] + ("†" if n in abstract_only else "")
        bad += 1
        return "[P?]"

    def split(m: re.Match[str]) -> str:
        return "".join(f"[{n}]" for n in re.findall(r"P\d+", m[0]))

    lines = [line for line in text.strip().splitlines() if not line.lstrip().startswith("# ")]
    body = _PAPER_HEADING.sub(r"\1\\[", _BULLET.sub(r"\1- ", "\n".join(lines)))
    return _CITE.sub(check, _CITE_LIST.sub(split, body)), cited, bad


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.strip().splitlines())


def render(
    run: dict[str, Any], lang: Lang, period: str, sources: Sequence[Source], listed: Sequence[dict[str, Any]],
    body: str, call_id: str, generated_at: str, unscreened: int = 0,
) -> str:
    """The report: title, scope note, the model's sections, then one `### [P<n>]`
    block per reviewed paper (the shelf counts these) with its verified facts."""
    zh = lang is Lang.ZH
    read = sum(1 for s in sources if s.fulltext is not None)
    total = len(sources) + len(listed)
    caveats = []
    if run["status"] != "done":
        caveats.append(f"运行状态为 {run['status']}，纳入集可能不完整" if zh
                       else f"the run is {run['status']}, so its included set may be incomplete")
    if unscreened:
        caveats.append(f"另有 {unscreened} 篇候选未完成筛选，未计入" if zh
                       else f"{unscreened} more candidates were left unscreened and are not counted")
    caveat = ""
    if caveats:
        caveat = "> 注意：" + "；".join(caveats) + "。\n>\n" if zh else "> Note: " + "; ".join(caveats) + ".\n>\n"
    if zh:
        out = [
            f"# 综述：{_cell(run['question'])}\n\n"
            f"> 问题运行 `{run['run_id']}` · 纳入 {total} 篇，综述其中 {len(sources)} 篇"
            f"（全文 {read} 篇，仅摘要 {len(sources) - read} 篇）· {period}\n>\n{caveat}"
            "> 正文由模型依据下列材料写成，[P编号] 指向文末的论文；带 † 的引用指向只读到摘要的论文。"
            "「核验事实」由模型从全文抽取，每条引文都已由程序与原文逐字核对。\n\n",
        ]
    else:
        out = [
            f"# Survey: {_cell(run['question'])}\n\n"
            f"> Question run `{run['run_id']}` · {total} papers included, {len(sources)} surveyed "
            f"({read} full text, {len(sources) - read} abstract only) · {period}\n>\n{caveat}"
            "> The model wrote the text below from these materials; [P<n>] points to the papers at the end, and "
            "a citation marked † points to a paper read from its abstract alone. Verified facts were extracted "
            "from the full text and each quote was checked against it word for word.\n\n",
        ]
    out.append(body.strip() + "\n\n")
    out.append("## 纳入论文\n\n" if zh else "## Included papers\n\n")
    for s in sources:
        p = s.paper
        out.append(f"### [P{s.n}] {_cell(p['title'])}\n\n")
        meta = " · ".join(_cell(v) for v in (
            str(p.get("year") or ""), p.get("venue") or "", "; ".join((p.get("authors") or [])[:3]),
        ) if v)
        url = p.get("url") or ""
        link = f" · [{'链接' if zh else 'link'}]({url})" if url.startswith(("https://", "http://")) else ""
        out.append(f"{meta + ' · ' if meta else ''}**{_depth(s, zh)}**{link}\n\n")
        if s.card is not None and s.card.fields:
            out.append("核验事实：\n\n" if zh else "Verified facts:\n\n")
            for k, zl, el in FACTS:
                fact = s.card.fields.get(k)
                if fact is not None:
                    out.append(f"- **{zl if zh else el}**{'：' if zh else ': '}{_cell(fact.value)}\n\n"
                               f"{_quote(fact.quote)}\n\n")
    if listed:
        out.append(f"## 未综述的纳入论文（{len(listed)} 篇）\n\n" if zh
                   else f"## Included papers not surveyed ({len(listed)})\n\n")
        for p in listed:
            out.append(f"- {_cell(p['title'])} ({p.get('year') or '-'})\n")
        out.append("\n")
    out.append(f"---\n\n{'生成记录' if zh else 'Generated'}: `{call_id}` · {generated_at}\n")
    return "".join(out)
