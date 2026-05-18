"""Path-traversal containment for memory/pending.py.

Threat model: LLM output (effectively attacker-controlled via prompt
injection through fetched RSS / arxiv text) generates the `target_path`
of a PendingMemoryDraft. If write_pending() resolves that path outside
memory_root, the LLM can overwrite arbitrary files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from isbe.memory.pending import accept_pending, write_pending
from isbe.topics.base import PendingMemoryDraft


def _draft(target_path: str, body: str = "x") -> PendingMemoryDraft:
    return PendingMemoryDraft(
        target_type="topic",
        target_path=target_path,
        body=body,
        rationale="test",
    )


def test_normal_relative_path_succeeds(tmp_path: Path) -> None:
    p = write_pending(tmp_path, _draft("topics/foo.md"))
    assert p.exists()
    assert (tmp_path / ".pending" / "topics" / "foo.md").exists()


def test_deep_relative_path_with_dotdot_inside_root_succeeds(tmp_path: Path) -> None:
    """`a/../b/c.md` resolves to `b/c.md` — legal as long as it stays inside root."""
    p = write_pending(tmp_path, _draft("topics/a/../b/foo.md"))
    assert p.exists()
    assert (tmp_path / ".pending" / "topics" / "b" / "foo.md").exists()


def test_traversal_above_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside memory_root"):
        write_pending(tmp_path, _draft("../../../etc/passwd.md"))


def test_traversal_with_topics_prefix_then_escape_is_rejected(tmp_path: Path) -> None:
    """Prompt-injection style: looks legal at first segment but climbs out via ../."""
    # Need enough `..`s to cross memory_root: .pending → memory_root → above.
    with pytest.raises(ValueError, match="outside memory_root"):
        write_pending(tmp_path, _draft("topics/../../../escaped.md"))


def test_absolute_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside memory_root"):
        write_pending(tmp_path, _draft("/etc/passwd.md"))


def test_accept_pending_also_checks_containment(tmp_path: Path) -> None:
    """accept_pending moves a file from .pending/X into X. If a symlink or
    pre-staged path tries to escape, accept must refuse."""
    pending_root = tmp_path / ".pending"
    pending_root.mkdir()
    rogue = pending_root / "ok.md"
    rogue.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="outside memory_root"):
        # Construct a pending path whose relative-to-.pending escapes upward
        from isbe.memory.pending import PENDING

        bad = tmp_path / PENDING / ".." / "evil.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("x", encoding="utf-8")
        # accept_pending derives a destination via relative_to(memory_root/.pending),
        # which for this path is "../evil.md" → would land outside memory_root.
        accept_pending(tmp_path, bad)
