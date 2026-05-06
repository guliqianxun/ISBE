from pathlib import Path

from isbe.memory.pending import (
    accept_pending,
    list_pending,
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
