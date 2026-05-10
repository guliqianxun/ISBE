"""Generic weekly digester — config-driven, topic-agnostic.

Each digest run:
  1. Reads `papers` rows whose submitted_at is within facts_window_days,
     filtered by topic's keywords (OR over title+abstract).
  2. Optionally reads `repos` (for nowcasting; off for new arxiv-only topics).
  3. Builds prompt with topic label + facts + memory; calls LLM.
  4. Parses three sections (## 事实 / ## 分析 / ## 蒸馏).
  5. Writes artifact + .pending memory drafts.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from jinja2 import Template
from prefect import flow
from sqlalchemy import or_, select

from isbe.artifacts.store import save_artifact
from isbe.facts.db import make_session_factory
from isbe.llm.client import complete
from isbe.llm.prompts import SYSTEM_PROMPT, build_digest_prompt
from isbe.memory.pending import write_pending
from isbe.observability.runs import topic_run
from isbe.topics._shared.digester_utils import (
    build_memory_block,
    memory_root as _memory_root,
    parse_distillation_section,  # noqa: F401  — re-exported for back-compat
    split_sections as _split_sections,
)
from isbe.topics.base import DigestResult, DigestSection
from isbe.topics.nowcasting.facts import Paper, Repo  # shared facts tables
from isbe.topics.registry import default_topics_root, load_topic_config

SHARED_TEMPLATE = Path(__file__).parent / "templates" / "weekly.j2"


def _build_facts_block(papers: list, repos: list | None) -> str:
    lines = [f"近窗 papers ({len(papers)}):"]
    for p in papers:
        lines.append(f"- [{p.arxiv_id}] {p.title} ({p.primary_category}) — {p.source_url}")
    if repos is not None:
        lines.append(f"\nTracked repos ({len(repos)}):")
        for r in repos:
            lines.append(
                f"- {r.title} stars={r.stars} last_commit={r.last_commit_at} — {r.github_url}"
            )
    return "\n".join(lines)


def _papers_keyword_filter(keywords: list[str]):
    """Build a SQLAlchemy OR-condition matching any keyword in title or abstract."""
    if not keywords:
        return None
    clauses = []
    for kw in keywords:
        like = f"%{kw}%"
        clauses.append(Paper.title.ilike(like))
        clauses.append(Paper.abstract.ilike(like))
    return or_(*clauses)


@flow(name="weekly-digester")
def weekly_digester(
    topic_id: str,
    period_label: str | None = None,
    today: date | None = None,
) -> DigestResult:
    """Generic weekly digest flow. Topic config loaded from topic.yaml."""
    today = today or date.today()
    if period_label is None:
        year, week, _ = today.isocalendar()
        period_label = f"{year}-W{week:02d}"

    cfg = load_topic_config(default_topics_root(), topic_id)
    digest_cfg = cfg.get("digest", {})
    arxiv_cfg = cfg.get("arxiv", {})
    label = cfg.get("label", topic_id)
    facts_window_days = int(digest_cfg.get("facts_window_days", 7))
    include_repos = bool(digest_cfg.get("include_repos", False))
    keywords = arxiv_cfg.get("include_keywords", [])

    with topic_run(topic_id, "weekly-digester") as run:
        return _digester_impl(
            topic_id=topic_id,
            topic_label=label,
            period_label=period_label,
            today=today,
            facts_window_days=facts_window_days,
            keywords=keywords,
            include_repos=include_repos,
            run=run,
        )


def _digester_impl(
    *,
    topic_id: str,
    topic_label: str,
    period_label: str,
    today: date,
    facts_window_days: int,
    keywords: list[str],
    include_repos: bool,
    run,
) -> DigestResult:
    cutoff = datetime.combine(
        today - timedelta(days=facts_window_days), datetime.min.time(), tzinfo=UTC
    )

    Session = make_session_factory()
    with Session() as s:
        query = select(Paper).where(Paper.submitted_at >= cutoff)
        kw_filter = _papers_keyword_filter(keywords)
        if kw_filter is not None:
            query = query.where(kw_filter)
        papers = list(s.scalars(query).all())
        repos = list(s.scalars(select(Repo)).all()) if include_repos else None

    facts_block = _build_facts_block(papers, repos)
    mroot = _memory_root()
    memory_block, memory_index = build_memory_block(mroot)

    user_prompt = build_digest_prompt(
        topic_label=topic_label,
        period_label=period_label,
        facts_block=facts_block,
        memory_block=memory_block,
    )
    resp = complete(system=SYSTEM_PROMPT, user=user_prompt)
    parts = _split_sections(resp.text)
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
            "papers": [p.arxiv_id for p in papers],
            "repos": [r.github_url for r in repos] if repos is not None else [],
        },
        "memory": memory_index,
        "trace_id": resp.trace_id,
        "message_id": resp.message_id,
    }

    template = Template(SHARED_TEMPLATE.read_text(encoding="utf-8"))
    rendered = template.render(
        topic_label=topic_label,
        period_label=period_label,
        n_papers=len(papers),
        n_repos=len(repos) if repos is not None else 0,
        memory_refs=", ".join(f"{k}@rev{v}" for k, v in memory_index.items()),
        trace_id=resp.trace_id or "(none)",
        digest_body=resp.text,
        generated_at=datetime.now(UTC).isoformat(),
        artifact_id="(filled below)",
    )

    artifact_id = save_artifact(
        topic_id=topic_id,
        kind="weekly_digest",
        period_label=period_label,
        body_markdown=rendered,
        fingerprint=fingerprint,
        generated_at=datetime.now(UTC),
    )

    run.payload["period_label"] = period_label
    run.payload["n_papers"] = len(papers)
    run.payload["n_repos"] = len(repos) if repos is not None else 0
    run.payload["n_drafts"] = len(drafts)
    run.payload["artifact_id"] = str(artifact_id)
    run.payload["llm_input_tokens"] = resp.input_tokens
    run.payload["llm_output_tokens"] = resp.output_tokens

    return DigestResult(
        topic_id=topic_id,
        period_label=period_label,
        generated_at=datetime.now(UTC),
        sections=sections,
        fingerprint={**fingerprint, "artifact_id": str(artifact_id)},
        pending_drafts=drafts,
    )
