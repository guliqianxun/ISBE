"""Bounded, deterministic reads for configured sources.
Discovery never leaves its seed origin.

Source URLs are fetched public-only: a literal address must be public, a host
name's every address is checked and the connection pinned to one of them
(`core.netguard.PublicResolver`). Redirects are never followed.

Feeds go through the shared feed parser (`isbe.collect.feed`), which honours a
declared or Content-Type charset; RSS 2 `updated` values are recovered from
that parse instead of re-tokenizing the feed as UTF-8 HTML, which would fail
every non-UTF-8 RSS 2 feed. Pages stay UTF-8 only.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

import httpx

from isbe.collect import feed as feeds
from isbe.collect import timestamps
from isbe.collect.items import squash
from isbe.core import http, netguard, validators

from . import pages
from .pages import Page, PageError, human_title, plain_text

MAX_BODY = 2 << 20
TIMEOUT = 15.0
# The caller processes eight candidates; a ninth signals that discovery was capped.
DISCOVERY_LIMIT = 9

# Reason phrases shown after the status code in error messages.
_REASONS = {
    100: "Continue", 101: "Switching Protocols", 102: "Processing", 103: "Early Hints",
    200: "OK", 201: "Created", 202: "Accepted", 203: "Non Authoritative Information",
    204: "No Content", 205: "Reset Content", 206: "Partial Content", 207: "Multi-Status",
    208: "Already Reported", 226: "IM Used", 300: "Multiple Choices", 301: "Moved Permanently",
    302: "Found", 303: "See Other", 304: "Not Modified", 305: "Use Proxy",
    307: "Temporary Redirect", 308: "Permanent Redirect",
}

validate_url = validators.validate_url  # raises validators.Invalid


class Kind(StrEnum):
    RSS = "rss"
    PAGE = "page"


@dataclass(frozen=True)
class Entry:
    url: str
    title: str
    summary: str | None = None
    published_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class ReadResult:
    entries: list[Entry]
    response_url: str
    content_type: str
    raw: bytes
    truncated: bool


@dataclass(frozen=True)
class Candidate:
    name: str
    url: str
    kind: Kind


class ReaderError(Exception):
    pass


def _content_type(response: httpx.Response) -> str:
    """The first Content-Type, or "" when it is not visible ASCII (http's `to_str`)."""
    for name, value in response.headers.raw:
        if name.lower() == b"content-type":
            return value.decode() if all(b == 9 or 32 <= b < 127 for b in value) else ""
    return ""


def fetch(url: str, *, resolver: http.Resolver | None = None) -> tuple[str, str, bytes]:
    """GET a source once: (validated URL, Content-Type, body of at most 2 MiB).

    `resolver` replaces the public-only resolver (tests)."""
    try:
        url = netguard.public_url(url)
        client = http.Http(
            TIMEOUT, max_redirects=0, resolver=resolver or netguard.PublicResolver(timeout=TIMEOUT)
        )
        try:
            # Redirects require a new explicitly configured URL; never follow a source to another host.
            with client.stream("GET", url, follow_redirects=False) as response:
                status = response.status_code
                if status >= 400:
                    raise ReaderError(f"http status: {status}")
                if status != 200:
                    reason = _REASONS.get(status, "<unknown status code>")
                    raise ReaderError(
                        f"source returned HTTP {status} {reason}; redirects are not followed"
                    )
                content_type = _content_type(response)
                try:
                    raw = http.read_capped(response, MAX_BODY)
                except Exception as error:
                    raise ReaderError("read source body (maximum 2 MiB)") from error
        finally:
            client.close()
    except (netguard.Blocked, TimeoutError) as error:
        # netguard words its refusals for article pages; name the source instead.
        raise ReaderError(str(error).replace("original-page", "source")) from None
    return url, content_type, raw


def read(
    url: str,
    kind: Kind,
    link_pattern: str,
    max_items: int,
    *,
    resolver: http.Resolver | None = None,
) -> ReadResult:
    """Fetch a source and list its entries: feed items, or a page's article links
    (`link_pattern` substring, else dated article paths), at most `max_items`."""
    if (
        not 1 <= max_items <= 200
        or len(link_pattern.encode()) > 512
        or any(validators.is_control(c) for c in link_pattern)
    ):
        raise ReaderError(
            "max_items must be 1–200; link_pattern must be at most 512 bytes without controls"
        )
    response_url, content_type, raw = fetch(url, resolver=resolver)
    if kind == Kind.RSS:
        entries, truncated = parse_feed(raw, response_url, max_items, content_type=content_type)
    else:
        entries, truncated = page_entries(_page(raw), response_url, link_pattern, max_items)
    return ReadResult(entries, response_url, content_type, raw, truncated)


def discover(url: str, *, resolver: http.Resolver | None = None) -> list[Candidate]:
    """Feed and listing candidates for a seed URL, all on its origin (at most nine)."""
    url, content_type, raw = fetch(url, resolver=resolver)
    # A user may supply a feed directly instead of a website home page.
    try:
        parsed = feeds.parse(raw, content_type=content_type)
    except feeds.FeedError:
        return discover_html(_page(raw), url)
    name = plain_text(parsed.title) if parsed.title is not None else ""
    return [Candidate(name or url, url, Kind.RSS)]


def _page(raw: bytes) -> Page:
    try:
        return pages.parse(raw)
    except PageError as error:
        raise ReaderError(str(error)) from error.__cause__


def resolve(base: str, href: str, same_origin: bool) -> str | None:
    """`href` against `base`, fragment dropped, as a valid source URL (on `base`'s
    origin when `same_origin`); None for anything unsafe or unusable."""
    href = validators.trim(href)
    if (
        not href
        or href.startswith("#")
        or "\\" in href
        or any(validators.is_space(c) or validators.is_control(c) for c in href)
        or ("//" in href and "@" in _authority(href.split("//", 1)[1]))
    ):
        return None
    try:
        joined = validators.whatwg(href, base).href.split("#", 1)[0]
        canonical = validate_url(joined)
    except (ValueError, validators.Invalid):
        return None
    if same_origin and validators.whatwg(canonical).origin != validators.whatwg(base).origin:
        return None
    return canonical


def _authority(rest: str) -> str:
    for stop in "/?#":
        rest = rest.split(stop, 1)[0]
    return rest


def _same_instant(explicit: str | None, updated: datetime | None) -> bool:
    """Whether an RSS `updated` text, read strictly (RFC 3339, else RFC 2822), is `updated`."""
    if explicit is None or updated is None:
        return False
    value = validators.trim(explicit)
    when = timestamps.rfc3339(value) or timestamps.rfc2822(value)
    leap = isinstance(updated, timestamps.Leap)
    return when is not None and when == updated and isinstance(when, timestamps.Leap) == leap


def parse_feed(
    raw: bytes, url: str, max_items: int, *, content_type: str | None = None
) -> tuple[list[Entry], bool]:
    try:
        parsed = feeds.parse(raw, base=url, content_type=content_type)
    except feeds.FeedError as error:
        raise ReaderError("source is not a valid RSS or Atom feed") from error
    # feed-rs copies RSS pubDate into updated when it is absent, so only an
    # explicit value that parses to the same instant is reported.
    rss2 = parsed.type == feeds.FeedType.RSS2
    entries: list[Entry] = []
    seen: set[str] = set()
    for item in parsed.entries:
        alternates = (x for x in item.links if x.rel is None or x.rel == "alternate")
        link = next((u for x in alternates if (u := resolve(url, x.href, False)) is not None), None)
        if link is None:
            continue
        title = plain_text(item.title) if item.title is not None else ""
        if not title or link in seen:
            continue
        seen.add(link)
        if len(entries) == max_items:
            return entries, True
        raw_summary = item.summary if item.summary is not None else item.content
        summary = plain_text(raw_summary) if raw_summary is not None else ""
        updated = item.updated
        if rss2 and not _same_instant(item.updated_text, updated):
            updated = None
        entries.append(
            Entry(
                url=link,
                title=title,
                summary=summary or None,
                published_at=None if item.published is None else timestamps.format_rfc3339(item.published),
                updated_at=None if updated is None else timestamps.format_rfc3339(updated),
            )
        )
    return entries, False


def page_entries(page: Page, url: str, pattern: str, max_items: int) -> tuple[list[Entry], bool]:
    # shortcut: listing links only; add explicit per-site date/full-text extraction when connecting reports.
    entries: list[Entry] = []
    seen: set[str] = set()
    truncated = page.truncated
    for link in page.links:
        found = resolve(url, link.href, True)
        if found is None:
            continue
        title = squash(link.text)
        if not human_title(title) or found == url or found in seen:
            continue
        seen.add(found)
        if pattern:
            selected = pattern in found
        else:
            path = validators.whatwg(found).pathname
            selected = dated_path(path) and article_path(path)
        if not selected:
            continue
        if len(entries) == max_items:
            return entries, True
        entries.append(Entry(found, title))
    return entries, truncated


def discover_html(page: Page, url: str) -> list[Candidate]:
    result: list[Candidate] = []
    seen: set[str] = set()
    for link in page.feeds:
        found = resolve(url, link.href, True)
        if found is not None and found not in seen:
            seen.add(found)
            result.append(Candidate(squash(link.text) or found, found, Kind.RSS))
        if len(result) == DISCOVERY_LIMIT:
            return result
    if page_entries(page, url, "", 1)[0] and url not in seen:
        seen.add(url)
        result.append(Candidate(squash(page.title) or url, url, Kind.PAGE))
    for link in page.links:
        if len(result) == DISCOVERY_LIMIT:
            break
        found = resolve(url, link.href, True)
        if found is None:
            continue
        path = validators.ascii_lower(validators.whatwg(found).pathname)
        name = squash(link.text)
        if (
            (path.endswith("/") or path.endswith(("/index.html", "/index.htm", "/index.shtml")))
            and human_title(name)
            and found not in seen
        ):
            seen.add(found)
            result.append(Candidate(name, found, Kind.PAGE))
    return result


def article_path(path: str) -> bool:
    last = validators.ascii_lower(path.rstrip("/").rsplit("/", 1)[-1])
    return "." not in last or last.rsplit(".", 1)[1] in ("html", "htm", "shtml", "aspx")


def _valid_date(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def dated_path(path: str) -> bool:
    """A calendar date (1900–2199) as 8 digits or `YYYY-MM-DD` / `YYYY/MM/DD`,
    not part of a longer number."""
    data = path.encode()
    digit = bytes.isdigit
    for start in range(len(data)):
        if start > 0 and digit(data[start - 1 : start]):
            continue
        for length in (8, 10):
            value = data[start : start + length]
            if len(value) < length or digit(data[start + length : start + length + 1]):
                continue
            if length == 10:
                if value[4] not in b"/-" or value[4] != value[7]:
                    continue
                value = value[:4] + value[5:7] + value[8:]
            if not value.isdigit():
                continue
            year = int(value[:4])
            if 1900 <= year <= 2199 and _valid_date(year, int(value[4:6]), int(value[6:])):
                return True
    return False
