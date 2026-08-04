"""Generic weekly digester — config-driven, topic-agnostic.

Each digest run:
  1. Reads `papers` rows whose submitted_at is within facts_window_days,
     filtered by topic's keywords (OR over title+abstract).
  2. Optionally reads `repos` (for nowcasting; off for new arxiv-only topics).
  3. Builds prompt with topic label + facts + memory; calls LLM.
  4. Parses three sections (## 事实 / ## 分析 / ## 蒸馏).
  5. Writes artifact + .pending memory drafts.
"""

import base64
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

from jinja2 import Template
from prefect import flow
from sqlalchemy import select

from isbe.artifacts.store import save_artifact
from isbe.facts.db import make_session_factory
from isbe.llm.client import complete
from isbe.llm.prompts import SYSTEM_PROMPT, build_digest_prompt
from isbe.memory.pending import write_pending
from isbe.notify import send_digest_notification
from isbe.observability.runs import topic_run
from isbe.topics._shared.arxiv import papers_keyword_filter
from isbe.topics._shared.comparison import build_comparison, load_prior_artifact
from isbe.topics._shared.digester_utils import (
    apply_triage,
    build_memory_block,
    facts_window,
    paper_to_item,
    parse_bracketed_reviews,
    parse_distillation_section,  # noqa: F401  — re-exported for back-compat
    parse_glossary,
    parse_paper_blocks,
    parse_paper_reviews,  # noqa: F401  — re-exported for back-compat
    s2paper_to_digest_row,
)
from isbe.topics._shared.digester_utils import (
    memory_root as _memory_root,
)
from isbe.topics._shared.digester_utils import (
    split_sections as _split_sections,
)
from isbe.topics.base import DigestResult, DigestSection
from isbe.topics.nowcasting.facts import Paper, Repo  # shared facts tables
from isbe.topics.registry import default_topics_root, load_topic_config
from isbe.triage import RetrievalContract
from isbe.triage.contract import contract_from_config
from isbe.triage.pipeline import acquire_by_source

SHARED_TEMPLATE = Path(__file__).parent / "templates" / "weekly.j2"


def _load_fulltext(paper) -> str | None:
    """Read the metrail-extracted markdown for a paper, or None.

    fulltext_uri is stored relative to the papers mirror root; a wrong or
    missing mirror silently yields None — the digest must never fail because
    an extraction file is absent (fail-open to abstract-only).
    """
    uri = getattr(paper, "fulltext_uri", None)
    if not uri:
        return None
    path = Path(os.getenv("ISBE_PAPERS_MIRROR", "papers")) / uri
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        # ValueError covers UnicodeDecodeError from a torn concurrent write
        # (enrich uses non-atomic write_text on a NAS mount) — fail open.
        return None


_MAX_FIGURE_BYTES = 500_000


def _load_paper_assets(papers: list) -> dict:
    """Collect per-paper assets saved by metrail_enrich: the framework figure
    and the paper's core result/ablation tables (verbatim GFM markdown).

    Returns the weekly.j2 `paper_assets` shape:
    {arxiv_id: {"figure": {"caption", "datauri"}, "tables": [{"caption","markdown"}]}}.
    Fail-open everywhere — a missing/oversized/corrupt asset just means that
    asset is absent for that paper.
    """
    assets: dict = {}
    mirror = Path(os.getenv("ISBE_PAPERS_MIRROR", "papers"))
    for p in papers:
        uri = getattr(p, "fulltext_uri", None)
        if not uri:
            continue
        md_path = mirror / uri
        fig_path = md_path.with_name(md_path.name.replace(".metrail.md", ".metrail.fig.png"))
        meta_path = md_path.with_name(md_path.name.replace(".metrail.md", ".metrail.assets.json"))
        try:
            meta: dict = {}
            if meta_path.is_file():
                loaded = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    meta = loaded
            entry: dict = {}
            if fig_path.is_file():
                png = fig_path.read_bytes()
                if png and len(png) <= _MAX_FIGURE_BYTES:
                    caption = (meta.get("figure") or {}).get("caption") or meta.get("caption", "")
                    entry["figure"] = {
                        "caption": caption or "框架图（自动提取）",
                        "datauri": "data:image/png;base64,"
                        + base64.b64encode(png).decode("ascii"),
                    }
            tables = [
                t
                for t in (meta.get("tables") or [])
                if isinstance(t, dict) and t.get("markdown")
            ]
            if tables:
                entry["tables"] = tables
            if entry:
                assets[p.arxiv_id] = entry
        except (OSError, ValueError):
            continue
    return assets


