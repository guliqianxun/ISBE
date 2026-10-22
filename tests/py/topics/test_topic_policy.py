import json
from typing import Any

import pytest

from isbe.topics import LIMITS, parse_with_env, shared_topic_allowed, valid_member_topic

PAPERS = "id: papers\nlabel: Papers\ncadence: weekly\narxiv:\n  categories: [cs.LG]\n  include_keywords: [learning]\n"


def test_member_topics_cannot_override_sources_or_unbounded_extraction() -> None:
    assert valid_member_topic({"arxiv": {"categories": ["cs.LG"]}})
    for value in [
        {"arxiv": {}, "rss": {"feeds": []}},
        {"arxiv": {}, "label": "${OPENAI_API_KEY}"},
        {"arxiv": {}, "metrail": {"poll_timeout_s": 86400}},
        {"arxiv": {}, "digest": {"max_papers": 99999}},
    ]:
        assert not valid_member_topic(value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        "arxiv",
        {},
        {"arxiv": None},
        {"arxiv": []},
        {"arxiv": {}, "github": {"repos": ["a/b"]}},
        {"arxiv": {}, "zhihu": {}},
        {"arxiv": {}, "metrail": {"base_url": "http://127.0.0.1/private"}},
        {"arxiv": {}, "window_days": 366},
        {"arxiv": {"max_results": 201}},
        {"arxiv": {}, "digest": {"max_papers": 51}},
        {"arxiv": {}, "metrail": {"poll_timeout_s": 301}},
        {"arxiv": {}, "rss": {"feeds": [{"name": "x", "url": "${HOST}"}]}},
        {"arxiv": {}, "${KEY}": 1},
    ],
)
def test_member_documents_outside_the_limits_are_refused(value: Any) -> None:
    assert not valid_member_topic(value)


def test_member_documents_within_the_limits_are_allowed() -> None:
    assert valid_member_topic({"arxiv": {}, "rss": None, "github": None, "zhihu": None, "metrail": {"base_url": ""}})
    assert valid_member_topic({"arxiv": {"max_results": 200}, "window_days": 365, "digest": {"max_papers": 50}, "metrail": {"poll_timeout_s": 300}})
    # Values the topic parser rejects anyway are left to it.
    assert valid_member_topic({"arxiv": {}, "window_days": 400.0, "digest": {"max_papers": True}})
    # The console fixture (web/topics.test.cjs).
    assert valid_member_topic({
        "id": "papers", "label": "Research papers", "lang": "en", "cadence": "weekly", "active": True, "window_days": 7,
        "arxiv": {"categories": ["cs.AI"], "include_keywords": ["agents"], "max_results": 50},
        "digest": {"max_papers": 25, "fulltext_per_paper_chars": 8000},
    })


def topic(extra: str = "") -> tuple[Any, str]:
    base = PAPERS.split("arxiv:")[0] if extra.startswith("arxiv:") else PAPERS
    text = base + extra
    return parse_with_env(text, "papers", lambda _: None), json.dumps({"text": text})


@pytest.mark.parametrize(
    "extra",
    [
        "window_days: 366\n",
        "arxiv:\n  categories: [cs.LG]\n  include_keywords: [learning]\n  max_results: 201\n",
        "digest: {max_papers: 51}\n",
        "metrail: {poll_timeout_s: 301}\n",
        "metrail: {base_url: 'http://127.0.0.1/private'}\n",
        "rss:\n  feeds:\n    - name: Private\n      url: http://127.0.0.1/private\n",
        "github: {repos: [a/b]}\n",
        "zhihu: {queries: [x]}\n",
    ],
)
def test_shared_topics_must_stay_within_service_limits(extra: str) -> None:
    parsed, raw = topic(extra)
    assert not shared_topic_allowed(False, parsed, raw)
    assert shared_topic_allowed(True, parsed, raw), "the local owner and active admins are trusted"


def test_shared_topics_within_limits_run() -> None:
    parsed, raw = topic("window_days: 365\ndigest: {max_papers: 50}\nmetrail: {poll_timeout_s: 300, base_url: '  '}\n")
    assert shared_topic_allowed(False, parsed, raw)
    assert not shared_topic_allowed(False, parsed, raw.replace("Papers", "${KEY}"))


def test_member_fulltext_budgets_are_bounded() -> None:
    per_paper, total = LIMITS.fulltext_per_paper_chars, LIMITS.fulltext_total_chars
    assert (per_paper, total) == (8000, 48000)
    ok = {"arxiv": {}, "digest": {"fulltext_per_paper_chars": per_paper, "fulltext_total_chars": total}}
    assert valid_member_topic(ok)
    for digest in [
        {"fulltext_per_paper_chars": per_paper + 1},
        {"fulltext_total_chars": total + 1},
        {"fulltext_total_chars": 2**64 - 1},
        # The topic parser reads a quoted number as that number.
        {"fulltext_total_chars": "48001"},
        {"fulltext_per_paper_chars": "0x2000"},
    ]:
        assert not valid_member_topic({"arxiv": {}, "digest": digest}), digest
    parsed, raw = topic(f"digest: {{fulltext_per_paper_chars: {per_paper}, fulltext_total_chars: {total}}}\n")
    assert shared_topic_allowed(False, parsed, raw)
    for extra in [f"digest: {{fulltext_per_paper_chars: {per_paper + 1}}}\n", f"digest: {{fulltext_total_chars: '{total + 1}'}}\n"]:
        parsed, raw = topic(extra)
        assert not shared_topic_allowed(False, parsed, raw)
        assert shared_topic_allowed(True, parsed, raw), "trusted owners keep the unbounded budgets"
