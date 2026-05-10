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

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from jinja2 import Template
from prefect import flow
from sqlalchemy import select

from isbe.artifacts.store import save_artifact
from isbe.facts.db import make_session_factory
from isbe.llm.client import complete
from isbe.llm.finance_prompts import FINANCE_SYSTEM_PROMPT, build_finance_prompt
from isbe.memory.pending import write_pending
from isbe.observability.runs import topic_run
from isbe.topics._shared.digester_utils import (
    build_memory_block,
    memory_root,
    parse_distillation_section,
    split_sections,
)
from isbe.topics.base import DigestResult, DigestSection
from isbe.topics.nvda.facts import NewsItem, PriceDaily, SecFiling
from isbe.topics.registry import default_topics_root, load_topic_config

TOPIC_ID = "nvda"
TEMPLATE_PATH = Path(__file__).parent / "templates" / "daily.j2"


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
        lines.append(f"- [{n.source}] {n.published_at:%Y-%m-%d %H:%M} {n.headline}")

    lines.append(f"\n=== SEC filings ({len(filings)}) ===")
    for f in filings[:10]:
        lines.append(f"- {f.form_type} ({f.ticker}) filed {f.filed_at:%Y-%m-%d}: {f.body_url}")

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
    facts_window = int(dcfg.get("facts_window_days", 1))
    news_window = int(dcfg.get("news_window_days", 3))
    filings_window = int(dcfg.get("filings_window_days", 7))

    with topic_run(TOPIC_ID, "nvda-daily-digester") as run:
        return _impl(
            today=today,
            period_label=period_label,
            facts_window=facts_window,
            news_window=news_window,
            filings_window=filings_window,
            run=run,
        )


def _impl(
    *,
    today: date,
    period_label: str,
    facts_window: int,
    news_window: int,
    filings_window: int,
    run,
) -> DigestResult:
    price_cutoff = today - timedelta(days=facts_window + 5)  # extra for prev-close lookup
    news_cutoff = datetime.combine(
        today - timedelta(days=news_window), datetime.min.time(), tzinfo=UTC
    )
    filings_cutoff = datetime.combine(
        today - timedelta(days=filings_window), datetime.min.time(), tzinfo=UTC
    )

    Session = make_session_factory()
    with Session() as s:
        prices = list(s.scalars(
            select(PriceDaily).where(PriceDaily.trade_date >= price_cutoff)
        ).all())
        news = list(s.scalars(
            select(NewsItem).where(NewsItem.published_at >= news_cutoff)
        ).all())
        filings = list(s.scalars(
            select(SecFiling).where(SecFiling.filed_at >= filings_cutoff)
        ).all())

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
        DigestSection(kind="facts", body=parts.get("facts", "")),
        DigestSection(kind="analysis", body=parts.get("analysis", "")),
        DigestSection(kind="distillation", body=parts.get("distillation", "")),
    ]
    drafts = parse_distillation_section(parts.get("distillation", ""))
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

    template = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))
    rendered = template.render(
        period_label=period_label,
        session_label=_session_label(period_label),
        n_prices=len(prices),
        n_news=len(news),
        n_filings=len(filings),
        memory_refs=", ".join(f"{k}@rev{v}" for k, v in memory_index.items()),
        trace_id=resp.trace_id or "(none)",
        digest_body=resp.text,
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

    return DigestResult(
        topic_id=TOPIC_ID,
        period_label=period_label,
        generated_at=datetime.now(UTC),
        sections=sections,
        fingerprint={**fingerprint, "artifact_id": str(artifact_id)},
        pending_drafts=drafts,
    )
