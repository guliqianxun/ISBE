import typer

from isbe.scheduler import serve_nowcasting

scheduler_app = typer.Typer(help="Prefect 调度运行（serve 长进程）。")


@scheduler_app.command("serve")
def serve() -> None:
    """Long-running serve of all nowcasting deployments. Ctrl-C to stop.

    Schedules (4 deployments):
      arxiv-collector       06:00 daily
      github-collector      06:30 daily
      arxiv-download-pdfs   07:00 Mon
      weekly-digester       08:00 Mon

    See Prefect UI at http://localhost:4200 for live runs.
    """
    serve_nowcasting()
