"""Single seam from (topic_id, schedule_key) -> (flow_callable, params).

Replaces the hand-maintained _FLOW_DISPATCH dict and the CLI's if/elif chains.
Adding a new topic = create topics/<id>/topic.yaml. Adding a custom digester =
create topics/<id>/digester.py with a `digest` symbol pointing at a flow. No
scheduler.py or topics_cmd.py edit required.

Resolution order for a schedule_key:
  1. _SHARED_COLLECTORS table (genuinely reusable library flows, parameterized
     by topic_id): arxiv_collector, rss_collector, crawl4ai_collector.
  2. Per-topic collector under topics.<id>.collectors.*: any module exposing
     a callable whose name matches the schedule_key.
  3. Standardized digester key: try topics.<id>.digester:digest; fall back to
     _shared.digester.weekly_digester(topic_id=<id>).
"""

from __future__ import annotations

import importlib
import inspect
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

DIGESTER_KEY = "digester"

logger = logging.getLogger(__name__)


class DispatchError(KeyError):
    """No flow resolves for the given (topic_id, schedule_key)."""


def _module_absent(e: ModuleNotFoundError, probed: str) -> bool:
    """True iff the error means `probed` (or a parent package) does not exist.

    Distinguishes "topic has no such module" (legitimate fallback) from
    "the module exists but an import *inside* it is broken" (must surface —
    swallowing it used to silently substitute the generic digester).
    Parent-package matching matters for hyphen-named topic dirs
    (video-generation → isbe.topics.video_generation is the missing name).
    """
    return e.name is not None and (probed == e.name or probed.startswith(e.name + "."))


# ---------------------------------------------------------------------------
# Shared collectors — small fixed library. These are NOT topic coupling: they
# are reusable building blocks parameterized by topic_id.
# ---------------------------------------------------------------------------


def _shared_collectors() -> dict[str, Callable[..., Any]]:
    from isbe.topics._shared.arxiv import arxiv_collector
    from isbe.topics._shared.crawl4ai_collector import crawl4ai_collector
    from isbe.topics._shared.metrail_enrich import metrail_enrich
    from isbe.topics._shared.monthly import monthly_digester
    from isbe.topics._shared.rss import rss_collector

    return {
        "arxiv_collector": arxiv_collector,
        "rss_collector": rss_collector,
        "crawl4ai_collector": crawl4ai_collector,
        "metrail_enrich": metrail_enrich,
        "monthly_digester": monthly_digester,
    }


# ---------------------------------------------------------------------------
# Topic id <-> module name (only china-tech needs the hyphen->underscore hop)
# ---------------------------------------------------------------------------


def _topic_module(topic_id: str) -> str:
    return topic_id.replace("-", "_")


def _topics_root() -> Path:
    return Path(__file__).parent


# ---------------------------------------------------------------------------
# Per-topic collector discovery
# ---------------------------------------------------------------------------


def _find_per_topic_collector(topic_id: str, schedule_key: str) -> Callable[..., Any] | None:
    mod_name = _topic_module(topic_id)
    pkg_dir = _topics_root() / mod_name / "collectors"
    if not pkg_dir.is_dir():
        return None
    for py in sorted(pkg_dir.glob("*.py")):
        if py.name.startswith("_"):
            continue
        probed = f"isbe.topics.{mod_name}.collectors.{py.stem}"
        try:
            mod = importlib.import_module(probed)
        except ModuleNotFoundError as e:
            if _module_absent(e, probed):
                continue
            raise DispatchError(f"broken import inside {probed}: {e}") from e
        except ImportError as e:
            raise DispatchError(f"broken import inside {probed}: {e}") from e
        fn = getattr(mod, schedule_key, None)
        if callable(fn):
            return fn
    return None


# ---------------------------------------------------------------------------
# Per-topic digester discovery
# ---------------------------------------------------------------------------