def _build_facts_block(
    papers: list,
    repos: list | None,
    *,
    include_abstract: bool = True,
    fulltext_for=None,
    per_paper_chars: int = 3000,
    total_chars: int = 24000,
) -> str:
    """Render papers (+repos) for the prompt.

    The SYSTEM_PROMPT grounds every field in "what the abstract says" — so the
    abstract must actually be in the prompt (it wasn't, historically). Fulltext
    excerpts are additive and budgeted: per_paper_chars caps each paper,
    total_chars caps the sum; when the budget runs out later papers keep their
    abstract but lose the excerpt.
    """
    lines = [f"近窗 papers ({len(papers)}):"]
    fulltext_budget = total_chars
    for p in papers:
        lines.append(f"- [{p.arxiv_id}] {p.title} ({p.primary_category}) — {p.source_url}")
        if include_abstract:
            abstract = (getattr(p, "abstract", "") or "").strip()
            if abstract:
                lines.append(f"  摘要: {abstract}")
        if fulltext_for is not None and fulltext_budget > 0:
            fulltext = fulltext_for(p)
            if fulltext:
                excerpt = fulltext[: min(per_paper_chars, fulltext_budget)].strip()
                if excerpt:
                    lines.append(f"  全文摘录: {excerpt}")
                    fulltext_budget -= len(excerpt)
    if repos is not None:
        lines.append(f"\nTracked repos ({len(repos)}):")
        for r in repos:
            lines.append(
                f"- {r.title} stars={r.stars} last_commit={r.last_commit_at} — {r.github_url}"
            )
    return "\n".join(lines)


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
    include_abstract = bool(digest_cfg.get("include_abstract", True))
    fulltext_per_paper_chars = int(digest_cfg.get("fulltext_per_paper_chars", 3000))
    fulltext_total_chars = int(digest_cfg.get("fulltext_total_chars", 24000))
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
            include_abstract=include_abstract,
            fulltext_per_paper_chars=fulltext_per_paper_chars,
            fulltext_total_chars=fulltext_total_chars,
            contract=contract_from_config(cfg),
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
    include_abstract: bool = True,
    fulltext_per_paper_chars: int = 3000,
    fulltext_total_chars: int = 24000,
    contract: RetrievalContract | None = None,
    run=None,
) -> DigestResult:
    cutoff_low, cutoff_high = facts_window(today, lookback_days=facts_window_days)

    Session = make_session_factory()
    with Session() as s:
        if contract is not None and contract.source in ("local", "s2"):
            # 外部源（local=本机每日 papers.db / s2=Semantic Scholar API）：digester 直接取数，
            # 不走 ISBE Postgres facts。⚠ 需对应源可达 + 服务器栈 smoke。
            # 服务器默认走下面 facts 分支（自己 arxiv 采集填 Postgres）。
            raw, _ = acquire_by_source(
                contract, reference_date=today, since_days=facts_window_days, limit=500, log=print,
            )
            papers = [s2paper_to_digest_row(p) for p in raw]
        else:
            # facts（默认）：读 ISBE Postgres（服务器自己 arxiv 采集填充）。
            # 产线 topic.yaml 无 `retrieval:` 块 → contract=None → triage 直通，行为不变。
            query = select(Paper).where(
                Paper.submitted_at >= cutoff_low, Paper.submitted_at <= cutoff_high
            )
            kw_filter = papers_keyword_filter(keywords)
            if kw_filter is not None:
                query = query.where(kw_filter)
            papers = list(s.scalars(query).all())
        # FT triage：契约缺省（当前所有产线域）→ 直通全留，行为不变。
        n_pre_triage = len(papers)
        papers, triage_result = apply_triage(papers, contract, paper_to_item)
        repos = list(s.scalars(select(Repo)).all()) if include_repos else None
        prior_artifact = load_prior_artifact(s, topic_id, period_label)
        comparison = build_comparison(
            prior_artifact,
            {"papers": {p.arxiv_id for p in papers}},
            bucket_labels={"papers": "论文"},
            compare_label="上周对比",
            repos=repos,
        )

    facts_block = _build_facts_block(
        papers,
        repos,
        include_abstract=include_abstract,
        fulltext_for=_load_fulltext,
        per_paper_chars=fulltext_per_paper_chars,
        total_chars=fulltext_total_chars,
    )
    mroot = _memory_root()
    memory_block, memory_index = build_memory_block(mroot, topic_id=topic_id)

    user_prompt = build_digest_prompt(
        topic_label=topic_label,
        period_label=period_label,
        facts_block=facts_block,
        memory_block=memory_block,
    )
    resp = complete(system=SYSTEM_PROMPT, user=user_prompt)
    parts = _split_sections(resp.text)
    sections = [
        DigestSection(kind="tldr", body=parts.get("tldr", "")),
        DigestSection(kind="paper_reviews", body=parts.get("paper_reviews", "")),
        DigestSection(kind="repo_reviews", body=parts.get("repo_reviews", "")),
        DigestSection(kind="analysis", body=parts.get("analysis", "")),
        DigestSection(kind="distillation", body=parts.get("distillation", "")),
    ]
    drafts = parse_distillation_section(parts.get("distillation", ""))
    paper_blocks = parse_paper_blocks(parts.get("paper_reviews", ""))
    glossary = parse_glossary(parts.get("glossary", ""))
    repo_reviews = parse_bracketed_reviews(parts.get("repo_reviews", ""))
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
        topic_id=topic_id,
        topic_label=topic_label,
        period_label=period_label,
        tldr=parts.get("tldr", "").strip(),
        analysis=parts.get("analysis", "").strip(),
        distillation=parts.get("distillation", "").strip(),
        memory_refs=", ".join(f"{k}@rev{v}" for k, v in memory_index.items()),
        trace_id=resp.trace_id or "(none)",
        papers=papers,
        paper_blocks=paper_blocks,
        paper_assets=_load_paper_assets(papers),
        glossary=glossary,
        repos=repos or [],
        repo_reviews=repo_reviews,
        comparison=comparison,
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
    if triage_result is not None:
        run.payload["triage"] = {
            "kept": len(papers),
            "dropped": len(triage_result.dropped),
            "pre_triage": n_pre_triage,
        }
    run.payload["n_repos"] = len(repos) if repos is not None else 0
    # Parse-yield: detect a silent LLM-format regression (e.g. whole 论文逐篇
    # section drifting off-contract → bare title+abstract cards) without
    # inspecting artifacts. n_paper_blocks << n_papers signals a parse miss.
    run.payload["n_paper_blocks"] = len(paper_blocks)
    run.payload["n_blocks_with_sota"] = sum(1 for b in paper_blocks.values() if b.sota)
    run.payload["n_glossary"] = len(glossary)
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
