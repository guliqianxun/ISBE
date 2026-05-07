import os
from pathlib import Path

import typer

from isbe.memory.pending import accept_pending, list_pending, reject_pending

review_app = typer.Typer(help="审核 agent 提议的草稿（memory / tools / workflows）。")


def _memory_root() -> Path:
    raw = os.getenv("ISBE_MEMORY_ROOT")
    if raw:
        return Path(raw)
    uid = os.getenv("ISBE_UID", "me")
    return Path("memory") / uid


@review_app.command("memory")
def review_memory(
    accept: str = typer.Option(
        None, "--accept", help="Accept a pending draft (relative path under .pending/)"
    ),
    reject: str = typer.Option(None, "--reject", help="Reject a pending draft"),
) -> None:
    """Review pending memory drafts; without flags, list all pending."""
    root = _memory_root()
    pending_root = root / ".pending"

    if accept:
        target = pending_root / accept
        if not target.exists():
            typer.echo(f"no such pending: {accept}", err=True)
            raise typer.Exit(code=1)
        moved = accept_pending(root, target)
        typer.echo(f"accepted -> {moved.relative_to(root)}")
        return

    if reject:
        target = pending_root / reject
        if not target.exists():
            typer.echo(f"no such pending: {reject}", err=True)
            raise typer.Exit(code=1)
        moved = reject_pending(root, target)
        typer.echo(f"rejected -> {moved.relative_to(root)}")
        return

    files = list_pending(root)
    for p in files:
        typer.echo(str(p.relative_to(root)))
    typer.echo(f"{len(files)} pending")


@review_app.command("tools")
def review_tools() -> None:
    """List pending skill drafts (P1 placeholder; wired up in P3)."""
    typer.echo("(not implemented in P1)")
