from pathlib import Path

from typer.testing import CliRunner

from isbe.cli.main import app


def test_radar_root_help():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "review" in result.stdout


def test_review_memory_lists_pending_files(memory_dir: Path, monkeypatch):
    pending = memory_dir / ".pending" / "feedback"
    pending.mkdir(parents=True)
    (pending / "test.md").write_text(
        """---
name: test
description: d
type: feedback
created: 2026-05-06
updated: 2026-05-06
source: agent-inferred
---
body""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))

    runner = CliRunner()
    result = runner.invoke(app, ["review", "memory"])
    assert result.exit_code == 0
    assert "test.md" in result.stdout
    assert "1 pending" in result.stdout


def test_review_memory_empty_when_nothing_pending(memory_dir: Path, monkeypatch):
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["review", "memory"])
    assert result.exit_code == 0
    assert "0 pending" in result.stdout
