"""RSS 0.9x/1.0/2.0, Atom and JSON Feed.

A feed must yield the same entries, links, text and dates as it did in the
earlier Rust build, or stored items and their hashes would change. The
handlers cover the fields ISBE reads (title, language, entry links, title,
summary, content, dates, authors, categories), quirks included: RSS
`<comments>` links count as links, an RSS entry's missing `updated` copies
`published`, text is trimmed but never sanitized, and an Atom text construct of
an unknown `type` fails the whole feed. Extensions ISBE never reads (iTunes,
Podcast, MediaRSS) are skipped, so a malformed one no longer fails the feed.
Decoding follows `xmldecode`.
"""

import re
from datetime import datetime

from isbe.core import validators

from . import jsonfeed, timestamps, xmldecode
from .feedmodel import Entry, Feed, FeedError, FeedType, Link, link, person_name
from .xmlevents import (
    ATOM,
    COMMENTS,
    CONTENT,
    DUBLIN_CORE,
    RSS,
    Element,
    Source,
    XmlError,
)

__all__ = ["Entry", "Feed", "FeedError", "FeedType", "Link", "parse", "person_name"]


_TOKEN = r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*"
_MIME = re.compile(rf'{_TOKEN}/{_TOKEN}(?:[ \t]*;[ \t]*{_TOKEN}=(?:{_TOKEN}|"[^"]*"))*')


def _unsupported(mime: str, el: Element) -> FeedError:
    return FeedError(f"unable to parse feed: unsupported content type {mime} at position {el.pos}")


def _missing(what: str, el: Element) -> FeedError:
    return FeedError(f"unable to parse feed: missing {what} at position {el.pos}")


def _text(el: Element) -> str:
    """Atom text constructs (`handle_text`): trimmed text, or markup for xhtml."""
    kind = el.attr("type")
    if kind is None or kind in ("text", "html", "text/html"):
        return validators.trim(el.text_sloppy())
    if kind == "xhtml":
        return validators.trim(el.xhtml())
    raise _unsupported(kind, el)


def _plain(el: Element) -> str | None:
    """`common::handle_text`: the leading text, trimmed."""
    found = el.text()
    return None if found is None else validators.trim(found)


def _timestamp(el: Element) -> datetime | None:
    found = el.text()
    return None if found is None else timestamps.lenient(found)


def _atom_content(el: Element) -> str | None:
    kind = el.attr("type")
    if el.attr("src") is not None:
        if kind is not None and not _MIME.fullmatch(kind):
            raise _unsupported(kind, el)
        if el.text() is not None:
            raise _missing("non-empty atom:content with src", el)
        return None
    if kind is None or kind in ("text", "html", "xhtml", "text/html"):
        return _text(el)
    if kind.endswith(" +xml") or kind.endswith("/xml"):
        return _text(el)  # read as text constructs, which rejects the type
    if not _MIME.fullmatch(kind):
        raise _unsupported(kind, el)
    body = el.text()
    if body is None:
        raise _missing("content.inline", el)
    return body


def _atom_entry(el: Element) -> Entry:
    entry = Entry()
    for child in el.children():
        match child.tag():
            case (ns, "id") if ns == ATOM:
                if (found := child.text()) is not None:
                    entry.id = found
            case (ns, "title") if ns == ATOM:
                entry.title = _text(child)
            case (ns, "updated") if ns == ATOM:
                if (found := child.text()) is not None:
                    entry.updated = timestamps.lenient(found)
            case (ns, "author") if ns == ATOM:
                names = [c.text() if c.name == "name" else None for c in child.children()]
                if named := [n for n in names if n is not None]:
                    entry.authors.append(named[-1])
            case (ns, "content") if ns == ATOM:
                entry.content = _atom_content(child)
            case (ns, "link") if ns == ATOM:
                if (href := child.attr("href")) is not None:
                    rel = child.attr("rel")
                    entry.links.append(Link(link(href, child.base), "alternate" if rel is None else rel))
            case (ns, "summary") if ns == ATOM:
                entry.summary = _text(child)
            case (ns, "category") if ns == ATOM:
                if (term := child.attr("term")) is not None:
                    entry.categories.append(validators.trim(term))
            case (ns, "published" | "pubDate") if ns == ATOM:
                if (found := child.text()) is not None:
                    entry.published = timestamps.lenient(found)
            case (ns, "rights") if ns == ATOM:
                _text(child)
            case (ns, "commentRss" | "commentRSS") if ns == COMMENTS:
                if (found := child.text()) is not None:
                    entry.links.append(Link(link(found, child.base)))
    return entry


def _atom(root: Element) -> Feed:
    feed = Feed(FeedType.ATOM, language=root.attr("xml:lang"))
    for child in root.children():
        match child.tag():
            case (ns, "title") if ns == ATOM:
                feed.title = _text(child)
            case (ns, "rights" | "subtitle") if ns == ATOM:
                _text(child)
            case (ns, "entry") if ns == ATOM:
                feed.entries.append(_atom_entry(child))
    return feed


