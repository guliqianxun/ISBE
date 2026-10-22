"""Full text and evidence cards for the papers a report will review
. arXiv HTML or metrail-parsed PDFs supply the text; the
existing fast LLM extracts quoted facts. Missing HTML falls back to abstracts.
This stage alone writes `fulltext`, `cards` and source figures under `figures/`.
"""

import sqlite3
import time
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from isbe.core import db, errors, http, jsonfmt, settings
from isbe.core.llm import Llm
from isbe.core.validators import trim
from isbe.topics import Flavor, Metrail, Topic

from . import arxiv_html, cards, metrail
from .arxiv_html import Paper
from .calls import EnrichError
from .figures import download_html_image, save_html_figure

HTML_TIMEOUT = 45
# Space arXiv requests even when it quickly rejects unavailable HTML.
HTML_SPACING = 3.0

type Load = Callable[[str], Paper]
type Save = Callable[[Sequence[arxiv_html.Figure]], dict[str, Any]]


def run(
    conn: sqlite3.Connection,
    llm: Llm,
    run_id: str,
    topic: Topic,
    data: Path,
    essaies: str | None,
    stats: dict[str, Any],
    *,
    client: http.Http | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Fetch full texts and build cards for `topic`'s next report, recording what
    happened in `stats`; raises `EnrichError` when any paper failed (stats are
    filled first) or the extractor setting is invalid. `client` and `sleep`
    replace the network and the pauses (tests)."""
    if topic.flavor() is not Flavor.PAPERS:
        stats["skipped"] = "not a papers topic"
        return
    # The papers the digest will review: its order and limit.
    since = topic.since(datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    papers = [
        (r[0], r[1], r[2], bool(r[3]))
        for r in conn.execute(
            "SELECT i.id, i.extra, f.via, f.figure IS NOT NULL "
            "FROM triage t JOIN items i ON i.id = t.item_id "
            "LEFT JOIN fulltext f ON f.item_id = i.id "
            "WHERE t.topic_id = ? AND t.verdict = 'kept' AND i.kind = 'paper' "
            "AND i.published_at >= ? "
            "ORDER BY t.tier = 'secondary', i.published_at DESC, i.id LIMIT ?",
            (topic.id, since, topic.digest.max_papers),
        )
    ]
    failed: dict[str, Any] = {}
    cfg = topic.metrail or Metrail()
    base = cfg.base_url if cfg.base_url is not None else settings.get("METRAIL_API_URL")
    if base is not None and not (cfg.enabled and trim(base)):
        base = None
    mode = extractor(settings.get("ISBE_FULLTEXT_EXTRACTOR"), base is not None)
    stats["extractor"] = mode
    stats["papers"] = len(papers)
    if mode == "off":
        stats["skipped"] = "full-text fetching and new cards disabled"
        return
    if mode == "metrail":
        if base is None:
            raise EnrichError(
                "metrail requires METRAIL_API_URL or topic metrail.base_url and metrail.enabled"
            )
        missing = [(p[0], p[1]) for p in papers if p[2] != "metrail" or not p[3]]
        n, figures = metrail.fetch(
            conn, trim(base).rstrip("/"), cfg, data, essaies, missing, failed,
            client=client, sleep=sleep,
        )
        stats["extracted"] = n
        stats["figures"] = figures
    else:
        todo = [(p[0], p[2] is not None) for p in papers
                if p[2] is None or (p[2] == "arxiv-html" and not p[3])]
        fallback: dict[str, Any] = {}
        figure_failures: dict[str, Any] = {}
        with http.Http(HTML_TIMEOUT) if client is None else nullcontext(client) as web:
            n, figures = fetch_html(
                conn, todo, spaced(lambda id: arxiv_html.fetch(web, id), sleep),
                lambda found: save_html_figure(
                    data, found, lambda url: download_html_image(web, url)
                ),
                fallback, figure_failures,
            )
        stats["extracted"] = n
        stats["figures"] = figures
        if fallback:
            stats["abstract_fallback"] = fallback
        if figure_failures:
            stats["figure_failures"] = figure_failures

    built = rejected = 0
    for id, *_ in papers:
        try:
            count = cards.refresh(conn, llm, run_id, topic.lang, id)
        except Exception as error:
            failed[id] = f"card: {errors.describe(error)}"
            continue
        if count is not None:
            built += 1
            rejected += count
    stats["cards"] = built
    stats["rejected_facts"] = rejected
    if failed:
        stats["failed"] = failed
        raise EnrichError(f"{len(failed)} of {len(papers)} papers failed")


def extractor(setting: str | None, has_metrail: bool) -> str:
    """`ISBE_FULLTEXT_EXTRACTOR`; unset, metrail when one is configured, else arXiv HTML."""
    value = trim(setting) if setting is not None else ""
    if not value:
        return "metrail" if has_metrail else "llm"
    if value in ("llm", "metrail", "off"):
        return value
    raise EnrichError("ISBE_FULLTEXT_EXTRACTOR must be llm, metrail or off")


def spaced(load: Load, sleep: Callable[[float], None]) -> Load:
    """`load` with at least `HTML_SPACING` seconds between the starts of two calls."""
    previous: list[float] = []

    def call(id: str) -> Paper:
        if previous:
            sleep(max(0.0, HTML_SPACING - (time.monotonic() - previous[0])))
        previous[:] = [time.monotonic()]
        return load(id)

    return call


def fetch_html(
    conn: sqlite3.Connection,
    papers: Sequence[tuple[str, bool]],
    load: Load,
    save: Save,
    fallback: dict[str, Any],
    figure_failures: dict[str, Any],
) -> tuple[int, int]:
    """Store each paper's HTML text and figure; returns (texts extracted, figures saved).

    Unavailable HTML is an expected abstract fallback (or a figure failure, for
    a paper whose text is already in); storage failures are not, and raise.
    """
    extracted = figures = 0
    for id, has_text in papers:
        try:
            paper = load(id)
        except Exception as error:
            (figure_failures if has_text else fallback)[id] = errors.describe(error)
            continue
        warnings = list(paper.figure_warnings)
        figure: dict[str, Any] | None
        try:
            figure = save(paper.figures)
        except Exception as error:
            warnings.append(errors.describe(error))
            figure = None
        if warnings:
            figure_failures[id] = "; ".join(warnings)
        figures += figure is not None and "files" in figure
        # Adding pictures to cached text must not replace its evidence or invalidate cards.
        conn.execute(
            "INSERT INTO fulltext (item_id, via, body, extracted_at, figure) "
            "VALUES (?, 'arxiv-html', ?, ?, ?) "
            "ON CONFLICT(item_id) DO UPDATE SET figure = excluded.figure "
            "WHERE fulltext.via = 'arxiv-html'",
            (id, paper.body, db.now(), None if figure is None else jsonfmt.dumps(figure)),
        )
        extracted += not has_text
    return extracted, figures
