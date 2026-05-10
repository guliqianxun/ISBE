"""RSS-driven NVDA news collector."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import feedparser
import httpx
from prefect import flow

from isbe.facts.db import make_session_factory
from isbe.observability.runs import topic_run
from isbe.topics.nvda.facts import NewsItem
from isbe.topics.registry import default_topics_root, load_topic_config

TOPIC_ID = "nvda"
DEFAULT_TICKERS = ["NVDA"]


def news_id_for(*, source: str, url: str) -> str:
    """sha1(source + '\\0' + url) — stable across collector reruns."""
    h = hashlib.sha1()
    h.update(source.encode("utf-8"))
    h.update(b"\0")
    h.update(url.encode("utf-8"))
    return h.hexdigest()


def _parse_published(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(UTC)
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def entry_to_news_item(entry: dict, *, source: str, tickers: list[str] | None = None) -> NewsItem:
    url = entry.get("link", "")
    return NewsItem(
        id=news_id_for(source=source, url=url),
        source=source,
        published_at=_parse_published(entry.get("published")),
        headline=entry.get("title", "").strip(),
        url=url,
        body=entry.get("summary"),
        tickers=tickers or DEFAULT_TICKERS,
        lang=entry.get("language", "en"),
    )


def upsert_news(session, items: list[NewsItem]) -> int:
    n = 0
    for it in items:
        if session.get(NewsItem, it.id) is None:
            session.add(it)
            n += 1
    session.commit()
    return n


@flow(name="nvda-news-collector")
def nvda_news_collector() -> int:
    """Fetch each RSS feed declared in topic.yaml, upsert as NewsItem rows."""
    cfg = load_topic_config(default_topics_root(), TOPIC_ID)
    rss_cfg = cfg.get("news_rss", {}) or {}
    feeds: list[dict] = list(rss_cfg.get("feeds", []))

    with topic_run(TOPIC_ID, "nvda-news-collector") as run:
        Session = make_session_factory()
        total_new = 0
        with Session() as s:
            for feed in feeds:
                source = feed["source"]
                url = feed["url"]
                try:
                    resp = httpx.get(url, timeout=30.0, follow_redirects=True)
                    resp.raise_for_status()
                except httpx.HTTPError as e:
                    print(f"[nvda-news] skip {source}: {e}")
                    continue
                parsed = feedparser.parse(resp.text)
                items = [entry_to_news_item(e, source=source) for e in parsed.entries]
                total_new += upsert_news(s, items)
        run.payload["feeds"] = [f["source"] for f in feeds]
        run.payload["new_items"] = total_new
        return total_new


if __name__ == "__main__":
    print(nvda_news_collector())
