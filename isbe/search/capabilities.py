"""What a run through SearXNG or each fixed-source kind can enforce .

Each constraint a research scope may ask for has a status:

- `supported`: the run enforces it, in the request or on the response it reads;
- `unsupported`: the run cannot ask for it; put it in the query terms, or apply it
  to the results afterwards;
- `unknown`: the request may carry it, but nothing has checked that the source
  applies it. Unknown is never supported: check the results as for unsupported.

`basis` names what a status rests on: ISBE's own request code, the boundary
recorded in docs/source-interface.md, or that nothing has checked it. `sent` is
what ISBE puts in the request for it, if anything.

The descriptions are static. They read no settings, so they carry setting
names and never values (no keys, URLs or local paths), and whether a provider
is configured stays with `providers.availability`. A capability says nothing
about result quality or a source's credibility: no field here means results
are relevant, complete or reliable. Change a description together with
`REVISION` and examples/search/capabilities.json.
"""

import copy
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

from isbe.core.errors import HttpError
from isbe.sources import registry_config

from . import plans

REVISION = "2026-10-21"

SUPPORTED, UNSUPPORTED, UNKNOWN = "supported", "unsupported", "unknown"

# The constraints a description covers, in the order it lists them.
CONSTRAINTS = (
    "terms",
    "phrase",
    "boolean",
    "exclusion",
    "site",
    "title",
    "institution",
    "region",
    "language",
    "publication_window",
    "pages",
    "result_count",
    "total_estimate",
    "publication_dates",
)


def _field(status: str, basis: str, sent: str | None = None) -> dict[str, str]:
    field = {"status": status, "basis": basis}
    if sent is not None:
        field["sent"] = sent
    return field


_SYNTAX = (
    "ISBE sends the query text unchanged; whether the instance's engines honour this "
    "syntax has not been checked"
)
_NO_PARAMETER = "the request has no parameter for it; name it in the query terms or check the results"
_HINT_DATES = (
    "a date a provider returns is kept as search_date_hint, never as the publication date; "
    "date rechecks read the date from the original page"
)
_FIXED = "a fixed source reads one feed or listing page and takes no query"
_SEARCH_LIMITS = {
    "queries": [1, 8],
    "query_chars": 200,
    "result_count": [1, 20],
    "timeout_s": 30,
    "response_bytes": 2 << 20,
    "redirects": 0,
    "retries": 0,
}

