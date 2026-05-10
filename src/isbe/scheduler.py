"""Prefect scheduler — long-lived process registering one set of deployments per active topic.

Drives schedules from each topic's `topic.yaml` `schedules:` block. Each topic
may declare any subset of:
  - arxiv_collector
  - github_collector
  - arxiv_download_pdfs
  - weekly_digester

Usage:
    uv run radar scheduler serve
"""

from prefect import serve

from isbe.topics._shared.arxiv import arxiv_collector
from isbe.topics._shared.digester import weekly_digester
from isbe.topics.nowcasting.collectors.arxiv import arxiv_download_pdfs
from isbe.topics.nowcasting.collectors.github import github_collector
from isbe.topics.registry import default_topics_root, discover_topics, load_topic_config


def _build_deployments():
    deployments = []
    root = default_topics_root()
    for meta in discover_topics(root):
        if not meta.active:
            continue
        cfg = load_topic_config(root, meta.id)
        schedules = cfg.get("schedules", {}) or {}

        if "arxiv_collector" in schedules and cfg.get("arxiv"):
            deployments.append(
                arxiv_collector.to_deployment(
                    name=f"{meta.id}-arxiv",
                    cron=schedules["arxiv_collector"],
                    parameters={"topic_id": meta.id},
                )
            )

        if "github_collector" in schedules:
            # github_collector is nowcasting-bespoke (TRACKED_REPOS hardcoded)
            deployments.append(
                github_collector.to_deployment(
                    name=f"{meta.id}-github",
                    cron=schedules["github_collector"],
                )
            )

        if "arxiv_download_pdfs" in schedules:
            deployments.append(
                arxiv_download_pdfs.to_deployment(
                    name=f"{meta.id}-pdfs",
                    cron=schedules["arxiv_download_pdfs"],
                    parameters={"limit": 20},
                )
            )

        if "weekly_digester" in schedules:
            deployments.append(
                weekly_digester.to_deployment(
                    name=f"{meta.id}-weekly",
                    cron=schedules["weekly_digester"],
                    parameters={"topic_id": meta.id},
                )
            )
    return deployments


def serve_topics() -> None:
    """Long-running: registers one deployment per (topic, scheduled flow). Ctrl-C to stop."""
    deployments = _build_deployments()
    if not deployments:
        raise RuntimeError("no active topics with schedules found under src/isbe/topics/")
    serve(*deployments)


# Backwards-compat alias for any external callers
serve_nowcasting = serve_topics


if __name__ == "__main__":
    serve_topics()