def _rss_item(el: Element) -> Entry:
    entry = Entry()
    for child in el.children():
        if child.name == "updated":
            # Kept apart for the reader: a missing `updated` is filled from `published`.
            explicit = child.text()
            entry.updated_text = (entry.updated_text or "") + (explicit or "")
            if child.ns in (RSS, ATOM):
                entry.updated = None if explicit is None else timestamps.lenient(explicit)
            continue
        match child.tag():
            case (ns, "title") if ns == RSS:
                entry.title = _plain(child)
            case (ns, "link") if ns == RSS:
                if (found := child.text()) is not None:
                    entry.links.append(Link(link(found, child.base)))
            case (ns, "description") if ns == RSS:
                entry.summary = _plain(child)
            case (ns, "author") if ns == RSS:
                if (found := child.text()) is not None and (name := person_name(found)) is not None:
                    entry.authors.append(name)
            case (ns, "category") if ns == RSS:
                if (found := child.text()) is not None:
                    entry.categories.append(validators.trim(found))
            case (ns, "guid") if ns == RSS:
                if (found := child.text()) is not None:
                    entry.id = validators.trim(found)
            case (ns, "pubDate") if ns == RSS:
                entry.published = _timestamp(child)
            case (ns, "date") if ns == DUBLIN_CORE:
                entry.published = _timestamp(child)
            case (ns, "encoded") if ns == CONTENT:
                found = child.text()
                entry.content = found or None
            case (ns, "creator") if ns == DUBLIN_CORE:
                if (found := child.text()) is not None and (name := person_name(found)) is not None:
                    entry.authors.append(name)
            case (ns, "commentRss" | "commentRSS") if ns == COMMENTS:
                if (found := child.text()) is not None:
                    entry.links.append(Link(link(found, child.base)))
            case (ns, "comments") if ns == RSS:
                if (found := child.text()) is not None:
                    entry.links.append(Link(link(found, child.base), comments=True))
    if entry.updated is None and entry.published is not None:
        entry.updated = entry.published
    # Comment links that only repeat another link are dropped.
    targets = {item.href for item in entry.links if not item.comments}
    entry.links = [item for item in entry.links if not item.comments or item.href not in targets]
    return entry


def _rss2(root: Element, kind: FeedType) -> Feed:
    channel = next((c for c in root.children() if c.name == "channel"), None)
    if channel is None:
        raise FeedError("unable to parse feed: no root element")
    feed = Feed(kind)
    for child in channel.children():
        match child.tag():
            case (ns, "title") if ns == RSS:
                feed.title = _plain(child)
            case (ns, "language") if ns == RSS:
                found = child.text()
                feed.language = None if found is None else found.lower()
            case (ns, "item") if ns == RSS:
                feed.entries.append(_rss_item(child))
    return feed


def _rss1_item(el: Element) -> Entry | None:
    entry = Entry()
    encoded: str | None = None
    for child in el.children():
        match child.tag():
            case (ns, "title") if ns == RSS:
                entry.title = _plain(child)
            case (ns, "link") if ns == RSS:
                if (found := child.text()) is not None:
                    entry.links.append(Link(link(found, child.base)))
            case (ns, "description") if ns == RSS:
                entry.summary = _plain(child)
            case (ns, "encoded") if ns == CONTENT:
                encoded = _plain(child)
            case (ns, "creator") if ns == DUBLIN_CORE:
                if (found := child.text()) is not None and (name := person_name(found)) is not None:
                    entry.authors.append(name)
            case (ns, "date") if ns == DUBLIN_CORE:
                entry.published = _timestamp(child)
            case (ns, "description") if ns == DUBLIN_CORE and entry.summary is None:
                entry.summary = _plain(child)
    if entry.content is None and encoded is not None:
        entry.content = encoded
    return entry if entry.links else None


def _rss1(root: Element) -> Feed:
    feed = Feed(FeedType.RSS1)
    for child in root.children():
        match child.tag():
            case (ns, "channel") if ns == RSS:
                for item in child.children():
                    match item.tag():
                        case (ns, "title") if ns == RSS:
                            feed.title = _plain(item)
                        case (ns, "language") if ns == DUBLIN_CORE:
                            feed.language = item.text()
            case (ns, "item") if ns == RSS:
                if (entry := _rss1_item(child)) is not None:
                    feed.entries.append(entry)
    return feed


def _xml(raw: bytes, base: str | None, content_type: str | None) -> Feed:
    try:
        text, tail = xmldecode.decode(raw, content_type), None
    except xmldecode.DecodeError as error:
        # Parsing failed when it reached the chunk it could not decode.
        text, tail = error.text, f"Parser error: {error}"
    try:
        source = Source(text, None if base is None else validators.whatwg(base).href, tail)
        root = source.root()
        version = None if root is None else root.attr("version")
        if root is not None and root.name in ("feed", "entry"):
            source.set_default_ns(ATOM)
            if root.name == "feed":
                return _atom(root)
            return Feed(FeedType.ATOM, entries=[_atom_entry(root)])
        if root is not None and root.name == "rss" and version in ("2.0", "", "0.91", "0.92"):
            source.set_default_ns(RSS)
            return _rss2(root, FeedType.RSS2 if version in ("2.0", "") else FeedType.RSS0)
        if root is not None and root.name == "RDF":
            source.set_default_ns(RSS)
            return _rss1(root)
    except XmlError as error:
        # The XML error appears twice in the chain: in the message and as its cause.
        raise FeedError(f"unable to parse XML: {error}") from error
    raise FeedError("unable to parse feed: no root element")


def parse(raw: bytes, *, base: str | None = None, content_type: str | None = None) -> Feed:
    """A feed from its raw bytes; `base` resolves relative links, `content_type` is
    the HTTP header (an encoding fallback, see `xmldecode`). Raises `FeedError`."""
    # Only the first 8 KiB is searched for the first `<` or `{`.
    head = raw[:8192]
    lt, brace = head.find(b"<"), head.find(b"{")
    if lt == -1 and brace == -1:
        raise FeedError("unable to parse feed: no root element")
    if lt == -1 or (brace != -1 and brace < lt):
        return jsonfeed.parse(raw)
    return _xml(raw, base, content_type)
