import importlib
from datetime import date

import typer

from isbe.topics.registry import default_topics_root, discover_topics

topics_app = typer.Typer(help="Topic 管理与执行。")


@topics_app.command("list")
def topics_list() -> None:
    for t in discover_topics(default_topics_root()):
        marker = "active" if t.active else "inactive"
        typer.echo(f"{t.id}\t{t.cadence}\t{marker}\t{t.label}")


@topics_app.command("run")
def topics_run(
    topic_id: str,
    collect: bool = typer.Option(False, "--collect", help="Run collectors only"),
    digest: bool = typer.Option(False, "--digest", help="Run digester only"),
    download_pdfs: bool = typer.Option(
        False, "--download-pdfs", help="Download PDFs for papers without pdf_uri"
    ),
    pdf_limit: int = typer.Option(10, "--pdf-limit", help="Max PDFs per --download-pdfs run"),
    period_label: str = typer.Option(None, help="e.g. 2026-W19; defaults to current ISO week"),
) -> None:
    topics = {t.id: t for t in discover_topics(default_topics_root())}
    if topic_id not in topics:
        typer.echo(f"unknown topic: {topic_id}", err=True)
        raise typer.Exit(code=1)

    if not (collect or digest or download_pdfs):
        typer.echo("specify --collect / --digest / --download-pdfs", err=True)
        raise typer.Exit(code=1)

    if topic_id == "nowcasting":
        if collect:
            arxiv_mod = importlib.import_module("isbe.topics.nowcasting.collectors.arxiv")
            github_mod = importlib.import_module("isbe.topics.nowcasting.collectors.github")
            n1 = arxiv_mod.arxiv_collector()
            n2 = github_mod.github_collector()
            typer.echo(f"arxiv: {n1} new / github: {n2} new")
        if download_pdfs:
            arxiv_mod = importlib.import_module("isbe.topics.nowcasting.collectors.arxiv")
            n = arxiv_mod.arxiv_download_pdfs(limit=pdf_limit, period_label=period_label)
            typer.echo(f"pdfs downloaded: {n} (rate-limited 1 per 3s per arXiv ToS)")
        if digest:
            digester_mod = importlib.import_module("isbe.topics.nowcasting.digester")
            today = date.today()
            year, week, _ = today.isocalendar()
            label = period_label or f"{year}-W{week:02d}"
            result = digester_mod.weekly_digester(period_label=label, today=today)
            typer.echo(f"digest done: {len(result.pending_drafts)} drafts pending")
    else:
        typer.echo(f"topic {topic_id} has no run wiring yet", err=True)
        raise typer.Exit(code=2)
