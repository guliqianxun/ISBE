import os
from pathlib import Path

import typer

review_app = typer.Typer(help="审核 agent 提议的草稿（memory / tools / workflows）。")


def _memory_root() -> Path:
    raw = os.getenv("ISBE_MEMORY_ROOT")
    if raw:
        return Path(raw)
    uid = os.getenv("ISBE_UID", "me")
    return Path("memory") / uid


@review_app.command("memory")
def review_memory() -> None:
    """List pending memory drafts (P0 placeholder — no actual review yet)."""
    root = _memory_root()
    pending_root = root / ".pending"
    if not pending_root.exists():
        typer.echo("0 pending")
        return

    files = sorted(p for p in pending_root.rglob("*.md"))
    for p in files:
        typer.echo(str(p.relative_to(root)))
    typer.echo(f"{len(files)} pending")


@review_app.command("tools")
def review_tools() -> None:
    """List pending skill drafts (P0 placeholder)."""
    typer.echo("(not implemented in P0; will be wired up after hermes integration)")
