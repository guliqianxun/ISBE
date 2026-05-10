"""Generic weekly digester — config-driven, topic-agnostic.

Each digest run:
  1. Reads `papers` rows whose submitted_at is within facts_window_days,
     filtered by topic's keywords (OR over title+abstract).
  2. Optionally reads `repos` (for nowcasting; off for new arxiv-only topics).
  3. Builds prompt with topic label + facts + memory; calls LLM.
  4. Parses three sections (## 事实 / ## 分析 / ## 蒸馏).
  5. Writes artifact + .pending memory drafts.
"""

import os
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from jinja2 import Template
from prefect import flow
from sqlalchemy import or_, select

from isbe.artifacts.store import save_artifact
from isbe.facts.db import make_session_factory
from isbe.llm.client import complete
from isbe.llm.prompts import SYSTEM_PROMPT, build_digest_prompt
from isbe.memory.loader import load_index
from isbe.memory.pending import write_pending
from isbe.observability.runs import topic_run
from isbe.topics.base import DigestResult, DigestSection, PendingMemoryDraft
from isbe.topics.nowcasting.facts import Paper, Repo  # shared facts tables
from isbe.topics.registry import default_topics_root, load_topic_config

DRAFT_LINE_RE = re.compile(r"^\s*-\s*DRAFT\[([^\]]+)\]:\s*(.+)$")
SHARED_TEMPLATE = Path(__file__).parent / "templates" / "weekly.j2"

VALID_TYPE_PREFIXES = {
    "topics": "topic",
    "reading": "reading",
    "feedback": "feedback",
    "user": "user",
    "reference": "reference",
}


def _memory_root() -> Path:
    raw = os.getenv("ISBE_MEMORY_ROOT")
    if raw:
        return Path(raw)
    uid = os.getenv("ISBE_UID", "me")
    return Path("memory") / uid


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


def _build_memory_block(memory_root: Path) -> tuple[str, dict]:
    index: dict[str, int] = {}
    chunks: list[str] = []
    for entry in load_index(memory_root):
        ftype = entry.frontmatter.type
        if ftype.value not in ("topic", "feedback", "user"):
            continue
        index[entry.frontmatter.name] = entry.frontmatter.revision
        chunks.append(
            f"--- {entry.frontmatter.name}@rev{entry.frontmatter.revision} "
            f"(type={ftype.value}) ---\n{entry.body.strip()}"
        )
    return "\n\n".join(chunks), index


def _split_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_key = None
    buf: list[str] = []
    name_map = {"事实": "facts", "分析": "analysis", "蒸馏": "distillation"}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if current_key is not None:
                sections[current_key] = "\n".join(buf).strip()
            header = stripped[3:].strip()
            current_key = name_map.get(header)
            buf = []
        else:
            if current_key is not None:
                buf.append(line)
    if current_key is not None:
        sections[current_key] = "\n".join(buf).strip()
    return sections


def parse_distillation_section(text: str) -> list[PendingMemoryDraft]:
    import sys

    drafts: list[PendingMemoryDraft] = []
    for line in text.splitlines():
        m = DRAFT_LINE_RE.match(line)
        if not m:
            continue
        target_path = m.group(1).strip()
        content = m.group(2).strip()

        prefix = target_path.split("/", 1)[0]
        if prefix not in VALID_TYPE_PREFIXES:
            print(f"[digester] skip DRAFT (bad prefix '{prefix}'): {target_path}", file=sys.stderr)
            continue
        if not target_path.endswith(".md"):
            print(f"[digester] skip DRAFT (not .md): {target_path}", file=sys.stderr)
            continue
        target_type = VALID_TYPE_PREFIXES[prefix]

        body = (
            f"---\nname: {Path(target_path).stem}\n"
            f"description: agent draft from digest\n"
            f"type: {target_type}\n"
            f"created: {date.today().isoformat()}\n"
            f"updated: {date.today().isoformat()}\n"
            f"source: agent-inferred\n---\n{content}\n"
        )
        drafts.append(
            PendingMemoryDraft(
                target_type=target_type,
                target_path=target_path,
                body=body,
                rationale="extracted from weekly digest distillation section",
            )
        )
    return drafts


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
    memory_root = _memory_root()
    memory_block, memory_index = _build_memory_block(memory_root)

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
        write_pending(memory_root, d)

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
