from pathlib import Path

import pytest
from prefect.testing.utilities import prefect_test_harness


@pytest.fixture(scope="session", autouse=True)
def _prefect_test_db():
    """Run all unit tests against an ephemeral Prefect DB; no external server needed."""
    with prefect_test_harness():
        yield


@pytest.fixture
def memory_dir(tmp_path: Path) -> Path:
    """Empty memory/<uid>/ scaffold."""
    root = tmp_path / "memory" / "me"
    for sub in ("user", "feedback", "topics", "reading", "reference"):
        (root / sub).mkdir(parents=True)
    (root / "MEMORY.md").write_text("", encoding="utf-8")
    return root


@pytest.fixture
def sample_feedback_file(memory_dir: Path) -> Path:
    p = memory_dir / "feedback" / "digest_style.md"
    p.write_text(
        """---
name: digest_style
description: 用户偏好的日报风格
type: feedback
tags: [output]
created: 2026-05-06
updated: 2026-05-06
source: user-edited
revision: 2
---

不要在日报里放融资新闻。
""",
        encoding="utf-8",
    )
    return p
