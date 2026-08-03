"""Prefect scheduler — builds deployments from each topic's topic.yaml::schedules.

Resolution of (topic_id, schedule_key) -> (flow, params) is delegated to
isbe.topics.dispatch, so adding a topic or a custom digester touches only the
topic's own directory.
"""

import logging

from prefect import serve

from isbe.topics.dispatch import DispatchError, resolve_flow
from isbe.topics.registry import default_topics_root, discover_topics, load_topic_config_typed

logger = logging.getLogger(__name__)


def _build_deployments():
    deployments = []
    skipped: list[tuple[str, str, str]] = []
    root = default_topics_root()
    for meta in discover_topics(root):
        if not meta.active:
            continue
        cfg = load_topic_config_typed(root, meta.id)
        for schedule_key, cron in cfg.schedules.items():
            try:
                flow_fn, params = resolve_flow(meta.id, schedule_key)
            except DispatchError as e:
                logger.error("topic %s schedule %s failed to resolve: %s", meta.id, schedule_key, e)
                skipped.append((meta.id, schedule_key, str(e)))
                continue
            deployments.append(
                flow_fn.to_deployment(
                    name=f"{meta.id}-{schedule_key}",
                    cron=cron,
                    parameters=params,
                )
            )
    if skipped:
        logger.warning(
            "scheduler: %d schedule(s) skipped and will NEVER run: %s",
            len(skipped),
            "; ".join(f"{t}::{k}" for t, k, _ in skipped),
        )
    return deployments


def serve_topics() -> None:
    deployments = _build_deployments()
    if not deployments:
        raise RuntimeError("no active topics with schedules found under src/isbe/topics/")
    serve(*deployments)


if __name__ == "__main__":
    serve_topics()
