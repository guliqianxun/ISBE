"""SearXNG requests and readiness.

SearXNG is a configured service, so it goes through `core.http.Http` (proxy
settings apply), never through the public-only fetcher. Redirects are not
followed and a failure is reported without the provider's body. Baidu was
removed on 2026-10-21; see `plans.Provider`.
"""

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from isbe.core import http, settings, validators
from isbe.core.errors import HttpError

from .plans import Provider, SearchPlan, parse_date

MAX_BODY = 2 << 20
TIMEOUT = 30.0
_SHANGHAI = ZoneInfo("Asia/Shanghai")


class ProviderError(Exception):
    """A failed provider request; the message never contains the provider's response."""


def today() -> date:
    """The calendar date in Asia/Shanghai, which recency hints count back from."""
    return datetime.now(_SHANGHAI).date()


def availability(conn: sqlite3.Connection) -> dict[str, Any]:
    """Whether SearXNG is configured."""
    return {"searxng": {"configured": settings.get("SEARXNG_URL") is not None}}


def preflight(conn: sqlite3.Connection, plan: SearchPlan) -> None:
    """Refuse a run SearXNG cannot start (missing or invalid setting)."""
    searxng_url()


def searxng_url() -> str:
    base = settings.get("SEARXNG_URL")
    if base is None:
        raise HttpError(400, "Configure SEARXNG_URL before searching")
    try:
        base = validators.validate_url(base)
    except validators.Invalid:
        raise HttpError(400, "SEARXNG_URL must be an HTTP(S) base URL without credentials") from None
    if validators.has_query(validators.whatwg(base)):
        raise HttpError(400, "SEARXNG_URL must not contain query parameters")
    return f"{base.rstrip('/')}/search"


# Search engines differ in date support; this is only a broad recency hint.
def time_range(start: str, today: date) -> str | None:
    try:
        first = parse_date(start)
        if first > today:
            return None
        if first >= today - timedelta(days=29):
            return "month"
        if first >= today - timedelta(days=364):
            return "year"
    except (HttpError, OverflowError):
        pass
    return None


def current_time_range(plan: SearchPlan) -> str | None:
    if plan.provider != Provider.SEARXNG:
        return None
    return time_range(plan.start_date, today())


_FORM_SAFE = frozenset(b"*-._0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")


def form_encode(pairs: list[tuple[str, str]]) -> str:
    """application/x-www-form-urlencoded: spaces as `+`; only alphanumerics and `*-._` stay plain."""

    def encode(text: str) -> str:
        return "".join(
            chr(b) if b in _FORM_SAFE else "+" if b == 0x20 else f"%{b:02X}" for b in text.encode()
        )

    return "&".join(f"{encode(k)}={encode(v)}" for k, v in pairs)


def request(
    plan: SearchPlan, query: str, *, transport: httpx.BaseTransport | None = None
) -> bytes:
    """One provider request for `query`: the raw 200 response body, at most 2 MiB.

    `transport` replaces the network (tests)."""
    client = http.Http(TIMEOUT, max_redirects=0, transport=transport)
    try:
        fields = [("q", query), ("format", "json"), ("language", plan.language), ("pageno", "1")]
        hint = current_time_range(plan)
        if hint is not None:
            fields.append(("time_range", hint))
        exchange = client.stream("GET", f"{searxng_url()}?{form_encode(fields)}", follow_redirects=False)
        return _read(exchange)
    finally:
        client.close()


def _read(exchange: Any) -> bytes:
    # Never echo a provider error body or request headers into user-visible logs.
    try:
        with exchange as response:
            status = response.status_code
            if status >= 400:
                raise ProviderError(f"Search provider returned HTTP {status}; request was not retried")
            if status != 200:
                raise ProviderError(f"Search provider returned HTTP {status}; redirects are not followed")
            try:
                return http.read_capped(response, MAX_BODY)
            except Exception:
                raise ProviderError("Cannot read search response (maximum 2 MiB)") from None
    except ProviderError:
        raise
    except Exception:
        raise ProviderError(
            "Search provider request failed; check connectivity and service settings. "
            "Request was not retried"
        ) from None
