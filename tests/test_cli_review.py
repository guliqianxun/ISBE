from pathlib import Path

from typer.testing import CliRunner

from isbe.cli.main import app


def _put_pending(memory_dir: Path, name: str, body_extra: str = "body"):
    p = memory_dir / ".pending" / "feedback" / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"""---
name: {name}
description: agent draft
type: feedback
created: 2026-05-07
updated: 2026-05-07
source: agent-inferred
---
{body_extra}""",
        encoding="utf-8",
    )
    return p


def test_review_memory_lists_when_no_action(memory_dir: Path, monkeypatch):
    _put_pending(memory_dir, "x")
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["review", "memory"])
    assert result.exit_code == 0
    assert "x.md" in result.stdout
    assert "1 pending" in result.stdout


def test_review_memory_empty_when_nothing_pending(memory_dir: Path, monkeypatch):
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["review", "memory"])
    assert result.exit_code == 0
    assert "0 pending" in result.stdout


def test_review_memory_accept_moves_file(memory_dir: Path, monkeypatch):
    pend = _put_pending(memory_dir, "x")
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["review", "memory", "--accept", "feedback/x.md"])
    assert result.exit_code == 0
    assert not pend.exists()
    assert (memory_dir / "feedback" / "x.md").exists()


def test_review_memory_reject_moves_file(memory_dir: Path, monkeypatch):
    pend = _put_pending(memory_dir, "x")
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["review", "memory", "--reject", "feedback/x.md"])
    assert result.exit_code == 0
    assert not pend.exists()
    assert (memory_dir / ".audit" / "rejected" / "feedback" / "x.md").exists()
