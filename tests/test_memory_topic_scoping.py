"""build_memory_block must scope `type=topic` entries to the current topic.

The bug it fixes: a video-generation digest was pulling nowcasting.theses
into its prompt and the LLM then wrote drafts back to nowcasting's memory.
Global types (feedback / user) stay unfiltered.
"""

from __future__ import annotations

from pathlib import Path

from isbe.topics._shared.digester_utils import build_memory_block


def _seed(memory_root: Path) -> None:
    """Create one topic.* file per shipped topic + a global feedback + user file."""
    for sub in ("topics", "feedback", "user"):
        (memory_root / sub).mkdir(parents=True, exist_ok=True)

    def write(rel: str, name: str, ftype: str) -> None:
        (memory_root / rel).write_text(
            "---\n"
            f"name: {name}\n"
            f"description: {name} desc\n"
            f"type: {ftype}\n"
            "created: 2026-05-01\n"
            "updated: 2026-05-01\n"
            "source: user-edited\n"
            "revision: 1\n"
            "---\n"
            f"body of {name}\n",
            encoding="utf-8",
        )

    write("topics/nowcasting.md", "nowcasting", "topic")
    write("topics/nowcasting.theses.md", "nowcasting.theses", "topic")
    write("topics/video-generation.md", "video-generation", "topic")
    write("topics/video-generation.theses.md", "video-generation.theses", "topic")
    write("topics/nvda.md", "nvda", "topic")
    write("feedback/digest_style.md", "digest_style", "feedback")
    write("user/research_focus.md", "research_focus", "user")


def test_topic_scoping_keeps_only_matching_topic_entries(tmp_path: Path) -> None:
    """When called with topic_id='video-generation', topic memory from
    nowcasting/nvda must NOT appear in the block or the index."""
    _seed(tmp_path)
    block, index = build_memory_block(tmp_path, topic_id="video-generation")

    # Block: only video-generation topic.* entries
    assert "video-generation" in block
    assert "video-generation.theses" in block
    assert "nowcasting" not in block
    assert "nvda" not in block

    # Index: same scoping; global types still present
    assert set(index.keys()) == {
        "video-generation",
        "video-generation.theses",
        "digest_style",   # feedback — always loaded
        "research_focus", # user — always loaded
    }


def test_topic_scoping_for_nowcasting(tmp_path: Path) -> None:
    _seed(tmp_path)
    block, index = build_memory_block(tmp_path, topic_id="nowcasting")
    assert "nowcasting" in block and "nowcasting.theses" in block
    assert "video-generation" not in block
    assert "nvda" not in block
    assert "research_focus" in index  # user-type entries still global


def test_feedback_and_user_entries_always_included(tmp_path: Path) -> None:
    """type=feedback and type=user are global preferences — never topic-scoped."""
    _seed(tmp_path)
    _, idx_a = build_memory_block(tmp_path, topic_id="nowcasting")
    _, idx_b = build_memory_block(tmp_path, topic_id="video-generation")
    for idx in (idx_a, idx_b):
        assert "digest_style" in idx
        assert "research_focus" in idx


def test_hyphen_topic_id_matches_dotted_memory_name(tmp_path: Path) -> None:
    """Topic id 'china-tech' must match memory names 'china-tech' and 'china-tech.theses'
    without false positives like 'china-tech-foo' or 'china-techsomething'."""
    (tmp_path / "topics").mkdir(parents=True)
    for name in ("china-tech", "china-tech.theses", "china-tech-foo"):
        (tmp_path / "topics" / f"{name}.md").write_text(
            "---\n"
            f"name: {name}\n"
            f"description: x\n"
            "type: topic\n"
            "created: 2026-05-01\n"
            "updated: 2026-05-01\n"
            "source: user-edited\n"
            "revision: 1\n"
            "---\nbody\n",
            encoding="utf-8",
        )

    _, idx = build_memory_block(tmp_path, topic_id="china-tech")
    assert "china-tech" in idx
    assert "china-tech.theses" in idx
    assert "china-tech-foo" not in idx, (
        "topic_id match must be by exact-name or `<id>.` prefix, not substring"
    )


def test_topic_id_none_preserves_legacy_unscoped_behavior(tmp_path: Path) -> None:
    """Callers that pass no topic_id (legacy or test fixtures) keep loading
    everything — no surprise behavior change."""
    _seed(tmp_path)
    _, idx = build_memory_block(tmp_path)  # no topic_id
    assert "nowcasting" in idx
    assert "video-generation" in idx
    assert "nvda" in idx
    assert "digest_style" in idx
