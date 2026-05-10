"""Prefect scheduler — dispatches schedules from each topic's topic.yaml.

Each topic's `schedules:` block maps named schedule keys → cron strings.
This module owns the mapping `schedule_key → flow callable`.
"""

from prefect import serve

from isbe.topics._shared.arxiv import arxiv_collector
from isbe.topics._shared.digester import weekly_digester
from isbe.topics.nowcasting.collectors.arxiv import arxiv_download_pdfs
from isbe.topics.nowcasting.collectors.github import github_collector
from isbe.topics.nvda.collectors.news import nvda_news_collector
from isbe.topics.nvda.collectors.prices import nvda_prices_collector
from isbe.topics.nvda.collectors.sec import nvda_sec_collector
from isbe.topics.nvda.digester import daily_digester
from isbe.topics.registry import default_topics_root, discover_topics, load_topic_config

# schedule_key -> (flow, parameter-template)
# parameter-template values may use {topic_id} for substitution
_FLOW_DISPATCH = {
    "arxiv_collector": (arxiv_collector, {"topic_id": "{topic_id}"}),
    "github_collector": (github_collector, {}),
    "arxiv_download_pdfs": (arxiv_download_pdfs, {"limit": 20}),
    "weekly_digester": (weekly_digester, {"topic_id": "{topic_id}"}),
    "nvda_prices_collector": (nvda_prices_collector, {}),
    "nvda_news_collector": (nvda_news_collector, {}),
    "nvda_sec_collector": (nvda_sec_collector, {}),
    "daily_digester": (daily_digester, {}),
}


def _materialize_params(template: dict, *, topic_id: str) -> dict:
    out: dict = {}
    for k, v in template.items():
        if isinstance(v, str) and "{topic_id}" in v:
            out[k] = v.replace("{topic_id}", topic_id)
        else:
            out[k] = v
    return out


def _build_deployments():
    deployments = []
    root = default_topics_root()
    for meta in discover_topics(root):
        if not meta.active:
            continue
        cfg = load_topic_config(root, meta.id)
        for schedule_key, cron in (cfg.get("schedules") or {}).items():
            entry = _FLOW_DISPATCH.get(schedule_key)
            if entry is None:
                print(f"[scheduler] WARN topic {meta.id}: unknown schedule key '{schedule_key}'")
                continue
            flow_fn, param_tpl = entry
            params = _materialize_params(param_tpl, topic_id=meta.id)
            deployments.append(
                flow_fn.to_deployment(
                    name=f"{meta.id}-{schedule_key}",
                    cron=cron,
                    parameters=params,
                )
            )
    return deployments


def serve_topics() -> None:
    deployments = _build_deployments()
    if not deployments:
        raise RuntimeError("no active topics with schedules found under src/isbe/topics/")
    serve(*deployments)


# Backwards-compat alias
serve_nowcasting = serve_topics


if __name__ == "__main__":
    serve_topics()
