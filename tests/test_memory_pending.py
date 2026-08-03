from datetime import date
from pathlib import Path

import frontmatter

from isbe.memory.pending import (
    accept_pending,
    list_pending,
    merge_memory_texts,
    reject_pending,
    write_pending,
)
from isbe.topics.base import PendingMemoryDraft


def _draft(target_path: str = "topics/nowcasting.theses.md") -> PendingMemoryDraft:
    return PendingMemoryDraft(
        target_type="topic",
        target_path=target_path,
        body="""---
name: nowcasting.theses
description: bull/bear theses for nowcasting
type: topic
created: 2026-05-07
updated: 2026-05-07
source: agent-inferred
---
新论点：diffusion 在 lead-time>90min 仍 mode-collapse""",
        rationale="本期新证据触发",
    )


def test_write_pending_creates_file_under_pending(memory_dir: Path):
    draft = _draft()
    p = write_pending(memory_dir, draft)
    assert p.exists()
    assert p.is_relative_to(memory_dir / ".pending")
    assert p.parts[-2:] == ("topics", "nowcasting.theses.md")


def test_list_pending_returns_all_drafts(memory_dir: Path):
    write_pending(memory_dir, _draft("topics/a.md"))
    write_pending(memory_dir, _draft("feedback/b.md"))
    drafts = list_pending(memory_dir)
    assert len(drafts) == 2


def test_accept_moves_to_real_dir(memory_dir: Path):
    draft = _draft()
    pend = write_pending(memory_dir, draft)
    accepted = accept_pending(memory_dir, pend)
    assert not pend.exists()
    assert accepted.exists()
    assert accepted.parts[-2:] == ("topics", "nowcasting.theses.md")
    assert ".pending" not in accepted.parts


def test_reject_moves_to_audit(memory_dir: Path):
    draft = _draft()
    pend = write_pending(memory_dir, draft)
    rejected = reject_pending(memory_dir, pend)
    assert not pend.exists()
    assert rejected.exists()
    assert ".audit" in rejected.parts and "rejected" in rejected.parts


def test_write_pending_uniquifies_duplicate_target(memory_dir: Path):
    p1 = write_pending(memory_dir, _draft())
    p2 = write_pending(memory_dir, _draft())
    assert p1.exists() and p2.exists()
    assert p1 != p2
    assert p2.name == "nowcasting.theses-2.md"


def test_accept_over_existing_merges_instead_of_overwriting(memory_dir: Path):
    existing = memory_dir / "topics" / "nowcasting.theses.md"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text(
        "---\nname: nowcasting.theses\ndescription: theses\ntype: topic\n"
        "created: 2026-01-01\nupdated: 2026-01-01\nsource: user-edited\nrevision: 3\n---\n"
        "原有论点 A：卷积仍是主流\n",
        encoding="utf-8",
    )
    pend = write_pending(memory_dir, _draft())
    accepted = accept_pending(memory_dir, pend)
    assert not pend.exists()
    post = frontmatter.load(accepted)
    assert post.metadata["revision"] == 4
    assert "原有论点 A" in post.content
    assert "diffusion 在 lead-time>90min" in post.content
    assert "## 合入" in post.content


def test_accept_to_fresh_target_moves_unchanged(memory_dir: Path):
    pend = write_pending(memory_dir, _draft())
    original = pend.read_text(encoding="utf-8")
    accepted = accept_pending(memory_dir, pend)
    assert accepted.read_text(encoding="utf-8") == original


def test_merge_memory_texts_draft_without_frontmatter():
    existing = (
        "---\nname: x\ndescription: d\ntype: topic\ncreated: 2026-01-01\n"
        "updated: 2026-01-01\nsource: user-edited\n---\n旧内容\n"
    )
    merged = merge_memory_texts(existing, "裸草稿内容", today=date(2026, 8, 3))
    post = frontmatter.loads(merged)
    assert post.metadata["revision"] == 2  # missing revision treated as 1
    assert post.metadata["updated"] == "2026-08-03"
    assert "旧内容" in post.content and "裸草稿内容" in post.content
