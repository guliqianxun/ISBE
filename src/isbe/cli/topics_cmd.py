from datetime import date

import typer

from isbe.topics.registry import default_topics_root, discover_topics, load_topic_config

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
        False, "--download-pdfs", help="Download PDFs (nowcasting only)"
    ),
    pdf_limit: int = typer.Option(10, "--pdf-limit", help="Max PDFs per --download-pdfs run"),
    period_label: str = typer.Option(None, help="e.g. 2026-W19 / 2026-05-10; defaults to current"),
) -> None:
    root = default_topics_root()
    topics = {t.id: t for t in discover_topics(root)}
    if topic_id not in topics:
        typer.echo(f"unknown topic: {topic_id}", err=True)
        raise typer.Exit(code=1)

    if not (collect or digest or download_pdfs):
        typer.echo("specify --collect / --digest / --download-pdfs", err=True)
        raise typer.Exit(code=1)

    cfg = load_topic_config(root, topic_id)

    if collect:
        if topic_id == "nvda":
            from isbe.topics.nvda.collectors.news import nvda_news_collector
            from isbe.topics.nvda.collectors.prices import nvda_prices_collector
            from isbe.topics.nvda.collectors.sec import nvda_sec_collector
            n_prices = nvda_prices_collector()
            n_news = nvda_news_collector()
            n_sec = nvda_sec_collector()
            typer.echo(f"prices: {n_prices} new / news: {n_news} new / sec: {n_sec} new")
        else:
            from isbe.topics._shared.arxiv import arxiv_collector
            n_arxiv = arxiv_collector(topic_id=topic_id) if cfg.get("arxiv") else 0
            n_gh = 0
            if topic_id == "nowcasting":
                from isbe.topics.nowcasting.collectors.github import github_collector
                n_gh = github_collector()
            typer.echo(f"arxiv: {n_arxiv} new / github: {n_gh} new")

    if download_pdfs:
        if topic_id != "nowcasting":
            typer.echo(
                f"--download-pdfs is currently nowcasting-only "
                f"(topic '{topic_id}' has no PDF download wiring)",
                err=True,
            )
            raise typer.Exit(code=2)
        from isbe.topics.nowcasting.collectors.arxiv import arxiv_download_pdfs
        n = arxiv_download_pdfs(limit=pdf_limit, period_label=period_label)
        typer.echo(f"pdfs downloaded: {n} (rate-limited 1 per 3s per arXiv ToS)")

    if digest:
        if topic_id == "nvda":
            from isbe.topics.nvda.digester import daily_digester
            today = date.today()
            label = period_label or today.isoformat()
            result = daily_digester(period_label=label, today=today)
            typer.echo(f"digest done: {len(result.pending_drafts)} drafts pending")
        else:
            from isbe.topics._shared.digester import weekly_digester
            today = date.today()
            year, week, _ = today.isocalendar()
            label = period_label or f"{year}-W{week:02d}"
            result = weekly_digester(topic_id=topic_id, period_label=label, today=today)
            typer.echo(f"digest done: {len(result.pending_drafts)} drafts pending")
