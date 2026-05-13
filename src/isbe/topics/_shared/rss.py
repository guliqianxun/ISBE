"""Generic RSS collector — config-driven, topic-agnostic.

Reads `topic.yaml`'s `rss:` block:

    rss:
      feeds:
        - name: cycleworld
          url: https://www.cycleworld.com/rss/
        - name: rideapart
          url: https://www.rideapart.com/rss/
      include_keywords: [200cc, 250cc, ...]      # OR-joined ILIKE against headline+summary
      exclude_keywords: [scooter, moped, 50cc]   # any hit drops the entry
      lookback_days: 14                          # ignore entries older than this
      lang: zh                                   # default per-topic; per-feed override possible
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import feedparser
import httpx
from prefect import flow, task
from sqlalchemy import or_

from isbe.facts.articles import Article
from isbe.facts.db import make_session_factory
from isbe.observability.runs import topic_run
from isbe.topics.registry import default_topics_root, load_topic_config

_DEFAULT_LOOKBACK_DAYS = 14
_USER_AGENT = "ISBE-rss/0.1 (+https://github.com/anthropics/claude-code)"


def _article_id(source: str, url: str) -> str:
    return hashlib.sha1(f"{source}|{url}".encode()).hexdigest()


def _parse_when(entry: dict) -> datetime:
    """Pick the most reliable timestamp out of an RSS entry."""
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=UTC)
    # last-ditch: now (collectors run roughly when the article goes live anyway)
    return datetime.now(UTC)


def _matches_filters(text: str, include: list[str], exclude: list[str]) -> bool:
    low = text.lower()
    if exclude and any(kw.lower() in low for kw in exclude):
        return False
    if not include:
        return True
    return any(kw.lower() in low for kw in include)


def _entry_to_article(
    entry: dict,
    *,
    topic_id: str,
    source: str,
    lang: str,
) -> Article | None:
    url = entry.get("link", "").strip()
    headline = (entry.get("title") or "").strip()
    if not url or not headline:
        return None
    summary = (entry.get("summary") or entry.get("description") or "").strip()
    # feedparser may bake HTML into summary; keep raw for now and let the digester
    # truncate. Stripping HTML adds a dep, doing it lazy.
    return Article(
        id=_article_id(source, url),
        topic_id=topic_id,
        source=source,
        published_at=_parse_when(entry),
        headline=headline[:1024],
        url=url[:2048],
        summary=summary[:8000] or None,
        tags=[],
        lang=lang,
    )


@task
def fetch_feed(url: str) -> list[dict]:
    resp = httpx.get(url, headers={"User-Agent": _USER_AGENT}, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    return feedparser.parse(resp.text).entries


def _upsert(session, articles: list[Article]) -> int:
    n = 0
    for a in articles:
        if session.get(Article, a.id) is None:
            session.add(a)
            n += 1
    session.commit()
    return n


@flow(name="rss-collector")
def rss_collector(topic_id: str) -> int:
    """Fetch all feeds for `topic_id`, filter, upsert into `articles`. Returns new-row count."""
    cfg = load_topic_config(default_topics_root(), topic_id)
    rcfg = cfg.get("rss") or {}
    feeds = rcfg.get("feeds") or []
    if not feeds:
        raise ValueError(f"topic {topic_id}: no `rss.feeds:` configured")
    include = rcfg.get("include_keywords") or []
    exclude = rcfg.get("exclude_keywords") or []
    exclude_url_patterns = rcfg.get("exclude_url_patterns") or []
    lookback_days = int(rcfg.get("lookback_days", _DEFAULT_LOOKBACK_DAYS))
    lang = rcfg.get("lang", "en")
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

    with topic_run(topic_id, "rss-collector") as run:
        fetched = 0
        new_total = 0
        per_feed: dict[str, int] = {}
        for feed in feeds:
            name = feed["name"]
            url = feed["url"]
            try:
                entries = fetch_feed(url)
            except Exception as e:  # tolerate per-feed failure
                run.payload.setdefault("errors", []).append(f"{name}: {e!s}")
                per_feed[name] = -1
                continue
            kept: list[Article] = []
            for e in entries:
                art = _entry_to_article(e, topic_id=topic_id, source=name, lang=lang)
                if art is None:
                    continue
                if art.published_at < cutoff:
                    continue
                if exclude_url_patterns and any(p in art.url for p in exclude_url_patterns):
                    continue
                if not _matches_filters(f"{art.headline}\n{art.summary or ''}", include, exclude):
                    continue
                kept.append(art)
            fetched += len(entries)
            Session = make_session_factory()
            with Session() as s:
                added = _upsert(s, kept)
            per_feed[name] = added
            new_total += added
        run.payload["fetched_entries"] = fetched
        run.payload["new_articles"] = new_total
        run.payload["per_feed"] = per_feed
        run.payload["include_keywords_n"] = len(include)
        run.payload["exclude_keywords_n"] = len(exclude)
        return new_total


def articles_keyword_filter(keywords: list[str]):
    """SQLAlchemy OR filter on Article.headline + summary. None if empty."""
    if not keywords:
        return None
    clauses = []
    for kw in keywords:
        like = f"%{kw}%"
        clauses.append(Article.headline.ilike(like))
        clauses.append(Article.summary.ilike(like))
    return or_(*clauses)
