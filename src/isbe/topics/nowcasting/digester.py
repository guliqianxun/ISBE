import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Template
from prefect import flow
from sqlalchemy import select

from isbe.artifacts.store import save_artifact
from isbe.facts.db import make_session_factory
from isbe.llm.client import complete
from isbe.llm.prompts import SYSTEM_PROMPT, build_digest_prompt
from isbe.memory.loader import load_index
from isbe.memory.pending import write_pending
from isbe.topics.base import (
    DigestResult,
    DigestSection,
    PendingMemoryDraft,
)
from isbe.topics.nowcasting.facts import Paper, Repo

TOPIC_ID = "nowcasting"
TOPIC_LABEL = "Nowcasting research subscription"

DRAFT_LINE_RE = re.compile(r"^\s*-\s*DRAFT\[([^\]]+)\]:\s*(.+)$")


def _memory_root() -> Path:
    raw = os.getenv("ISBE_MEMORY_ROOT")
    if raw:
        return Path(raw)
    uid = os.getenv("ISBE_UID", "me")
    return Path("memory") / uid


def _build_facts_block(papers: list, repos: list) -> str:
    lines = [f"近 7 天 papers ({len(papers)}):"]
    for p in papers:
        lines.append(f"- [{p.arxiv_id}] {p.title} ({p.primary_category}) — {p.source_url}")
    lines.append(f"\nTracked repos ({len(repos)}):")
    for r in repos:
        lines.append(f"- {r.title} stars={r.stars} last_commit={r.last_commit_at} — {r.github_url}")
    return "\n".join(lines)


def _build_memory_block(memory_root: Path) -> tuple[str, dict]:
    """Returns (text_block, {name: revision} index)."""
    index = {}
    chunks = []
    for entry in load_index(memory_root):
        ftype = entry.frontmatter.type
        # Only include topic-relevant types for digest context
        if ftype.value not in ("topic", "feedback", "user"):
            continue
        index[entry.frontmatter.name] = entry.frontmatter.revision
        chunks.append(
            f"--- {entry.frontmatter.name}@rev{entry.frontmatter.revision} "
            f"(type={ftype.value}) ---\n{entry.body.strip()}"
        )
    return "\n\n".join(chunks), index


def _split_sections(text: str) -> dict[str, str]:
    """Split LLM output by '## 事实' / '## 分析' / '## 蒸馏' headers."""
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
    drafts: list[PendingMemoryDraft] = []
    for line in text.splitlines():
        m = DRAFT_LINE_RE.match(line)
        if not m:
            continue
        target_path = m.group(1).strip()
        content = m.group(2).strip()
        target_type = target_path.split("/")[0].rstrip("s")  # "topics"->"topic", "reading"->"reading"
        if target_type == "topic":
            target_type = "topic"
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


@flow(name="nowcasting-weekly-digester")
def weekly_digester(period_label: str, today: date | None = None) -> DigestResult:
    today = today or date.today()
    cutoff = datetime.combine(today - timedelta(days=7), datetime.min.time(), tzinfo=timezone.utc)

    Session = make_session_factory()
    with Session() as s:
        papers = list(s.scalars(select(Paper).where(Paper.submitted_at >= cutoff)).all())
        repos = list(s.scalars(select(Repo)).all())

    facts_block = _build_facts_block(papers, repos)
    memory_root = _memory_root()
    memory_block, memory_index = _build_memory_block(memory_root)

    user_prompt = build_digest_prompt(
        topic_label=TOPIC_LABEL,
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
        "facts": {"papers": [p.arxiv_id for p in papers], "repos": [r.github_url for r in repos]},
        "memory": memory_index,
        "trace_id": resp.trace_id,
        "message_id": resp.message_id,
    }

    template = Template((Path(__file__).parent / "templates" / "weekly.j2").read_text(encoding="utf-8"))
    rendered = template.render(
        period_label=period_label,
        n_papers=len(papers),
        n_repos=len(repos),
        memory_refs=", ".join(f"{k}@rev{v}" for k, v in memory_index.items()),
        trace_id=resp.trace_id or "(none)",
        digest_body=resp.text,
        generated_at=datetime.now(timezone.utc).isoformat(),
        artifact_id="(filled below)",
    )

    artifact_id = save_artifact(
        topic_id=TOPIC_ID,
        kind="weekly_digest",
        period_label=period_label,
        body_markdown=rendered,
        fingerprint=fingerprint,
        generated_at=datetime.now(timezone.utc),
    )

    return DigestResult(
        topic_id=TOPIC_ID,
        period_label=period_label,
        generated_at=datetime.now(timezone.utc),
        sections=sections,
        fingerprint={**fingerprint, "artifact_id": str(artifact_id)},
        pending_drafts=drafts,
    )
