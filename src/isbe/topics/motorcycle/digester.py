"""Motorcycle weekly digester — reads articles, writes structured weekly report.

Same overall shape as arxiv-weekly: 5-section LLM contract, paper-card-style
article rendering, period-over-period comparison via the generic strategy.
"""
from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from jinja2 import Template
from prefect import flow
from sqlalchemy import select

from isbe.artifacts.store import save_artifact
from isbe.facts.articles import Article
from isbe.facts.db import make_session_factory
from isbe.llm.client import complete
from isbe.llm.motorcycle_prompts import MOTORCYCLE_SYSTEM_PROMPT, build_motorcycle_prompt
from isbe.memory.pending import write_pending
from isbe.notify import send_digest_notification
from isbe.observability.runs import topic_run
from isbe.topics._shared.comparison import build_comparison, load_prior_artifact
from isbe.topics._shared.digester_utils import (
    build_memory_block,
    memory_root,
    parse_bracketed_reviews,
    parse_distillation_section,
    split_sections,
)
from isbe.topics.base import DigestResult, DigestSection
from isbe.topics.registry import default_topics_root, load_topic_config

TOPIC_ID = "motorcycle"
TEMPLATE_PATH = Path(__file__).parent / "templates" / "weekly.j2"


def _build_facts_block(articles: list) -> str:
    if not articles:
        return "(本周无文章)"
    lines = [f"=== Articles ({len(articles)}) ==="]
    for a in articles:
        snippet = (a.summary or "").replace("\n", " ")[:280]
        lines.append(
            f"- [id={a.id[:12]}] [{a.source}] {a.published_at:%Y-%m-%d} "
            f"{a.headline}\n  {snippet}"
        )
    return "\n".join(lines)


@flow(name="motorcycle-digester")
def motorcycle_digester(
    period_label: str | None = None,
    today: date | None = None,
) -> DigestResult:
    today = today or date.today()
    if period_label is None:
        year, week, _ = today.isocalendar()
        period_label = f"{year}-W{week:02d}"

    cfg = load_topic_config(default_topics_root(), TOPIC_ID)
    dcfg = cfg.get("digest") or {}
    facts_window = int(dcfg.get("facts_window_days", 7))
    label = cfg.get("label", TOPIC_ID)

    with topic_run(TOPIC_ID, "motorcycle-digester") as run:
        return _impl(
            topic_label=label,
            period_label=period_label,
            today=today,
            facts_window=facts_window,
            run=run,
        )


def _impl(
    *,
    topic_label: str,
    period_label: str,
    today: date,
    facts_window: int,
    run,
) -> DigestResult:
    cutoff = datetime.combine(
        today - timedelta(days=facts_window), datetime.min.time(), tzinfo=UTC
    )

    Session = make_session_factory()
    with Session() as s:
        articles = list(s.scalars(
            select(Article).where(
                Article.topic_id == TOPIC_ID,
                Article.published_at >= cutoff,
            ).order_by(Article.published_at.desc())
        ).all())
        prior_artifact = load_prior_artifact(s, TOPIC_ID, period_label)
        comparison = build_comparison(
            prior_artifact,
            {"articles": {a.id for a in articles}},
            bucket_labels={"articles": "文章"},
            compare_label="上周对比",
        )

    facts_block = _build_facts_block(articles)
    mroot = memory_root()
    memory_block, memory_index = build_memory_block(mroot)

    user_prompt = build_motorcycle_prompt(
        topic_label=topic_label,
        period_label=period_label,
        facts_block=facts_block,
        memory_block=memory_block,
    )
    resp = complete(system=MOTORCYCLE_SYSTEM_PROMPT, user=user_prompt)
    parts = split_sections(resp.text)
    sections = [
        DigestSection(kind="tldr", body=parts.get("tldr", "")),
        DigestSection(kind="article_reviews", body=parts.get("article_reviews", "")),
        DigestSection(kind="brand_notes", body=parts.get("brand_notes", "")),
        DigestSection(kind="analysis", body=parts.get("analysis", "")),
        DigestSection(kind="distillation", body=parts.get("distillation", "")),
    ]
    drafts = parse_distillation_section(parts.get("distillation", ""))
    for d in drafts:
        write_pending(mroot, d)

    # LLM keys on id[:12] (matching facts_block); template needs full id → reuse 12-prefix dict.
    article_reviews_raw = parse_bracketed_reviews(parts.get("article_reviews", ""))
    article_reviews: dict[str, str] = {}
    for a in articles:
        key12 = a.id[:12]
        if key12 in article_reviews_raw:
            article_reviews[a.id] = article_reviews_raw[key12]

    fingerprint = {
        "facts": {"articles": [a.id for a in articles]},
        "memory": memory_index,
        "trace_id": resp.trace_id,
        "message_id": resp.message_id,
    }

    template = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))
    rendered = template.render(
        topic_id=TOPIC_ID,
        topic_label=topic_label,
        period_label=period_label,
        tldr=parts.get("tldr", "").strip(),
        analysis=parts.get("analysis", "").strip(),
        brand_notes=parts.get("brand_notes", "").strip(),
        distillation=parts.get("distillation", "").strip(),
        articles=articles,
        article_reviews=article_reviews,
        comparison=comparison,
        memory_refs=", ".join(f"{k}@rev{v}" for k, v in memory_index.items()),
        trace_id=resp.trace_id or "(none)",
        generated_at=datetime.now(UTC).isoformat(),
        artifact_id="(filled below)",
    )

    artifact_id = save_artifact(
        topic_id=TOPIC_ID,
        kind="weekly_digest",
        period_label=period_label,
        body_markdown=rendered,
        fingerprint=fingerprint,
        generated_at=datetime.now(UTC),
    )

    run.payload["period_label"] = period_label
    run.payload["n_articles"] = len(articles)
    run.payload["n_drafts"] = len(drafts)
    run.payload["artifact_id"] = str(artifact_id)
    run.payload["llm_input_tokens"] = resp.input_tokens
    run.payload["llm_output_tokens"] = resp.output_tokens

    mirror_root = Path(os.getenv("ISBE_ARTIFACT_MIRROR", "artifacts"))
    latest = mirror_root / TOPIC_ID / period_label / "latest.md"
    excerpt = (resp.text or "")[:800]
    pushed = send_digest_notification(
        topic_label=topic_label,
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
