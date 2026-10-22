"""JSON Feed 1.x. The whole document is checked against the feed structure
first (field types, required fields), and only then the version.

Type errors and missing fields keep the wording of the earlier Rust build
(`invalid type: ..., expected ...`, `missing field ...`), without its
"at line L column C" suffix; syntax errors carry Python's json wording.
"""

from typing import Any

from isbe.core import jsonfmt, validators
from isbe.topics.model import debug

from . import timestamps
from .feedmodel import Entry, Feed, FeedError, FeedType, Link, link, person_name

# Field kinds: `s` optional string, `s!` required string, `u64` optional
# unsigned integer, `[x]` optional list of x, `[x]!` required list, a struct
# name an optional struct; a `!` kind must be present and not null, and so
# must every element of a list.
_STRUCTS: dict[str, dict[str, str]] = {
    "JsonFeed": {
        "version": "s!", "title": "s!", "home_page_url": "s", "feed_url": "s",
        "language": "s", "description": "s", "icon": "s", "favicon": "s",
        "author": "JsonAuthor", "authors": "[JsonAuthor]", "items": "[JsonItem]!",
    },
    "JsonItem": {
        "id": "s", "url": "s", "external_url": "s", "title": "s", "content_html": "s",
        "content_text": "s", "summary": "s", "date_published": "s", "date_modified": "s",
        "author": "JsonAuthor", "authors": "[JsonAuthor]", "tags": "[s]",
        "attachments": "[JsonAttachment]",
    },
    "JsonAuthor": {"name": "s", "url": "s"},
    "JsonAttachment": {"url": "s!", "mime_type": "s!", "title": "s", "size_in_bytes": "u64"},
}


def _unexpected(value: Any) -> str:
    """A JSON value described for a type error."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return f"boolean `{'true' if value else 'false'}`"
    if isinstance(value, int):
        return f"integer `{value}`"
    if isinstance(value, str):
        return f"string {debug(value)}"
    if isinstance(value, list):
        return "sequence"
    if isinstance(value, dict):
        return "map"
    return f"floating point `{value}`"


def _error(message: str) -> FeedError:
    """A feed error whose cause carries the same message, so both show in the chain."""
    error = FeedError(f"unable to parse JSON: {message}")
    error.__cause__ = ValueError(message)
    return error


def _invalid(value: Any, expected: str) -> FeedError:
    return _error(f"invalid type: {_unexpected(value)}, expected {expected}")


def _check(value: Any, kind: str) -> None:
    required = kind.endswith("!")
    kind = kind.rstrip("!")
    if value is None and not required:
        return
    if kind.startswith("["):
        if not isinstance(value, list):
            raise _invalid(value, "a sequence")
        for item in value:
            _check(item, kind[1:-1].rstrip("!") + "!")
    elif kind == "s":
        if not isinstance(value, str):
            raise _invalid(value, "a string")
    elif kind == "u64":
        if isinstance(value, bool) or not isinstance(value, int):
            raise _invalid(value, "u64")
        if not 0 <= value < 2**64:
            raise _error(f"invalid value: integer `{value}`, expected u64")
    else:
        if not isinstance(value, dict):
            raise _invalid(value, f"struct {kind}")
        fields = _STRUCTS[kind]
        for key, item in value.items():
            if key in fields:
                _check(item, fields[key])
        for key, field_kind in fields.items():
            if field_kind.endswith("!") and key not in value:
                raise _error(f"missing field `{key}`")


def _authors(obj: dict[str, Any], into: list[str]) -> None:
    """`author`, then `authors`, each name once."""
    single, many = obj.get("author"), obj.get("authors")
    for author in ([] if single is None else [single]) + (many or []):
        name = author.get("name")
        if name is not None and name not in into:
            parsed = person_name(name)
            if parsed is not None:
                into.append(parsed)


def _item(item: dict[str, Any]) -> Entry:
    entry = Entry(id=item.get("id") or "")
    for key in ("url", "external_url"):
        if item.get(key) is not None:
            entry.links.append(Link(link(item[key], None)))
    if item.get("title") is not None:
        entry.title = validators.trim(item["title"])
    html, text, summary = item.get("content_html"), item.get("content_text"), item.get("summary")
    entry.content = None if html is None else validators.trim(html)
    entry.summary = None if summary is None else validators.trim(summary)
    if text is not None:
        if entry.content is None:
            entry.content = validators.trim(text)
        elif entry.summary is None:
            entry.summary = validators.trim(text)
    if item.get("date_published") is not None:
        entry.published = timestamps.lenient(item["date_published"])
    if item.get("date_modified") is not None:
        entry.updated = timestamps.lenient(item["date_modified"])
    _authors(item, entry.authors)
    entry.categories = [validators.trim(tag) for tag in item.get("tags") or []]
    for attachment in item.get("attachments") or []:
        entry.links.append(Link(link(attachment["url"], None)))
    return entry


def parse(raw: bytes) -> Feed:
    try:
        doc = jsonfmt.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise _error(str(error))  # noqa: B904 - the cause is set by _error
    _check(doc, "JsonFeed!")
    if not doc["version"].startswith("https://jsonfeed.org/version/1"):
        raise FeedError(f"unsupported version: {doc['version']}")
    feed = Feed(FeedType.JSON, title=validators.trim(doc["title"]), language=doc.get("language"))
    authors: list[str] = []
    _authors(doc, authors)
    for item in doc["items"]:
        entry = _item(item)
        if not entry.authors:
            entry.authors = list(authors)
        feed.entries.append(entry)
    return feed
