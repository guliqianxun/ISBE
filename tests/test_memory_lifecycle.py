from pathlib import Path

from isbe.memory.lifecycle import reindex_memory_md


def test_reindex_writes_one_line_per_file(memory_dir: Path, sample_feedback_file: Path):
    extra = memory_dir / "user" / "research_focus.md"
    extra.write_text(
        """---
name: research_focus
description: 我在写关于 nowcasting 的论文
type: user
created: 2026-05-06
updated: 2026-05-06
source: user-edited
---
focus body""",
        encoding="utf-8",
    )
    reindex_memory_md(memory_dir)
    content = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "feedback/digest_style.md" in content
    assert "user/research_focus.md" in content
    assert "用户偏好的日报风格" in content


def test_reindex_skips_pending_and_audit_and_archive(memory_dir: Path, sample_feedback_file: Path):
    pending = memory_dir / ".pending" / "feedback"
    pending.mkdir(parents=True)
    (pending / "x.md").write_text(
        """---
name: x
description: should be excluded
type: feedback
created: 2026-05-06
updated: 2026-05-06
source: agent-inferred
---
body""",
        encoding="utf-8",
    )
    archive = memory_dir / "reading" / ".archive" / "2025" / "W01"
    archive.mkdir(parents=True)
    (archive / "old.md").write_text(
        """---
name: old
description: archived reading
type: reading
created: 2025-01-01
updated: 2025-01-01
source: agent-inferred
---
body""",
        encoding="utf-8",
    )
    reindex_memory_md(memory_dir)
    content = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "should be excluded" not in content
    assert "archived reading" not in content
