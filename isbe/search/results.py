"""SearXNG responses to hits.

A provider's date is kept only as `search_date_hint`; `published_at` starts
unknown and is filled from the original page, never from the search engine.
"""

import itertools
from dataclasses import dataclass
from typing import Any

from isbe.core import jsonfmt, validators

from .plans import SearchPlan


class ResponseError(Exception):
    """A provider response that cannot be used; the message never quotes it."""


@dataclass
class Parsed:
    hits: list[dict[str, Any]]
    rejected: int
    omitted: int
    limited: bool
    unresponsive_engines: int


def text(value: Any, field: str, limit: int) -> str | None:
    """A string field without control characters, at most `limit` characters, trimmed."""
    raw = value.get(field) if isinstance(value, dict) else None
    if not isinstance(raw, str):
        return None
    kept = "".join(itertools.islice((c for c in raw if not validators.is_control(c)), limit))
    return validators.trim(kept) or None


def _hit(row: Any, rank: int) -> dict[str, Any] | None:
    original = row.get("url") if isinstance(row, dict) else None
    if not isinstance(original, str):
        return None
    try:
        url = validators.validate_url(original.split("#", 1)[0])
    except validators.Invalid:
        return None
    title = text(row, "title", 1000)
    if title is None:
        return None
    host = validators.whatwg(url).hostname
    if not host:
        return None
    hint = text(row, "publishedDate", 200) or text(row, "pubdate", 200)
    return {
        "url": url,
        "title": title,
        "summary": text(row, "content", 8000),
        "publisher": text(row, "website", 200) or host,
        "search_date_hint": hint,
        "rank": rank,
        "published_at": None,
        "date_status": "unknown",
        "read_status": "not_checked",
    }


def parse(plan: SearchPlan, raw: bytes) -> Parsed:
    try:
        value = jsonfmt.loads(raw)
    except ValueError:
        raise ResponseError("Search provider did not return valid JSON") from None
    rows = value.get("results") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise ResponseError("Search provider response did not contain a results array")
    hits: list[dict[str, Any]] = []
    rejected = 0
    for index, row in enumerate(rows[: plan.max_results]):
        hit = _hit(row, index + 1)
        if hit is None:
            rejected += 1
        else:
            hits.append(hit)
    engines = value.get("unresponsive_engines")
    return Parsed(
        hits=hits,
        rejected=rejected,
        omitted=max(0, len(rows) - plan.max_results),
        limited=len(rows) >= plan.max_results,
        unresponsive_engines=len(engines) if isinstance(engines, list) else 0,
    )
