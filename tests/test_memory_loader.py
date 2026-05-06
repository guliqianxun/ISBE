from pathlib import Path

from isbe.memory.loader import MemoryFile, load_file, load_index


def test_load_file_parses_frontmatter_and_body(sample_feedback_file: Path):
    mf: MemoryFile = load_file(sample_feedback_file)
    assert mf.frontmatter.name == "digest_style"
    assert mf.frontmatter.revision == 2
    assert "不要在日报里放融资新闻" in mf.body
    assert mf.path == sample_feedback_file


def test_load_index_empty_when_no_files(memory_dir: Path):
    entries = load_index(memory_dir)
    assert entries == []


def test_load_index_lists_all_memory_files(memory_dir: Path, sample_feedback_file: Path):
    entries = load_index(memory_dir)
    assert len(entries) == 1
    assert entries[0].frontmatter.name == "digest_style"


def test_load_index_skips_pending_and_audit(memory_dir: Path, sample_feedback_file: Path):
    pending = memory_dir / ".pending" / "feedback"
    pending.mkdir(parents=True)
    (pending / "x.md").write_text(
        """---
name: x
description: d
type: feedback
created: 2026-05-06
updated: 2026-05-06
source: agent-inferred
---
body""",
        encoding="utf-8",
    )
    entries = load_index(memory_dir)
    assert len(entries) == 1
    assert entries[0].frontmatter.name == "digest_style"