_DESCRIPTIONS: dict[str, dict[str, Any]] = {
    "searxng": {
        "kind": "search",
        "requires": ["SEARXNG_URL"],
        "limits": _SEARCH_LIMITS,
        "constraints": {
            "terms": _field(
                SUPPORTED, "each plan query, normalized by query.compile, is sent as q", "q"
            ),
            "phrase": _field(UNKNOWN, _SYNTAX, "q"),
            "boolean": _field(UNKNOWN, _SYNTAX, "q"),
            "exclusion": _field(UNKNOWN, _SYNTAX, "q"),
            "site": _field(UNKNOWN, _SYNTAX, "q"),
            "title": _field(UNKNOWN, _SYNTAX, "q"),
            "institution": _field(UNSUPPORTED, _NO_PARAMETER),
            "region": _field(
                UNSUPPORTED,
                "the request has no region parameter; language zh-CN names a language "
                "variant, not a place",
            ),
            "language": _field(
                UNKNOWN,
                "SearXNG passes language to the engines that take one; results are not "
                "checked for their language",
                "language",
            ),
            "publication_window": _field(
                UNSUPPORTED,
                "only a recency hint: time_range=month or year when the window starts within "
                "30 or 365 days of the run, otherwise none (providers.time_range); exact dates "
                "come from date rechecks",
                "time_range",
            ),
            "pages": _field(UNSUPPORTED, "providers.request asks for the first page only", "pageno=1"),
            "result_count": _field(
                SUPPORTED,
                "results.parse keeps the first max_results rows and counts the ones it left out",
            ),
            "total_estimate": _field(UNSUPPORTED, "results.parse reads no total from the response"),
            "publication_dates": _field(UNSUPPORTED, _HINT_DATES),
        },
    },
    "rss": {
        "kind": "fixed",
        "requires": [],
        "limits": {"result_count": [1, 200]},
        "constraints": {
            **{
                name: _field(UNSUPPORTED, _FIXED)
                for name in (
                    "terms", "phrase", "boolean", "exclusion", "site", "title",
                    "institution", "region", "language",
                )
            },
            "publication_window": _field(
                UNSUPPORTED, "the reader lists the entries the feed shows, whatever their dates"
            ),
            "pages": _field(
                UNSUPPORTED, "the feed document only; entries it no longer lists are not fetched"
            ),
            "result_count": _field(SUPPORTED, "max_items entries per read (sources.reader.read)"),
            "total_estimate": _field(UNSUPPORTED, "a feed states no total"),
            "publication_dates": _field(
                SUPPORTED,
                "the dates the feed states for an entry are stored as its published_at and "
                "updated_at; they are the feed's statement, not checked against the article page",
            ),
        },
    },
    "page": {
        "kind": "fixed",
        "requires": [],
        "limits": {"result_count": [1, 200]},
        "constraints": {
            **{
                name: _field(UNSUPPORTED, _FIXED)
                for name in (
                    "terms", "phrase", "boolean", "exclusion", "site", "title",
                    "institution", "region", "language",
                )
            },
            "publication_window": _field(
                UNSUPPORTED, "the reader lists the article links the page shows, whatever their dates"
            ),
            "pages": _field(UNSUPPORTED, "one listing page; its pagination is not followed"),
            "result_count": _field(SUPPORTED, "max_items links per read (sources.reader.read)"),
            "total_estimate": _field(UNSUPPORTED, "a listing page states no total"),
            "publication_dates": _field(
                UNSUPPORTED, "a link carries no date, and none is guessed from its address"
            ),
        },
    },
}


def describe(name: str) -> dict[str, Any]:
    """The description of provider `searxng` or fixed-source kind `rss`/`page`."""
    if name not in _DESCRIPTIONS:
        raise KeyError(f"no capability description for {name!r}")
    return {"id": name, "revision": REVISION, **copy.deepcopy(_DESCRIPTIONS[name])}


def describe_all() -> dict[str, dict[str, Any]]:
    return {name: describe(name) for name in _DESCRIPTIONS}


def for_plan(conn: sqlite3.Connection, plan_id: str) -> dict[str, Any]:
    """A saved search plan's provider description, bound to the plan's revision.
    A plan still saved for Baidu (removed) gets the 400 that saving or running it gets."""
    plan = plans.get(conn, plan_id)
    provider = plans.field(plan["config"], "provider")
    if provider == plans.Provider.BAIDU:
        raise HttpError(400, plans.BAIDU_REMOVED)
    return {
        "plan": plan_id,
        "plan_revision": plan["revision"],
        "capabilities": describe(provider),
    }


def for_source(conn: sqlite3.Connection, source_id: str) -> dict[str, Any]:
    """A configured source's kind description, bound to the source's revision."""
    source = registry_config.get(conn, source_id)
    return {
        "source": source_id,
        "source_revision": source["revision"],
        "capabilities": describe(plans.field(source["config"], "kind")),
    }


def route(description: Mapping[str, Any], wanted: Iterable[str]) -> dict[str, list[str]]:
    """Split the constraints a scope wants: `executed` ones the run enforces, `deferred`
    ones to check in the results, and `unknown`, the deferred ones nobody has checked
    either way. A constraint the description does not list is unknown."""
    listed = description.get("constraints", {})
    routed: dict[str, list[str]] = {"executed": [], "deferred": [], "unknown": []}
    for name in dict.fromkeys(wanted):
        status = (listed.get(name) or {}).get("status", UNKNOWN)
        if status == SUPPORTED:
            routed["executed"].append(name)
            continue
        routed["deferred"].append(name)
        if status != UNSUPPORTED:
            routed["unknown"].append(name)
    return routed
