"""Request steps whose failure messages the stage stores in its stats.

The messages (`failed`, `abstract_fallback`, `figure_failures`) are a chain: the
step's context, then its cause, e.g. `fetch arXiv HTML: http status: 404`. A 4xx
or 5xx status is an error before any body is read, and an oversized body is
reported by its limit; `call` and `read` produce both, in the wording earlier
runs stored.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from isbe.core import http

# Canonical reason phrases of the statuses `status_line` can show.
_REASONS = {
    200: "OK", 201: "Created", 202: "Accepted", 203: "Non Authoritative Information",
    204: "No Content", 205: "Reset Content", 206: "Partial Content", 207: "Multi-Status",
    208: "Already Reported", 226: "IM Used", 300: "Multiple Choices",
    301: "Moved Permanently", 302: "Found", 303: "See Other", 304: "Not Modified",
    305: "Use Proxy", 307: "Temporary Redirect", 308: "Permanent Redirect",
}


class EnrichError(Exception):
    """A failed step; `errors.describe` gives the message with its causes."""


class StatusError(EnrichError):
    pass


@contextmanager
def step(message: str) -> Iterator[None]:
    """Wrap whatever the block raises in an `EnrichError` carrying `message` as context."""
    try:
        yield
    except Exception as error:
        raise EnrichError(message) from error


@contextmanager
def call(client: http.Http, method: str, url: str, **options: Any) -> Iterator[httpx.Response]:
    """A streamed response that is not 4xx/5xx; read its body inside the block."""
    try:
        with client.stream(method, url, **options) as response:
            if response.status_code >= 400:
                raise StatusError(f"http status: {response.status_code}")
            yield response
    except httpx.TimeoutException:
        raise EnrichError("timeout: global") from None


def read(response: httpx.Response, limit: int) -> bytes:
    """The body, at most `limit` bytes."""
    try:
        return http.read_capped(response, limit)
    except http.BodyTooLarge:
        raise BodyLimit(limit) from None
    except httpx.TimeoutException:
        raise EnrichError("timeout: global") from None


def read_text(response: httpx.Response, limit: int) -> str:
    """The body as strict UTF-8; no other charset is decoded."""
    try:
        return read(response, limit).decode("utf-8")
    except UnicodeDecodeError:
        raise EnrichError("io: stream did not contain valid UTF-8") from None


class BodyLimit(EnrichError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"the response body is larger than request limit: {limit}")


def header(response: httpx.Response, name: str) -> str | None:
    """The first value of a header when it is visible ASCII."""
    values = response.headers.get_list(name)
    if not values:
        return None
    value = values[0]
    return value if all(c == "\t" or " " <= c < "\x7f" for c in value) else None


def status_line(status: int) -> str:
    """The code and its canonical reason phrase."""
    return f"{status} {_REASONS.get(status, '<unknown status code>')}"
