from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from isbe.cli.main import app


def test_radar_memory_reindex_writes_index(memory_dir: Path, sample_feedback_file: Path, monkeypatch):
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["memory", "reindex"])
    assert result.exit_code == 0
    content = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "digest_style" in content


def test_radar_memory_archive_moves_old(memory_dir: Path, monkeypatch):
    week_dir = memory_dir / "reading" / "2026" / "W10"
    week_dir.mkdir(parents=True)
    (week_dir / "x.md").write_text(
        """---
name: x
description: x
type: reading
created: 2026-03-09
updated: 2026-03-09
source: agent-inferred
---
body""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["memory", "archive", "--today", "2026-05-15"])
    assert result.exit_code == 0
    assert "1 file" in result.stdout
