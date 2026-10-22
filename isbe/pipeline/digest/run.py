"""The report: kept papers + tracked repos + memory → one
LLM call → the five-section markdown file under `reports/`. Sole writer of
digest `reports` rows; memory drafts go to `.pending/` through
`memory.write_draft`.
"""

import logging
import sqlite3
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from isbe import memory
from isbe.core import assets, db, jsonfmt
from isbe.core.errors import describe
from isbe.core.llm import Llm, Prompt, Reply, Tier
from isbe.memory import Note
from isbe.topics import Cadence, Flavor, Lang, Topic

from . import load, parse, render, review, store
from .model import FACTS, Paper, Repo
from .parse import lines

log = logging.getLogger(__name__)


class DigestError(Exception):
    pass


def run(
    conn: sqlite3.Connection,
    llm: Llm,
    run_id: str,
    topic: Topic,
    data: Path,
    stats: dict[str, Any],
) -> None:
    """Write this period's report for `topic` and record it in `reports`.

    Fills `stats` (report, papers, reviewed, listed, fulltext, cards, repos,
    drafts, flagged, second, rewritten) once the report is written, then fails
    if the reply skipped papers or the review did not finish: the report stands,
    but the run says it is incomplete. Raises `store.ReportTaken`, before any
    model call, when another kind of report owns the period's file.
    """
    if topic.flavor() is not Flavor.PAPERS:
        raise DigestError(
            "no report for topics without an arxiv block yet: the articles digest is not built"
        )
    try:
        tz = ZoneInfo(topic.schedule_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise DigestError(f"bad timezone {topic.schedule_timezone}") from None
    now = datetime.now(UTC)
    today = now.astimezone(tz).date()
    name, kind = period(topic.cadence, today)
    start = topic.since(now)
    window = (start.astimezone(tz).date().isoformat(), today.isoformat())
    since = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    # Checked again when writing; refusing here spares the model calls.
    store.check_free(conn, data, topic.id, name, kind)

    papers = load.papers(conn, topic, since)
    shown, listed = papers[: topic.digest.max_papers], papers[topic.digest.max_papers :]
    repos = load.repos(conn, topic, since)
    funnel = load.funnel(conn, topic.id, since)
    mem_root = data / "memory"
    notes, skipped = memory.load(mem_root, topic.id)
    for s in skipped:
        log.warning("memory: skipped %s", s)

    system = assets.text("prompts", topic.lang.value, "digest.md")

    def write(user: str) -> Reply:
        return llm.complete(
            conn,
            run_id,
            Prompt(purpose="digest", tier=Tier.SMART, system=system, user=user, max_tokens=24_000),
        )

    user = prompt_input(topic, name, window, shown, repos, notes)
    first = write(user)
    ids = [p.arxiv_id for p in shown]
    reply, checked = review.run(conn, llm, run_id, topic.lang, user, write, first, ids)
    sections = parse.sections(reply.text)
    blocks = parse.paper_blocks(sections.get("papers", ""))

    drafts: list[tuple[str, str]] = []
    for target, content in parse.drafts(sections.get("drafts", "")):
        origin = f"digest draft: {topic.id} {name}"
        try:
            memory.write_draft(mem_root, target, content, origin, today.isoformat())
        except (OSError, ValueError) as e:
            log.warning("%s: dropped a memory draft: %s", topic.id, describe(e))
        else:
            drafts.append((target, content))

    report_id = f"{topic.id}/{name}/{kind}"
    generated_at = db.now()
    md = render.report(
        render.Input(
            topic=topic,
            period=name,
            window=window,
            sections=sections,
            blocks=blocks,
            shown=shown,
            listed=listed,
            repos=repos,
            funnel=funnel,
            notes=notes,
            drafts=drafts,
            review=checked,
            call_id=reply.call_id,
            report_id=report_id,
            generated_at=generated_at,
        )
    )
    fingerprint = {
        "llm_call": reply.call_id,
        "memory": {n.name: n.revision for n in notes},
        "papers": ids,
        "listed": [p.arxiv_id for p in listed],
        "repos": [r.name for r in repos],
    }
    rel = store.save(
        conn,
        data,
        report_id=report_id,
        topic=topic.id,
        period=name,
        kind=kind,
        body=md,
        fingerprint=jsonfmt.dumps(fingerprint),
        created_at=generated_at,
    )

    reviewed = sum(1 for p in shown if p.arxiv_id in blocks)
    stats.update(
        report=rel,
        papers=len(shown),
        reviewed=reviewed,
        listed=len(listed),
        fulltext=sum(1 for p in shown if p.fulltext is not None),
        cards=sum(1 for p in shown if p.card is not None),
        repos=len(repos),
        drafts=len(drafts),
        flagged=len(checked.first),
        second=None if checked.second is None else len(checked.second),
        rewritten=checked.rewritten,
    )
    # A reply that skipped papers is a format regression worth a failed run.
    if reviewed < len(shown):
        raise DigestError(
            f"the reply reviewed {reviewed} of {len(shown)} papers; "
            "the report lists the rest unreviewed"
        )
    if checked.error is not None:
        raise DigestError(f"reviewer: {checked.error}; the report is unreviewed past that point")


def period(cadence: Cadence, today: date) -> tuple[str, str]:
    """The window ends today, so a weekly report covers yesterday's ISO week: a
    Monday run reports on (and is named after) the week before."""
    if cadence is Cadence.WEEKLY:
        year, week, _ = (today - timedelta(days=1)).isocalendar()
        return f"{year}-W{week:02}", "weekly"
    return today.isoformat(), "daily"


def prompt_input(
    topic: Topic,
    period: str,
    window: tuple[str, str],
    papers: Sequence[Paper],
    repos: Sequence[Repo],
    notes: Sequence[Note],
) -> str:
    zh = topic.lang is Lang.ZH
    if zh:
        s = [
            f"主题：{topic.label}\n周期：{period}\n\n=== Facts (本周期) ===\n"
            f"本期窗口: {window[0]} → {window[1]}（「本期窗口内有推送」以此为准）\n"
            f"近窗 papers ({len(papers)}):\n"
        ]
    else:
        s = [
            f"Topic: {topic.label}\nPeriod: {period}\n\n=== Facts (this period) ===\n"
            f"Window: {window[0]} → {window[1]} (pushed_this_period refers to this window)\n"
            f"Papers ({len(papers)}):\n"
        ]
    budget = topic.digest.fulltext_total_chars
    for p in papers:
        authors = "; ".join(p.authors[:6])
        if len(p.authors) > 6:
            authors += f" 等 {len(p.authors)} 人" if zh else f" et al. ({len(p.authors)})"
        s.append(
            f"- [{p.arxiv_id}] {p.title} ({', '.join(p.categories)}) — {p.url}\n"
            f"  {'作者' if zh else 'Authors'}: {authors}\n"
            f"  {'摘要' if zh else 'Abstract'}: {p.summary}\n"
        )
        if p.card is not None:
            facts = [
                f"{zh_label if zh else en_label}={p.card.fields[k].value}"
                for k, zh_label, en_label in FACTS
                if k in p.card.fields
            ]
            if facts:
                label = "全文事实（已核验）" if zh else "Full-text facts (verified)"
                s.append(f"  {label}: {' | '.join(facts)}\n")
        cap = min(topic.digest.fulltext_per_paper_chars, budget)
        t = tables(p.fulltext or "", cap)
        if t:
            budget -= len(t)
            s.append(
                "  全文表格（PDF 原表）:\n"
                if zh
                else "  Tables from the full text (as parsed from the PDF):\n"
            )
            s.append(t)
    if repos:
        s.append(f"\nTracked repos ({len(repos)}):\n")
        for r in repos:
            pushed = "-" if r.pushed is None else r.pushed
            stars = "-" if r.stars is None else str(r.stars)
            if zh:
                active = "是" if r.active else "否"
                s.append(f"- [{r.name}] stars={stars} 最近推送={pushed} 本期窗口内有推送={active} — {r.url}\n")
            else:
                active = "yes" if r.active else "no"
                s.append(f"- [{r.name}] stars={stars} last_push={pushed} pushed_this_period={active} — {r.url}\n")
    s.append("\n=== Memory (当前) ===\n" if zh else "\n=== Memory (current) ===\n")
    if not notes:
        s.append("(空)\n" if zh else "(empty)\n")
    for n in notes:
        s.append(f"--- {n.name}@rev{n.revision} (type={n.kind}) ---\n{n.body}\n\n")
    s.append(
        "\n请按 system 指令输出六段（## TL;DR / ## 论文逐篇 / ## 仓库逐条 / ## 名词 / ## 分析 / ## 蒸馏）。"
        if zh
        else "\nWrite the six sections from the system prompt (## TL;DR / ## Papers / ## Repos / "
        "## Glossary / ## Analysis / ## Memory drafts)."
    )
    return "".join(s)


def tables(fulltext: str, cap: int) -> str:
    """The tables of a paper's full text (the `### ` blocks `enrich` writes), whole
    lines only, at most `cap` characters; `…` marks a cut."""
    out: list[str] = []
    used = 0
    for block in fulltext.split("\n\n"):
        if not block.startswith("### "):
            continue
        for line in [*lines(block[4:]), ""]:
            n = len(line) + 1
            if used + n + 2 > cap:
                if out:
                    out.append("…\n")
                return "".join(out)
            out.append(line + "\n")
            used += n
    return "".join(out)
