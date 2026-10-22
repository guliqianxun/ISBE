"""RSS / Atom feeds, with v1's filters: lookback window, URL substrings,
keywords.

Feed URLs come from topic files and lead to third-party sites, so they are
fetched public-only: every address checked and pinned, and each redirect
revalidated.
"""

from datetime import datetime, timedelta

from isbe.core import http, netguard, validators
from isbe.topics import Feed, Rss

from . import feed as feeds
from .items import CollectError, Item, checked, squash
from .timestamps import format_rfc3339

TIMEOUT = 90.0
MAX_REDIRECTS = 10


def fetcher() -> netguard.PublicFetcher:
    return netguard.PublicFetcher(timeout=TIMEOUT, max_redirects=MAX_REDIRECTS)


def fetch(
    client: netguard.PublicFetcher, feed: Feed, cfg: Rss, now: datetime
) -> list[Item]:
    try:
        response = checked(client.fetch(feed.url, max_bytes=http.MAX_BODY))
    except (netguard.Blocked, netguard.RedirectError, TimeoutError) as error:
        # netguard words its refusals for article pages; name the feed instead.
        raise CollectError(str(error).replace("original-page", "feed")) from None
    content_type = response.headers.get("content-type")
    return parse(response.content, feed, cfg, now, content_type=content_type)


def parse(
    xml: bytes, feed: Feed, cfg: Rss, now: datetime, *, content_type: str | None = None
) -> list[Item]:
    try:
        parsed = feeds.parse(xml, content_type=content_type)
    except feeds.FeedError:
        head = xml[:512].decode("utf-8", "replace").lower()
        if "<html" in head:
            raise CollectError(
                "got an HTML page, not a feed (the site blocks this client, or the URL is not a feed)"
            ) from None
        raise
    lang = parsed.language if parsed.language is not None else "und"
    cutoff = now - timedelta(days=cfg.lookback_days)
    excluded = [k.lower() for k in cfg.exclude_keywords]
    items = []
    for entry in parsed.entries:
        link = next((x for x in entry.links if x.rel is None or x.rel == "alternate"), None)
        if link is None or entry.title is None:
            continue
        url = validators.trim(link.href)
        title = squash(entry.title)
        # An undated entry counts as new: collection runs about when items go live.
        when = entry.published or entry.updated or now
        # Raw, possibly HTML; sanitized wherever it is rendered.
        raw = entry.summary if entry.summary is not None else entry.content
        summary = None if raw is None else validators.trim(raw)[:8000] or None
        text = f"{title}\n{summary or ''}".lower()
        if (
            not url
            or not title
            or when < cutoff
            or any(p in url for p in cfg.exclude_url_patterns)
            or any(k in text for k in excluded)
        ):
            continue
        items.append(
            Item(
                id=f"rss:{url}",
                kind="article",
                source=f"rss:{feed.name}",
                title=title,
                summary=summary,
                url=url,
                authors=list(entry.authors),
                lang=lang,
                published_at=format_rfc3339(when),
            )
        )
    return items
