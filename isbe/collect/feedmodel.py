"""What ISBE reads of a parsed feed, and the helpers every format shares:
link resolution and author name parsing."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from isbe.core import validators

from .xmlevents import parse_uri


class FeedError(ValueError):
    pass


class FeedType(StrEnum):
    ATOM = "atom"
    JSON = "json"
    RSS0 = "rss0"
    RSS1 = "rss1"
    RSS2 = "rss2"


@dataclass
class Link:
    href: str
    rel: str | None = None
    comments: bool = False  # an RSS <comments> link


@dataclass
class Entry:
    id: str = ""
    title: str | None = None
    summary: str | None = None
    content: str | None = None  # the content body
    links: list[Link] = field(default_factory=list)
    published: datetime | None = None
    updated: datetime | None = None
    authors: list[str] = field(default_factory=list)  # names; nameless people are left out
    categories: list[str] = field(default_factory=list)
    # RSS 2 only: the text of the item's `*:updated` children, joined, or None.
    updated_text: str | None = None


@dataclass
class Feed:
    type: FeedType
    title: str | None = None
    language: str | None = None
    entries: list[Entry] = field(default_factory=list)


def link(href: str, base: str | None) -> str:
    """Resolved when it parses, then trimmed."""
    resolved = parse_uri(href, base)
    return validators.trim(href if resolved is None else resolved)


_EMAIL = re.compile(
    r"\b(<\[\()?(mailto:)?([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})(>]\))?\b"
)


def person_name(raw: str) -> str | None:
    """The name half of an author string (`Name <a@b.c>` and kin)."""
    found = _EMAIL.search(raw)
    if found is None:
        return validators.trim(raw)
    start, end = found.span()
    before, after = len(raw[:start].encode()), len(raw[end:].encode())
    if before > after:
        name = raw[: start - 1]
    elif end < len(raw):
        name = raw[end + 1 :]
    else:
        name = ""
    return validators.trim(name) or None


