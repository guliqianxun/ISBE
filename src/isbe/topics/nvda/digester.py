"""NVDA daily digester — finance-flavored, three-section artifact.

Reads:
  - prices_daily within the last `digest.facts_window_days` days
  - news_items within last `digest.news_window_days` days
  - sec_filings within last `digest.filings_window_days` days
  - memory entries (topic / feedback / user)

Calls the LLM with FINANCE_SYSTEM_PROMPT; parses three sections; writes
artifact + .pending memory drafts.
"""
from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Template
from prefect import flow
from sqlalchemy import select

from isbe.artifacts.store import save_artifact
from isbe.facts.db import make_session_factory
from isbe.llm.client import complete
from isbe.llm.finance_prompts import FINANCE_SYSTEM_PROMPT, build_finance_prompt
from isbe.memory.pending import write_pending
from isbe.notify import send_digest_notification
from isbe.observability.runs import topic_run
from isbe.topics._shared.comparison import build_comparison, load_prior_artifact
from isbe.topics._shared.digester_utils import (
    build_memory_block,
    facts_window,
    memory_root,
    parse_bracketed_reviews,
    parse_distillation_section,
    split_sections,
)
from isbe.topics.base import DigestResult, DigestSection
from isbe.topics.nvda.facts import NewsItem, PriceDaily, SecFiling
from isbe.topics.registry import default_topics_root, load_topic_config

TOPIC_ID = "nvda"
TEMPLATE_PATH = Path(__file__).parent / "templates" / "daily.j2"

# Canonical entry point picked up by topics.dispatch (see PR #1). Assigned at
# the bottom of this module after daily_digester is defined.


def _build_facts_block(
    prices: list, news: list, filings: list, today: date
) -> str:
    lines: list[str] = []
    lines.append(f"=== Prices (close on {today}) ===")
    by_sym: dict[str, list] = {}
    for p in prices:
        by_sym.setdefault(p.symbol, []).append(p)
    for sym, rows in by_sym.items():
        rows.sort(key=lambda r: r.trade_date)
        latest = rows[-1]
        prev = rows[-2] if len(rows) >= 2 else None
        if prev:
            chg_pct = (latest.close - prev.close) / prev.close * 100
            lines.append(
                f"- {sym}: ${latest.close:.2f} ({chg_pct:+.2f}%) "
                f"vol={latest.volume:,}"
            )
        else:
            lines.append(f"- {sym}: ${latest.close:.2f} vol={latest.volume:,}")

    lines.append(f"\n=== News ({len(news)}) ===")
    for n in news[:20]:  # cap to keep prompt size sane
        lines.append(
            f"- [id={n.id}] [{n.source}] {n.published_at:%Y-%m-%d %H:%M} {n.headline}"
        )

    lines.append(f"\n=== SEC filings ({len(filings)}) ===")
    for f in filings[:10]:
        lines.append(
            f"- [accession={f.accession_no}] {f.form_type} ({f.ticker}) "
            f"filed {f.filed_at:%Y-%m-%d}: {f.body_url}"
        )

    return "\n".join(lines)


def _session_label(period_label: str) -> str:
    """Pretty 'session' marker — for now always '盘后' since we only have after-close digest."""
    return "盘后"


@flow(name="nvda-daily-digester")
def daily_digester(
    period_label: str | None = None,
    today: date | None = None,
) -> DigestResult:
    today = today or date.today()
    period_label = period_label or today.isoformat()

    cfg = load_topic_config(default_topics_root(), TOPIC_ID)
    dcfg = cfg.get("digest", {}) or {}
    facts_window_days = int(dcfg.get("facts_window_days", 1))
    news_window = int(dcfg.get("news_window_days", 3))
    filings_window = int(dcfg.get("filings_window_days", 7))

    with topic_run(TOPIC_ID, "nvda-daily-digester") as run:
        return _impl(
            today=today,
            period_label=period_label,
            facts_window_days=facts_window_days,
            news_window=news_window,
            filings_window=filings_window,
            run=run,
        )


