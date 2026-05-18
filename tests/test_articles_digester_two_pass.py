"""Two-pass digester: fast tier batches per-article reviews, then smart tier
synthesizes. Without this, a 100+ article topic gets `—` for every review
because the synthesis LLM gives up on per-article output under high cardinality.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from isbe.llm.client import LLMResponse
from isbe.topics._shared.article_reviews import (
    REVIEWS_SYSTEM_PROMPT,
    build_reviews_prompt,
    collect_article_reviews,
)


@dataclass
class _Article:
    id: str
    source: str
    headline: str
    summary: str
    published_at: object


def _fake_article(idx: int) -> _Article:
    import datetime

    # Pack idx into the LEADING bytes so the 12-char prefix is unique per article.
    return _Article(
        id=f"{idx:08x}{'0' * 32}"[:40],
        source="36kr",
        headline=f"headline #{idx}",
        summary=f"summary body #{idx}",
        published_at=datetime.date(2026, 5, 18),
    )


# ---------------------------------------------------------------------------
# Prompt builder smoke
# ---------------------------------------------------------------------------


def test_reviews_prompt_includes_id_marker_and_format_instruction() -> None:
    articles = [_fake_article(1), _fake_article(2)]
    prompt = build_reviews_prompt(topic_label="topic-x", articles=articles)
    # Each article appears with its 12-char id prefix
    assert articles[0].id[:12] in prompt
    assert articles[1].id[:12] in prompt
    # Format instruction so the model emits parseable lines
    assert "[id=" in prompt


def test_reviews_system_prompt_is_terse() -> None:
    # The fast tier is supposed to do one thing — write reviews. The system
    # prompt is short; if it grows beyond a paragraph, the budget creep is a smell.
    assert len(REVIEWS_SYSTEM_PROMPT) < 1500


# ---------------------------------------------------------------------------
# collect_article_reviews — batching + dict assembly
# ---------------------------------------------------------------------------


def _fake_complete_factory(reviews_by_id12: dict[str, str]):
    """Return a fake `complete` that emits `[id=xxxxxxxxxxxx]: review` for any
    article id-prefix it knows about that appears in the user prompt."""

    def fake_complete(*, system, user, tier="smart", **kwargs):
        lines: list[str] = []
        for key12, text in reviews_by_id12.items():
            if key12 in user:
                lines.append(f"- [id={key12}]: {text}")
        return LLMResponse(
            text="\n".join(lines),
            message_id=f"mock-{tier}",
            input_tokens=10,
            output_tokens=len(lines),
            trace_id=None,
        )

    return MagicMock(side_effect=fake_complete)


def test_collect_reviews_batches_articles_and_combines_results() -> None:
    articles = [_fake_article(i) for i in range(60)]
    expected = {a.id[:12]: f"eval-{i}" for i, a in enumerate(articles)}
    fake = _fake_complete_factory(expected)

    out = collect_article_reviews(
        articles,
        topic_label="topic-x",
        complete_fn=fake,
        batch_size=25,
    )

    # Three batches: 25 + 25 + 10
    assert fake.call_count == 3
    # Every article got its review, keyed by FULL id
    for i, a in enumerate(articles):
        assert out[a.id] == f"eval-{i}"


def test_collect_reviews_calls_fast_tier() -> None:
    articles = [_fake_article(i) for i in range(3)]
    fake = _fake_complete_factory({a.id[:12]: "x" for a in articles})
    collect_article_reviews(
        articles, topic_label="t", complete_fn=fake, batch_size=10
    )
    # All calls must request tier='fast' so cost stays bounded.
    for call in fake.call_args_list:
        assert call.kwargs.get("tier") == "fast"


def test_collect_reviews_handles_empty_article_list() -> None:
    fake = MagicMock()
    out = collect_article_reviews(
        [], topic_label="t", complete_fn=fake, batch_size=25
    )
    assert out == {}
    fake.assert_not_called()


def test_collect_reviews_tolerates_missing_reviews_in_batch() -> None:
    """If the fast tier returns reviews for only some articles, we still get
    a partial dict and don't crash."""
    articles = [_fake_article(i) for i in range(5)]
    # Only return reviews for 2 out of 5
    fake = _fake_complete_factory({articles[0].id[:12]: "a", articles[2].id[:12]: "c"})

    out = collect_article_reviews(
        articles, topic_label="t", complete_fn=fake, batch_size=25
    )
    assert out == {articles[0].id: "a", articles[2].id: "c"}


def test_collect_reviews_tolerates_llm_exception_on_one_batch(monkeypatch) -> None:
    """One batch failing must not lose reviews from successful batches."""
    articles = [_fake_article(i) for i in range(40)]

    call_count = [0]

    def flaky_complete(*, system, user, tier="smart", **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("transient")
        # Return reviews for everything in the user prompt
        lines = []
        for a in articles:
            if a.id[:12] in user:
                lines.append(f"- [id={a.id[:12]}]: ok-{a.id[:4]}")
        return LLMResponse(
            text="\n".join(lines),
            message_id="m",
            input_tokens=1,
            output_tokens=1,
            trace_id=None,
        )

    out = collect_article_reviews(
        articles, topic_label="t", complete_fn=flaky_complete, batch_size=25
    )
    # First batch (25 articles) failed → those 25 are missing.
    # Second batch (15 articles) succeeded → those 15 are present.
    assert len(out) == 15
    # Ensure no crash, just partial result.


# ---------------------------------------------------------------------------
# Pre-pass result threading to the renderer
# ---------------------------------------------------------------------------


@pytest.fixture
def force_provider(monkeypatch):
    """Tests below don't actually hit a provider — they patch complete() — but
    the env still needs ISBE_LLM_PROVIDER set so client init code does not break."""
    monkeypatch.setenv("ISBE_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub")
