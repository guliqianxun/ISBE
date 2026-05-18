"""Tests for src/isbe/topics/dispatch.py — the single seam that maps
(topic_id, schedule_key) → (flow_callable, params).

Replaces the hand-maintained _FLOW_DISPATCH dict and the CLI's if/elif chain.
"""

from __future__ import annotations

import pytest

from isbe.topics.dispatch import (
    DispatchError,
    collect_flow_names,
    digest_flow_names,
    resolve_flow,
)

# ---------------------------------------------------------------------------
# Shared collectors — fixed registry, parameterized by topic_id
# ---------------------------------------------------------------------------


def test_arxiv_collector_resolves_to_shared_flow() -> None:
    flow, params = resolve_flow("nowcasting", "arxiv_collector")
    assert flow.name == "arxiv-collector"
    assert params == {"topic_id": "nowcasting"}


def test_rss_collector_resolves_to_shared_flow_with_topic_id() -> None:
    flow, params = resolve_flow("motorcycle", "rss_collector")
    assert flow.name == "rss-collector"
    assert params == {"topic_id": "motorcycle"}


def test_crawl4ai_collector_resolves_to_shared_flow() -> None:
    flow, params = resolve_flow("china-tech", "crawl4ai_collector")
    assert flow.name == "crawl4ai-collector"
    assert params == {"topic_id": "china-tech"}


# ---------------------------------------------------------------------------
# Per-topic collectors — discovered from topics/<id>/collectors/<name>.py
# ---------------------------------------------------------------------------


def test_nowcasting_github_collector_resolves_to_per_topic_flow() -> None:
    flow, params = resolve_flow("nowcasting", "github_collector")
    assert flow.name == "github-collector"


def test_arxiv_download_pdfs_resolves_to_nowcasting_flow() -> None:
    flow, params = resolve_flow("nowcasting", "arxiv_download_pdfs")
    assert flow.name == "arxiv-download-pdfs"
    # arxiv_download_pdfs accepts topic_id; resolver should pass it
    assert params.get("topic_id") == "nowcasting"


def test_nvda_prices_collector_resolves_to_per_topic_flow() -> None:
    flow, _ = resolve_flow("nvda", "nvda_prices_collector")
    assert flow.name == "nvda-prices-collector"


def test_nvda_news_collector_resolves_to_per_topic_flow() -> None:
    flow, _ = resolve_flow("nvda", "nvda_news_collector")
    assert flow.name == "nvda-news-collector"


def test_nvda_sec_collector_resolves_to_per_topic_flow() -> None:
    flow, _ = resolve_flow("nvda", "nvda_sec_collector")
    assert flow.name == "nvda-sec-collector"


# ---------------------------------------------------------------------------
# Digester — standardized "digester" key; per-topic overrides _shared
# ---------------------------------------------------------------------------


def test_digester_falls_back_to_shared_weekly_for_arxiv_topics() -> None:
    flow, params = resolve_flow("nowcasting", "digester")
    assert flow.name == "weekly-digester"
    assert params == {"topic_id": "nowcasting"}


def test_digester_falls_back_to_shared_for_video_generation() -> None:
    flow, params = resolve_flow("video-generation", "digester")
    assert flow.name == "weekly-digester"
    assert params == {"topic_id": "video-generation"}


def test_digester_uses_per_topic_module_when_present_nvda() -> None:
    flow, params = resolve_flow("nvda", "digester")
    assert flow.name == "nvda-daily-digester"
    # NVDA digester takes no topic_id — resolver should NOT pass one
    assert "topic_id" not in params


def test_digester_uses_per_topic_module_when_present_motorcycle() -> None:
    flow, params = resolve_flow("motorcycle", "digester")
    assert flow.name == "motorcycle-digester"
    assert "topic_id" not in params


def test_digester_uses_per_topic_module_when_present_china_tech() -> None:
    flow, params = resolve_flow("china-tech", "digester")
    assert flow.name == "china-tech-digester"
    assert "topic_id" not in params


# ---------------------------------------------------------------------------
# Unknown / typo schedule keys
# ---------------------------------------------------------------------------


def test_unknown_schedule_key_raises_dispatch_error() -> None:
    with pytest.raises(DispatchError) as exc:
        resolve_flow("nowcasting", "bogus_key")
    msg = str(exc.value)
    assert "bogus_key" in msg
    assert "nowcasting" in msg


def test_per_topic_collector_for_topic_that_lacks_it_raises() -> None:
    """nvda has no github_collector — should raise rather than silently no-op."""
    with pytest.raises(DispatchError):
        resolve_flow("nvda", "github_collector")


# ---------------------------------------------------------------------------
# Bulk enumeration helpers (used by status_cmd to classify rows)
# ---------------------------------------------------------------------------


def test_collect_flow_names_includes_known_collectors() -> None:
    names = collect_flow_names()
    # Shared collectors
    assert "arxiv-collector" in names
    assert "rss-collector" in names
    assert "crawl4ai-collector" in names


def test_digest_flow_names_includes_per_topic_and_shared() -> None:
    names = digest_flow_names()
    assert "weekly-digester" in names
    assert "nvda-daily-digester" in names
    assert "motorcycle-digester" in names
    assert "china-tech-digester" in names


# ---------------------------------------------------------------------------
# Full integration: every shipped topic.yaml's schedules must resolve
# ---------------------------------------------------------------------------


def test_every_shipped_schedule_key_resolves() -> None:
    """The whole point: no topic.yaml can ship a schedule key that doesn't dispatch.

    This is the test that catches "added a topic, forgot to wire it" in one place.
    """
    from isbe.topics.registry import default_topics_root, discover_topics, load_topic_config_typed

    root = default_topics_root()
    failures: list[str] = []
    for meta in discover_topics(root):
        cfg = load_topic_config_typed(root, meta.id)
        for schedule_key in cfg.schedules:
            try:
                flow, _ = resolve_flow(meta.id, schedule_key)
                assert callable(flow) or hasattr(flow, "name")
            except DispatchError as e:
                failures.append(f"{meta.id}::{schedule_key}: {e}")
    assert not failures, "schedules failed to resolve:\n" + "\n".join(failures)
