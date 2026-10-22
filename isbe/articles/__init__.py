"""Original article pages: fetch, text extraction, publication dates and the cache.

Every original-page read goes through `core.netguard` (`fetch.fetch_page`).

- `fetch_page(url) -> (html, final_url)`: fetch a page, public addresses only.
- `verify(url, start_date, end_date) -> DateCheck`: fetch a page and check its
  dates; `DateCheck.to_json()` is its JSON form.
- `parse_html(html, url, start_date, end_date) -> DateCheck`: dates of a page in hand.
- `parse_article(html, url) -> (paragraphs, title)`: the article body.
- `fetch_article(url) -> dict`: fetch and parse; raises `UnsupportedBody`.
- `load / get_or_fetch / get_or_fetch_with`: the cache; upstream failures
  raise `FetchFailed`, and `caused_by(error, UnsupportedBody)` finds that cause
  for the web layer.
"""

from isbe.articles.cache import (
    FetchFailed,
    caused_by,
    get_or_fetch,
    get_or_fetch_with,
    load,
)
from isbe.articles.charset import decode_html
from isbe.articles.dates import DateCheck, DateEvidence, parse_html
from isbe.articles.fetch import (
    PageError,
    UnsupportedBody,
    failure_message,
    fetch_article,
    fetch_page,
    verify,
)
from isbe.articles.text import parse as parse_article

__all__ = [
    "DateCheck",
    "DateEvidence",
    "FetchFailed",
    "PageError",
    "UnsupportedBody",
    "caused_by",
    "decode_html",
    "failure_message",
    "fetch_article",
    "fetch_page",
    "get_or_fetch",
    "get_or_fetch_with",
    "load",
    "parse_article",
    "parse_html",
    "verify",
]