def _find_per_topic_digester(topic_id: str) -> Callable[..., Any] | None:
    mod_name = _topic_module(topic_id)
    probed = f"isbe.topics.{mod_name}.digester"
    try:
        mod = importlib.import_module(probed)
    except ModuleNotFoundError as e:
        if _module_absent(e, probed):
            return None
        raise DispatchError(f"broken import inside {probed}: {e}") from e
    except ImportError as e:
        raise DispatchError(f"broken import inside {probed}: {e}") from e
    digest = getattr(mod, "digest", None)
    return digest if callable(digest) else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _pass_topic_id_if_accepted(fn: Callable[..., Any], topic_id: str) -> dict:
    """Return {"topic_id": ...} iff the flow's signature accepts it."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return {}
    return {"topic_id": topic_id} if "topic_id" in sig.parameters else {}


def resolve_flow(topic_id: str, schedule_key: str) -> tuple[Callable[..., Any], dict]:
    """Resolve (topic_id, schedule_key) -> (flow, kwargs). Raises DispatchError."""
    shared = _shared_collectors()
    if schedule_key in shared:
        fn = shared[schedule_key]
        return fn, _pass_topic_id_if_accepted(fn, topic_id)

    if schedule_key == DIGESTER_KEY:
        custom = _find_per_topic_digester(topic_id)
        if custom is not None:
            return custom, _pass_topic_id_if_accepted(custom, topic_id)
        from isbe.topics._shared.digester import weekly_digester

        return weekly_digester, {"topic_id": topic_id}

    per_topic = _find_per_topic_collector(topic_id, schedule_key)
    if per_topic is not None:
        return per_topic, _pass_topic_id_if_accepted(per_topic, topic_id)

    raise DispatchError(
        f"no flow resolves for topic={topic_id!r} schedule_key={schedule_key!r}"
    )


# ---------------------------------------------------------------------------
# Enumeration helpers (used by status_cmd to classify rows)
# ---------------------------------------------------------------------------


def _all_topic_ids() -> list[str]:
    from isbe.topics.registry import default_topics_root, discover_topics

    return [t.id for t in discover_topics(default_topics_root())]


def collect_flow_names() -> set[str]:
    """Return all Prefect flow .name strings classified as collectors."""
    names: set[str] = set()
    for fn in _shared_collectors().values():
        names.add(_flow_name(fn))
    for topic_id in _all_topic_ids():
        pkg_dir = _topics_root() / _topic_module(topic_id) / "collectors"
        if not pkg_dir.is_dir():
            continue
        for py in pkg_dir.glob("*.py"):
            if py.name.startswith("_"):
                continue
            probed = f"isbe.topics.{_topic_module(topic_id)}.collectors.{py.stem}"
            try:
                mod = importlib.import_module(probed)
            except ModuleNotFoundError as e:
                if not _module_absent(e, probed):
                    # Unlike resolve_flow, enumeration is for status display —
                    # a broken module shouldn't crash `radar status`, just warn.
                    logger.warning("collect_flow_names: broken import inside %s: %s", probed, e)
                continue
            except ImportError as e:
                logger.warning("collect_flow_names: broken import inside %s: %s", probed, e)
                continue
            for attr in dir(mod):
                if attr.startswith("_"):
                    continue
                obj = getattr(mod, attr)
                if _is_prefect_flow(obj):
                    names.add(_flow_name(obj))
    return names


def digest_flow_names() -> set[str]:
    """Return all Prefect flow .name strings classified as digesters."""
    from isbe.topics._shared.digester import weekly_digester

    names = {_flow_name(weekly_digester)}
    for topic_id in _all_topic_ids():
        try:
            custom = _find_per_topic_digester(topic_id)
        except DispatchError as e:
            logger.warning("digest_flow_names: %s", e)
            continue
        if custom is not None:
            names.add(_flow_name(custom))
    return names


def _is_prefect_flow(obj: Any) -> bool:
    return callable(obj) and hasattr(obj, "name") and hasattr(obj, "to_deployment")


def _flow_name(obj: Any) -> str:
    return getattr(obj, "name", getattr(obj, "__name__", "?"))
