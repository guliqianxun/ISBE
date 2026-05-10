"""Pure helpers shared between digester flavors (arxiv-weekly + finance-daily).

No flow decorators, no DB access, no LLM calls — just text processing
and memory loading utilities.
"""
import os
import re
from datetime import date
from pathlib import Path

from isbe.memory.loader import load_index
from isbe.topics.base import PendingMemoryDraft

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


def build_memory_block(memory_root_path: Path) -> tuple[str, dict]:
    """Returns (text_block, {name: revision} index) for relevant memory entries."""
    index: dict[str, int] = {}
    chunks: list[str] = []
    for entry in load_index(memory_root_path):
        ftype = entry.frontmatter.type
        if ftype.value not in ("topic", "feedback", "user"):
            continue
        index[entry.frontmatter.name] = entry.frontmatter.revision
        chunks.append(
            f"--- {entry.frontmatter.name}@rev{entry.frontmatter.revision} "
            f"(type={ftype.value}) ---\n{entry.body.strip()}"
        )
    return "\n\n".join(chunks), index


def split_sections(text: str) -> dict[str, str]:
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
