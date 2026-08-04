from datetime import date

import typer

from isbe.topics.dispatch import DIGESTER_KEY, DispatchError, resolve_flow
from isbe.topics.registry import default_topics_root, discover_topics, load_topic_config_typed

topics_app = typer.Typer(help="Topic 管理与执行。")


@topics_app.command("list")
def topics_list() -> None:
    for t in discover_topics(default_topics_root()):
        marker = "active" if t.active else "inactive"
        typer.echo(f"{t.id}\t{t.cadence}\t{marker}\t{t.label}")


def _period_label_for(topic_id: str, today: date, override: str | None) -> str:
    if override:
        return override
    # NVDA / any daily topic: ISO date. Weekly topics: ISO year-week.
    cfg = load_topic_config_typed(default_topics_root(), topic_id)
    if cfg.cadence.startswith("daily"):
        return today.isoformat()
    year, week, _ = today.isocalendar()
    return f"{year}-W{week:02d}"


def _digester_params(topic_id: str, period_label: str, today: date) -> dict:
    """Build the kwargs a digester flow expects given its signature.

    The dispatch module returns the flow + a base param set (with topic_id
    only if the signature accepts it). The CLI additionally passes period_label
    and today when the flow accepts them.
    """
    import inspect

    flow_fn, params = resolve_flow(topic_id, DIGESTER_KEY)
    sig_params = inspect.signature(flow_fn).parameters
    extras: dict = {}
    if "period_label" in sig_params:
        extras["period_label"] = period_label
    if "today" in sig_params:
        extras["today"] = today
    return flow_fn, {**params, **extras}


@topics_app.command("run")
def topics_run(
    topic_id: str,
    collect: bool = typer.Option(False, "--collect", help="Run collectors"),
    digest: bool = typer.Option(False, "--digest", help="Run digester"),
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

    cfg = load_topic_config_typed(root, topic_id)

    if collect:
        # Every schedule_key in yaml that isn't the digester is treated as a
        # collector. Run each one; report row counts.
        counts: list[str] = []
        for key in cfg.schedules:
            if key == DIGESTER_KEY or key in ("arxiv_download_pdfs", "metrail_enrich"):
                continue
            try:
                flow_fn, params = resolve_flow(topic_id, key)
            except DispatchError as e:
                typer.echo(f"WARN {key}: {e}", err=True)
                continue
            n = flow_fn(**params)
            counts.append(f"{key}: {n} new")
        if counts:
            typer.echo(" / ".join(counts))

        # arXiv PDF chain: only if the topic has an arxiv block AND yaml
        # declares the download schedule (single source of truth).
        if (
            cfg.arxiv is not None
            and "arxiv_download_pdfs" in cfg.schedules
            and not no_pdfs
        ):
            flow_fn, base = resolve_flow(topic_id, "arxiv_download_pdfs")
            extra = {}
            import inspect
            sig = inspect.signature(flow_fn).parameters
            if "limit" in sig:
                extra["limit"] = pdf_limit
            if "period_label" in sig:
                extra["period_label"] = period_label
            n = flow_fn(**{**base, **extra})
            typer.echo(f"pdfs downloaded: {n} (rate-limited 1 per 3s per arXiv ToS)")

        # metrail full-text chain: yaml-driven opt-in. Runs even with --no-pdfs
        # (extraction needs no downloads) and without a limit (limited, dead
        # rows could starve the batch; the strike blacklist handles
        # pathological PDFs). No-ops when METRAIL_API_URL is unset.
        if "metrail_enrich" in cfg.schedules:
            flow_fn, base = resolve_flow(topic_id, "metrail_enrich")
            n = flow_fn(**base)
            typer.echo(f"fulltext extracted: {n}")

    if digest:
        today = date.fromisoformat(today_str) if today_str else date.today()
        label = _period_label_for(topic_id, today, period_label)
        flow_fn, params = _digester_params(topic_id, label, today)
        result = flow_fn(**params)
        typer.echo(f"digest done: {len(result.pending_drafts)} drafts pending")
