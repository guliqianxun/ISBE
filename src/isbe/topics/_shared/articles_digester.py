"""Generic article-based weekly digester factory.

motorcycle and china-tech digesters were 95% identical: same fact source
(Article rows), same memory loop, same template shape, same 5-section LLM
contract. The only differences were:

  - TOPIC_ID, label, flow name
  - The prompt module (system prompt + builder)
  - The name of the 2nd section bucket (brand_notes vs company_notes)
  - The Jinja template path

make_articles_digester collapses both into one parameterized factory. To
add a new article-based topic: write one prompt module + one Jinja template,
then create a 5-line topics/<id>/digester.py that calls the factory.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

from jinja2 import Template
from prefect import flow
from sqlalchemy import select

from isbe.artifacts.store import save_artifact
from isbe.facts.articles import Article
from isbe.facts.db import make_session_factory
from isbe.llm.client import complete
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
from isbe.topics.registry import default_topics_root, load_topic_config_typed


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


def make_articles_digester(
    *,
    topic_id: str,
    flow_name: str,
    system_prompt: str,
    prompt_builder: Callable[..., str],
    second_bucket_kind: str,
    template_path: Path,
):
    """Build a Prefect flow that digests Article rows for one topic.

    Args mirror the differences between motorcycle and china-tech digesters:
    everything else is shared.
    """

    @flow(name=flow_name)
    def _digester(
        period_label: str | None = None,
        today: date | None = None,
    ) -> DigestResult:
        today_local = today or date.today()
        if period_label is None:
            year, week, _ = today_local.isocalendar()
            period_local = f"{year}-W{week:02d}"
        else:
            period_local = period_label

        cfg = load_topic_config_typed(default_topics_root(), topic_id)
        facts_window_days = cfg.digest.facts_window_days
        topic_label = cfg.label

        with topic_run(topic_id, flow_name) as run:
            return _run_impl(
                topic_id=topic_id,
                topic_label=topic_label,
                period_label=period_local,
                today=today_local,
                facts_window_days=facts_window_days,
                system_prompt=system_prompt,
                prompt_builder=prompt_builder,
                second_bucket_kind=second_bucket_kind,
                template_path=template_path,
                run=run,
            )

    # Expose the bucket kind for tests / introspection without poking the closure.
    _digester._isbe_second_bucket_kind = second_bucket_kind  # type: ignore[attr-defined]
    return _digester


def _run_impl(
    *,
    topic_id: str,
    topic_label: str,
    period_label: str,
    today: date,
    facts_window_days: int,
    system_prompt: str,
    prompt_builder: Callable[..., str],
    second_bucket_kind: str,
    template_path: Path,
    run,
) -> DigestResult:
    cutoff_low, cutoff_high = facts_window(today, lookback_days=facts_window_days)

    Session = make_session_factory()
    with Session() as s:
        articles = list(
            s.scalars(
                select(Article)
                .where(
                    Article.topic_id == topic_id,
                    Article.published_at >= cutoff_low,
                    Article.published_at <= cutoff_high,
                )
                .order_by(Article.published_at.desc())
            ).all()
        )
        prior_artifact = load_prior_artifact(s, topic_id, period_label)
        comparison = build_comparison(
            prior_artifact,
            {"articles": {a.id for a in articles}},
            bucket_labels={"articles": "文章"},
            compare_label="上周对比",
        )

    facts_block = _build_facts_block(articles)
    mroot = memory_root()
    memory_block, memory_index = build_memory_block(mroot, topic_id=topic_id)

    user_prompt = prompt_builder(
        topic_label=topic_label,
        period_label=period_label,
        facts_block=facts_block,
        memory_block=memory_block,
    )
    resp = complete(system=system_prompt, user=user_prompt)
    parts = split_sections(resp.text)

    sections = [
        DigestSection(kind="tldr", body=parts.get("tldr", "")),
        DigestSection(kind="article_reviews", body=parts.get("article_reviews", "")),
        DigestSection(kind=second_bucket_kind, body=parts.get(second_bucket_kind, "")),
        DigestSection(kind="analysis", body=parts.get("analysis", "")),
        DigestSection(kind="distillation", body=parts.get("distillation", "")),
    ]

    drafts = parse_distillation_section(parts.get("distillation", ""))
    for d in drafts:
        write_pending(mroot, d)

    # LLM keys reviews on id[:12]; template needs full id → reuse 12-prefix dict.
    reviews_raw = parse_bracketed_reviews(parts.get("article_reviews", ""))
    article_reviews: dict[str, str] = {}
    for a in articles:
        key12 = a.id[:12]
        if key12 in reviews_raw:
            article_reviews[a.id] = reviews_raw[key12]

    fingerprint = {
        "facts": {"articles": [a.id for a in articles]},
        "memory": memory_index,
        "trace_id": resp.trace_id,
        "message_id": resp.message_id,
    }

    template = Template(template_path.read_text(encoding="utf-8"))
    render_kwargs = {
        "topic_id": topic_id,
        "topic_label": topic_label,
        "period_label": period_label,
        "tldr": parts.get("tldr", "").strip(),
        "analysis": parts.get("analysis", "").strip(),
        "distillation": parts.get("distillation", "").strip(),
        "articles": articles,
        "article_reviews": article_reviews,
        "comparison": comparison,
        "memory_refs": ", ".join(f"{k}@rev{v}" for k, v in memory_index.items()),
        "trace_id": resp.trace_id or "(none)",
        "generated_at": datetime.now(UTC).isoformat(),
        "artifact_id": "(filled below)",
        second_bucket_kind: parts.get(second_bucket_kind, "").strip(),
    }
    rendered = template.render(**render_kwargs)

    artifact_id = save_artifact(
        topic_id=topic_id,
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
    latest = mirror_root / topic_id / period_label / "latest.md"
    excerpt = (resp.text or "")[:800]
    pushed = send_digest_notification(
        topic_label=topic_label,
        period_label=period_label,
        artifact_path=latest if latest.exists() else None,
        excerpt=excerpt,
    )
    run.payload["notify_sent"] = pushed

    return DigestResult(
        topic_id=topic_id,
        period_label=period_label,
        generated_at=datetime.now(UTC),
        sections=sections,
        fingerprint={**fingerprint, "artifact_id": str(artifact_id)},
        pending_drafts=drafts,
    )