def _impl(
    *,
    today: date,
    period_label: str,
    facts_window_days: int,
    news_window: int,
    filings_window: int,
    run,
) -> DigestResult:
    # Prices: window extends back further so prev-close lookups have data; upper
    # bound is today (date column) so future-dated rows can't leak in.
    price_cutoff_low = today - timedelta(days=facts_window_days + 5)
    news_low, news_high = facts_window(today, lookback_days=news_window)
    filings_low, filings_high = facts_window(today, lookback_days=filings_window)

    Session = make_session_factory()
    with Session() as s:
        prices = list(s.scalars(
            select(PriceDaily).where(
                PriceDaily.trade_date >= price_cutoff_low,
                PriceDaily.trade_date <= today,
            )
        ).all())
        news = list(s.scalars(
            select(NewsItem).where(
                NewsItem.published_at >= news_low,
                NewsItem.published_at <= news_high,
            )
        ).all())
        filings = list(s.scalars(
            select(SecFiling).where(
                SecFiling.filed_at >= filings_low,
                SecFiling.filed_at <= filings_high,
            )
        ).all())
        prior_artifact = load_prior_artifact(s, TOPIC_ID, period_label)
        comparison = build_comparison(
            prior_artifact,
            {
                "news": {n.id for n in news},
                "filings": {f.accession_no for f in filings},
            },
            bucket_labels={"news": "新闻", "filings": "SEC 文件"},
            compare_label="上日对比",
        )

    facts_block = _build_facts_block(prices, news, filings, today)
    mroot = memory_root()
    memory_block, memory_index = build_memory_block(mroot)

    user_prompt = build_finance_prompt(
        period_label=period_label,
        facts_block=facts_block,
        memory_block=memory_block,
    )
    resp = complete(system=FINANCE_SYSTEM_PROMPT, user=user_prompt)
    parts = split_sections(resp.text)
    sections = [
        DigestSection(kind="tldr", body=parts.get("tldr", "")),
        DigestSection(kind="news_reviews", body=parts.get("news_reviews", "")),
        DigestSection(kind="filing_reviews", body=parts.get("filing_reviews", "")),
        DigestSection(kind="analysis", body=parts.get("analysis", "")),
        DigestSection(kind="distillation", body=parts.get("distillation", "")),
    ]
    drafts = parse_distillation_section(parts.get("distillation", ""))
    news_reviews = parse_bracketed_reviews(parts.get("news_reviews", ""))
    filing_reviews = parse_bracketed_reviews(parts.get("filing_reviews", ""))
    for d in drafts:
        write_pending(mroot, d)

    fingerprint = {
        "facts": {
            "prices": [(p.symbol, str(p.trade_date)) for p in prices],
            "news": [n.id for n in news],
            "filings": [f.accession_no for f in filings],
        },
        "memory": memory_index,
        "trace_id": resp.trace_id,
        "message_id": resp.message_id,
    }

    # Compose prices_by_symbol = latest row + day-over-day delta — shaped for template.
    prices_by_symbol: dict = {}
    by_sym: dict[str, list] = {}
    for p in prices:
        by_sym.setdefault(p.symbol, []).append(p)
    for sym, rows in by_sym.items():
        rows.sort(key=lambda r: r.trade_date)
        latest = rows[-1]
        prev = rows[-2] if len(rows) >= 2 else None
        chg_pct = ((latest.close - prev.close) / prev.close * 100) if prev else None
        prices_by_symbol[sym] = SimpleNamespace(
            close=latest.close, volume=latest.volume, chg_pct=chg_pct, date=str(latest.trade_date),
        )

    template = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))
    rendered = template.render(
        period_label=period_label,
        session_label=_session_label(period_label),
        tldr=parts.get("tldr", "").strip(),
        analysis=parts.get("analysis", "").strip(),
        distillation=parts.get("distillation", "").strip(),
        prices_by_symbol=prices_by_symbol,
        news=news,
        news_reviews=news_reviews,
        filings=filings,
        filing_reviews=filing_reviews,
        comparison=comparison,
        memory_refs=", ".join(f"{k}@rev{v}" for k, v in memory_index.items()),
        trace_id=resp.trace_id or "(none)",
        generated_at=datetime.now(UTC).isoformat(),
        artifact_id="(filled below)",
    )

    artifact_id = save_artifact(
        topic_id=TOPIC_ID,
        kind="daily_digest",
        period_label=period_label,
        body_markdown=rendered,
        fingerprint=fingerprint,
        generated_at=datetime.now(UTC),
    )

    run.payload["period_label"] = period_label
    run.payload["n_prices"] = len(prices)
    run.payload["n_news"] = len(news)
    run.payload["n_filings"] = len(filings)
    run.payload["n_drafts"] = len(drafts)
    run.payload["artifact_id"] = str(artifact_id)
    run.payload["llm_input_tokens"] = resp.input_tokens
    run.payload["llm_output_tokens"] = resp.output_tokens

    mirror_root = Path(os.getenv("ISBE_ARTIFACT_MIRROR", "artifacts"))
    latest = mirror_root / TOPIC_ID / period_label / "latest.md"
    excerpt = (resp.text or "")[:800]
    pushed = send_digest_notification(
        topic_label=f"NVDA 日报 {_session_label(period_label)}",
        period_label=period_label,
        artifact_path=latest if latest.exists() else None,
        excerpt=excerpt,
    )
    run.payload["notify_sent"] = pushed

    return DigestResult(
        topic_id=TOPIC_ID,
        period_label=period_label,
        generated_at=datetime.now(UTC),
        sections=sections,
        fingerprint={**fingerprint, "artifact_id": str(artifact_id)},
        pending_drafts=drafts,
    )


digest = daily_digester
