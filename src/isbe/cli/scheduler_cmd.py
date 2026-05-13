import typer

from isbe.scheduler import serve_topics

scheduler_app = typer.Typer(help="Prefect 调度运行（serve 长进程）。")


@scheduler_app.command("serve")
def serve() -> None:
    """Long-running serve of all active topics' scheduled deployments.

    Schedules are read from each `topics/*/topic.yaml`'s `schedules:` block.
    See Prefect UI at http://localhost:4200 for live runs. Ctrl-C to stop.
    """
    serve_topics()
