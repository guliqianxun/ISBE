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
    collect: bool = typer.Option(False, "--collect", help="Run collectors (downloads PDFs by default)"),
    digest: bool = typer.Option(False, "--digest", help="Run digester only"),
    no_pdfs: bool = typer.Option(
        False, "--no-pdfs", help="Skip the auto-PDF-download chained after --collect"
    ),
    pdf_limit: int = typer.Option(0, "--pdf-limit", help="Cap PDFs per run; 0 = unlimited"),
    period_label: str = typer.Option(None, help="e.g. 2026-W19 / 2026-05-10; defaults to current"),
    today_str: str = typer.Option(
        None, "--today", help="Override 'today' for digest cutoff (ISO date, e.g. 2026-05-07)"
    ),
) -> None:
    root = default_topics_root()
    topics = {t.id: t for t in discover_topics(root)}
    if topic_id not in topics:
        typer.echo(f"unknown topic: {topic_id}", err=True)
        raise typer.Exit(code=1)

    if not (collect or digest):
        typer.echo("specify --collect / --digest", err=True)
        raise typer.Exit(code=1)

    cfg = load_topic_config(root, topic_id)
    has_arxiv = bool(cfg.get("arxiv"))
    has_rss = bool(cfg.get("rss"))
    has_crawl4ai = bool(cfg.get("crawl4ai"))

    if collect:
        if topic_id == "nvda":
            from isbe.topics.nvda.collectors.news import nvda_news_collector
            from isbe.topics.nvda.collectors.prices import nvda_prices_collector
            from isbe.topics.nvda.collectors.sec import nvda_sec_collector
            n_prices = nvda_prices_collector()
            n_news = nvda_news_collector()
            n_sec = nvda_sec_collector()
            typer.echo(f"prices: {n_prices} new / news: {n_news} new / sec: {n_sec} new")
        elif has_rss or has_crawl4ai:
            n_articles = 0
            if has_rss:
                from isbe.topics._shared.rss import rss_collector
                n_articles += rss_collector(topic_id=topic_id)
            if has_crawl4ai:
                from isbe.topics._shared.crawl4ai_collector import crawl4ai_collector
                n_articles += crawl4ai_collector(topic_id=topic_id)
            typer.echo(f"articles: {n_articles} new")
        else:
            from isbe.topics._shared.arxiv import arxiv_collector
            n_arxiv = arxiv_collector(topic_id=topic_id) if has_arxiv else 0
            n_gh = 0
            if topic_id == "nowcasting":
                from isbe.topics.nowcasting.collectors.github import github_collector
                n_gh = github_collector()
            typer.echo(f"arxiv: {n_arxiv} new / github: {n_gh} new")

        # Chain PDF download for arxiv-backed topics by default (--no-pdfs to skip).
        if has_arxiv and not no_pdfs:
            from isbe.topics.nowcasting.collectors.arxiv import arxiv_download_pdfs
            n = arxiv_download_pdfs(
                topic_id=topic_id, limit=pdf_limit, period_label=period_label
            )
            typer.echo(f"pdfs downloaded: {n} (rate-limited 1 per 3s per arXiv ToS)")

    if digest:
        today = date.fromisoformat(today_str) if today_str else date.today()
        if topic_id == "nvda":
            from isbe.topics.nvda.digester import daily_digester
            label = period_label or today.isoformat()
            result = daily_digester(period_label=label, today=today)
            typer.echo(f"digest done: {len(result.pending_drafts)} drafts pending")
        elif topic_id == "motorcycle":
            from isbe.topics.motorcycle.digester import motorcycle_digester
            year, week, _ = today.isocalendar()
            label = period_label or f"{year}-W{week:02d}"
            result = motorcycle_digester(period_label=label, today=today)
            typer.echo(f"digest done: {len(result.pending_drafts)} drafts pending")
        elif topic_id == "china-tech":
            from isbe.topics.china_tech.digester import china_tech_digester
            year, week, _ = today.isocalendar()
            label = period_label or f"{year}-W{week:02d}"
            result = china_tech_digester(period_label=label, today=today)
            typer.echo(f"digest done: {len(result.pending_drafts)} drafts pending")
        else:
            from isbe.topics._shared.digester import weekly_digester
            year, week, _ = today.isocalendar()
            label = period_label or f"{year}-W{week:02d}"
            result = weekly_digester(topic_id=topic_id, period_label=label, today=today)
            typer.echo(f"digest done: {len(result.pending_drafts)} drafts pending")
