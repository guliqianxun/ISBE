import os
from datetime import date
from pathlib import Path

import typer

from isbe.memory.lifecycle import archive_old_reading, reindex_memory_md

memory_app = typer.Typer(help="Memory 维护命令（索引、归档）。")


def _memory_root() -> Path:
    raw = os.getenv("ISBE_MEMORY_ROOT")
    if raw:
        return Path(raw)
    uid = os.getenv("ISBE_UID", "me")
    return Path("memory") / uid


@memory_app.command("reindex")
def reindex() -> None:
    """Rewrite MEMORY.md from current in-tree files."""
    root = _memory_root()
    reindex_memory_md(root)
    typer.echo(f"reindexed {root / 'MEMORY.md'}")


@memory_app.command("archive")
def archive(
    today: str = typer.Option(None, help="ISO date (YYYY-MM-DD); defaults to today"),
    age_weeks: int = typer.Option(8, help="Archive entries older than N weeks"),
) -> None:
    """Archive old reading/ entries to reading/.archive/."""
    root = _memory_root()
    today_d = date.fromisoformat(today) if today else date.today()
    moved = archive_old_reading(root, today=today_d, age_weeks=age_weeks)
    typer.echo(f"{moved} file(s) archived")
