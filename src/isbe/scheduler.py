"""Prefect scheduler — run as long-lived process to fire flows on cron schedules.

Usage:
    uv run radar scheduler serve

Schedules (local timezone — Prefect server's TZ):
    arxiv-collector       06:00 daily   (fetch new papers)
    github-collector      06:30 daily   (refresh repo metadata)
    arxiv-download-pdfs   07:00 Mon     (download PDFs before digest)
    weekly-digester       08:00 Mon     (generate weekly report)
"""

from prefect import serve

from isbe.topics.nowcasting.collectors.arxiv import (
    arxiv_collector,
    arxiv_download_pdfs,
)
from isbe.topics.nowcasting.collectors.github import github_collector
from isbe.topics.nowcasting.digester import weekly_digester


def serve_nowcasting() -> None:
    """Long-running: serves 4 deployments with cron schedules. Ctrl-C to stop."""
    arxiv_daily = arxiv_collector.to_deployment(
        name="nowcasting-arxiv-daily",
        cron="0 6 * * *",
        parameters={"max_results": 50},
    )
    github_daily = github_collector.to_deployment(
        name="nowcasting-github-daily",
        cron="30 6 * * *",
    )
    pdf_weekly = arxiv_download_pdfs.to_deployment(
        name="nowcasting-pdf-weekly",
        cron="0 7 * * 1",
        parameters={"limit": 20},
    )
    weekly = weekly_digester.to_deployment(
        name="nowcasting-weekly-digester",
        cron="0 8 * * 1",
    )
    serve(arxiv_daily, github_daily, pdf_weekly, weekly)


if __name__ == "__main__":
    serve_nowcasting()
