"""What a shared-mode member may put in a topic.

Members get paper topics with server-managed sources: arXiv (or essaies) only,
no feeds, repositories, Zhihu queries or metrail URL of their own, no `${VAR}`
(which would read the server's environment), and bounded work per run. The
raw document is checked when a member saves it (`valid_member_topic`); the
parsed topic is checked again when a task is submitted and when it runs
(`shared_topic_allowed`), because the file can change and admins can lose
their role in between.
"""

import json
from dataclasses import dataclass
from typing import Any

from .decode import parse_uint
from .model import Topic


@dataclass(frozen=True)
class Limits:
    window_days: int = 365
    max_results: int = 200
    max_papers: int = 50
    poll_timeout_s: int = 300
    # The full-text budgets need a ceiling, or a member could
    # make every digest prompt as large as the papers' tables. The defaults
    # are 3000 and 24000; the largest value in the repository is 8000 per
    # paper (the console fixture). 8000 and 48000 keep that working and cap a
    # 50-paper digest at twice the default table budget (~12-16k tokens of
    # mostly numeric text), well inside the smart tier's context.
    fulltext_per_paper_chars: int = 8000
    fulltext_total_chars: int = 48000


LIMITS = Limits()


def _field(config: Any, *path: str) -> Any:
    for key in path:
        config = config.get(key) if isinstance(config, dict) else None
    return config


def _within(value: Any, limit: int) -> bool:
    """Only non-negative integers are compared (the same rule as the typed
    fields); anything else fails topic validation later. A quoted number
    is compared too, because the topic parser reads `'301'` as 301."""
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return value <= limit
    if isinstance(value, str):
        number = parse_uint(value)
        return number is None or number <= limit
    return True


def valid_member_topic(config: Any, limits: Limits = LIMITS) -> bool:
    """Whether a member may save this raw topic document (the JSON the console sends)."""
    if not isinstance(_field(config, "arxiv"), dict):
        return False
    if any(_field(config, key) is not None for key in ("rss", "github", "zhihu")):
        return False
    if _field(config, "metrail", "base_url") not in (None, ""):
        return False
    if "${" in json.dumps(config, ensure_ascii=False):
        return False
    return all(
        _within(_field(config, *path), limit)
        for path, limit in (
            (("metrail", "poll_timeout_s"), limits.poll_timeout_s),
            (("window_days",), limits.window_days),
            (("arxiv", "max_results"), limits.max_results),
            (("digest", "max_papers"), limits.max_papers),
            (("digest", "fulltext_per_paper_chars"), limits.fulltext_per_paper_chars),
            (("digest", "fulltext_total_chars"), limits.fulltext_total_chars),
        )
    )


def shared_topic_allowed(trusted: bool, topic: Topic, raw: str, limits: Limits = LIMITS) -> bool:
    """Whether a queued report may run this topic. `trusted` is the local
    owner or an active admin; `raw` is the document as submitted (JSON), so
    a `${VAR}` anywhere in it is refused before any expansion matters."""
    if trusted:
        return True
    metrail = topic.metrail
    return not (
        topic.rss is not None
        or topic.zhihu is not None
        or topic.github is not None
        or (metrail is not None and (bool((metrail.base_url or "").strip()) or metrail.poll_timeout_s > limits.poll_timeout_s))
        or topic.window_days > limits.window_days
        or (topic.arxiv is not None and topic.arxiv.max_results > limits.max_results)
        or topic.digest.max_papers > limits.max_papers
        or topic.digest.fulltext_per_paper_chars > limits.fulltext_per_paper_chars
        or topic.digest.fulltext_total_chars > limits.fulltext_total_chars
        or "${" in raw
    )
