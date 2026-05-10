"""Unit tests for NVDA news RSS collector — httpx + feedparser mocked."""
from isbe.topics.nvda.collectors.news import (
    entry_to_news_item,
    news_id_for,
)

SAMPLE_ENTRY = {
    "title": "NVIDIA reports Q1 record revenue",
    "link": "https://reuters.com/articles/nvda-q1-record-revenue",
    "published": "Tue, 06 May 2026 21:00:00 GMT",
    "summary": "NVIDIA Corp reported record Q1 revenue driven by data center.",
}


def test_news_id_is_stable_sha1():
    a = news_id_for(source="reuters", url="https://reuters.com/x")
    b = news_id_for(source="reuters", url="https://reuters.com/x")
    assert a == b
    assert len(a) == 40  # sha1 hex


def test_entry_to_news_item_extracts_fields():
    item = entry_to_news_item(SAMPLE_ENTRY, source="reuters")
    assert item.source == "reuters"
    assert item.headline.startswith("NVIDIA reports")
    assert item.url == "https://reuters.com/articles/nvda-q1-record-revenue"
    assert item.tickers == ["NVDA"]
    assert item.published_at.year == 2026
