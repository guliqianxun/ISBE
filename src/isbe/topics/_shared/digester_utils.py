"""Pure helpers shared between digester flavors (arxiv-weekly + finance-daily).

No flow decorators, no DB access, no LLM calls — just text processing
and memory loading utilities.
"""
import os
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from isbe.memory.loader import load_index
from isbe.topics.base import PendingMemoryDraft


def facts_window(today: date, *, lookback_days: int) -> tuple[datetime, datetime]:
    """Compute the [low, high] datetime range used to filter facts in a digest.

    Returned pair is intended for `col >= low AND col <= high`. The upper
    bound is end-of-day on `today` (23:59:59.999999 UTC) so same-day items
    are included; without it, items submitted after `today` but before the
    digest actually ran would leak into the report.
    """
    low = datetime.combine(today - timedelta(days=lookback_days), time.min, tzinfo=UTC)
    high = datetime.combine(today, time.max, tzinfo=UTC)
    return low, high

DRAFT_LINE_RE = re.compile(r"^\s*-\s*DRAFT\[([^\]]+)\]:\s*(.+)$")

VALID_TYPE_PREFIXES = {
    "topics": "topic",
    "reading": "reading",
    "feedback": "feedback",
    "user": "user",
    "reference": "reference",
}


def memory_root() -> Path:
    raw = os.getenv("ISBE_MEMORY_ROOT")
    if raw:
        return Path(raw)
    uid = os.getenv("ISBE_UID", "me")
    return Path("memory") / uid


def build_memory_block(
    memory_root_path: Path,
    *,
    topic_id: str | None = None,
) -> tuple[str, dict]:
    """Returns (text_block, {name: revision} index) for relevant memory entries.

    When `topic_id` is given, type=topic entries are filtered to those whose
    `name` equals the topic_id or starts with `<topic_id>.` (sub-document
    convention, e.g. `nowcasting.theses`). Other topics' notes never enter
    the prompt — that's the bug this fixes: cross-topic pollution where a
    video-generation digest sees nowcasting.theses and writes drafts to it.

    type=feedback and type=user are global preferences and always included,
    regardless of topic_id.

    When `topic_id` is None, behavior is the legacy "load all" — kept so
    older test fixtures and any non-digester caller don't break silently.
    """
    index: dict[str, int] = {}
    chunks: list[str] = []
    for entry in load_index(memory_root_path):
        ftype = entry.frontmatter.type
        if ftype.value not in ("topic", "feedback", "user"):
            continue
        if ftype.value == "topic" and topic_id is not None:
            name = entry.frontmatter.name
            if name != topic_id and not name.startswith(f"{topic_id}."):
                continue
        index[entry.frontmatter.name] = entry.frontmatter.revision
        chunks.append(
            f"--- {entry.frontmatter.name}@rev{entry.frontmatter.revision} "
            f"(type={ftype.value}) ---\n{entry.body.strip()}"
        )
    return "\n\n".join(chunks), index


PAPER_REVIEW_RE = re.compile(r"^\s*-\s*\[([0-9.]+(?:v\d+)?)\]\s*:?\s*(.+)$")
REPO_REVIEW_RE = re.compile(r"^\s*-\s*\[([^\]]+)\]\s*:?\s*(.+)$")


def split_sections(text: str) -> dict[str, str]:
    """Split LLM output by markdown level-2 headers into the six known section keys."""
    sections: dict[str, str] = {}
    current_key = None
    buf: list[str] = []
    name_map = {
        "TL;DR": "tldr",
        # arxiv-weekly
        "论文逐篇": "paper_reviews",
        "仓库逐条": "repo_reviews",
        # nvda-daily
        "新闻逐条": "news_reviews",
        "SEC 逐条": "filing_reviews",
        # motorcycle-weekly / china-tech-weekly
        "文章逐条": "article_reviews",
        "品牌动态": "brand_notes",
        # china-tech-weekly
        "公司动态": "company_notes",
        "分析": "analysis",
        "蒸馏": "distillation",
    }
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


def parse_paper_reviews(text: str) -> dict[str, str]:
    """Extract `{arxiv_id: review_text}` from a `## 论文逐篇` block.

    Tolerates either `- [2605.10046] body` or `- [2605.10046]: body`.
    Strips trailing `vN` version suffix to match the bare `arxiv_id` used in DB.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = PAPER_REVIEW_RE.match(line)
        if not m:
            continue
        aid = m.group(1).split("v")[0]
        out[aid] = m.group(2).strip()
    return out


def parse_bracketed_reviews(text: str) -> dict[str, str]:
    """Extract `{key: review_text}` from any `- [<key>] <text>` block.

    Used for `## 仓库逐条` (key = repo title), `## 新闻逐条` (key = news.id sha1),
    `## SEC 逐条` (key = accession_no), `## 文章逐条` (key = article.id[:12]).

    Normalizes a leading `id=` prefix so both `[id=abc123]:` and `[abc123]:`
    map to the same key — the LLM oscillates between the two and getting
    bitten by the former is the root cause of "评价 all —" in article reports.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = REPO_REVIEW_RE.match(line)
        if not m:
            continue
        key = m.group(1).strip()
        if key.startswith("id="):
            key = key[3:]
        out[key] = m.group(2).strip()
    return out


def parse_distillation_section(text: str) -> list[PendingMemoryDraft]:
    """Parse `- DRAFT[<target_path>]: <body>` lines into PendingMemoryDraft objects."""
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
                rationale="extracted from digest distillation section",
            )
        )
    return drafts
