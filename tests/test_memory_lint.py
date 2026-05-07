from pathlib import Path

from isbe.memory.lint import lint_file


def test_valid_file_passes(sample_feedback_file: Path):
    errors = lint_file(sample_feedback_file)
    assert errors == []


def test_name_must_match_stem(memory_dir: Path):
    p = memory_dir / "feedback" / "actual_stem.md"
    p.write_text(
        """---
name: wrong_name
description: d
type: feedback
created: 2026-05-06
updated: 2026-05-06
source: user-edited
---
body""",
        encoding="utf-8",
    )
    errors = lint_file(p)
    assert any("stem" in e.message for e in errors)


def test_body_size_limit(memory_dir: Path):
    p = memory_dir / "feedback" / "huge.md"
    huge_body = "x" * 5000
    p.write_text(
        f"""---
name: huge
description: d
type: feedback
created: 2026-05-06
updated: 2026-05-06
source: user-edited
---
{huge_body}""",
        encoding="utf-8",
    )
    errors = lint_file(p)
    assert any("body size" in e.message for e in errors)


def test_invalid_frontmatter_collected(memory_dir: Path):
    p = memory_dir / "feedback" / "bad.md"
    p.write_text(
        """---
name: bad
description: d
type: not_a_type
created: 2026-05-06
updated: 2026-05-06
source: user-edited
---
body""",
        encoding="utf-8",
    )
    errors = lint_file(p)
    assert any("frontmatter" in e.message for e in errors)
