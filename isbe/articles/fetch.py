"""One original-page read through `core.netguard`, and the reads built on it.

Ports `article_date::fetch` / `verify` and `article::fetch`. A page is read
at most once per call: public addresses only, at most three revalidated
redirects, eight seconds in total, 1 MiB of body, HTML only, decoded strictly
by its declared charset.
"""

import os
import socket
import ssl

import httpcore
import httpx

from isbe.articles import charset, dates, text
from isbe.core import db, http, netguard, validators
from isbe.core.validators import ascii_lower

TIMEOUT = 8.0
MAX_REDIRECTS = 3
MAX_BODY = 1 << 20

# Canonical reason phrases: a status prints as "404 Not Found".
_REASONS = {
    int(code): reason
    for code, reason in (
        item.split(":", 1)
        for item in (
            "100:Continue|101:Switching Protocols|102:Processing|103:Early Hints|200:OK|"
            "201:Created|202:Accepted|203:Non Authoritative Information|204:No Content|"
            "205:Reset Content|206:Partial Content|207:Multi-Status|208:Already Reported|"
            "226:IM Used|300:Multiple Choices|301:Moved Permanently|302:Found|303:See Other|"
            "304:Not Modified|305:Use Proxy|307:Temporary Redirect|308:Permanent Redirect|"
            "400:Bad Request|401:Unauthorized|402:Payment Required|403:Forbidden|"
            "404:Not Found|405:Method Not Allowed|406:Not Acceptable|"
            "407:Proxy Authentication Required|408:Request Timeout|409:Conflict|410:Gone|"
            "411:Length Required|412:Precondition Failed|413:Payload Too Large|"
            "414:URI Too Long|415:Unsupported Media Type|416:Range Not Satisfiable|"
            "417:Expectation Failed|418:I'm a teapot|421:Misdirected Request|"
            "422:Unprocessable Entity|423:Locked|424:Failed Dependency|425:Too Early|"
            "426:Upgrade Required|428:Precondition Required|429:Too Many Requests|"
            "431:Request Header Fields Too Large|451:Unavailable For Legal Reasons|"
            "500:Internal Server Error|501:Not Implemented|502:Bad Gateway|"
            "503:Service Unavailable|504:Gateway Timeout|505:HTTP Version Not Supported|"
            "506:Variant Also Negotiates|507:Insufficient Storage|508:Loop Detected|"
            "510:Not Extended|511:Network Authentication Required"
        ).split("|")
    )
}


class PageError(Exception):
    """The original page answered, but not with a readable HTML page."""


class UnsupportedBody(Exception):
    """The page has no complete supported article body (`article::UnsupportedBody`);
    raised `from` the extraction error."""

    def __init__(self) -> None:
        super().__init__("No complete supported article body found")


def status_line(status: int) -> str:
    return f"{status} {_REASONS.get(status, '<unknown status code>')}"


def _header(value: str | None) -> str:
    """`HeaderValue::to_str`, or "" when it is not visible ASCII."""
    if value is None or not all(c == "\t" or " " <= c <= "~" for c in value):
        return ""
    return value


def fetch_page(url: str, *, fetcher: netguard.PublicFetcher | None = None) -> tuple[str, str]:
    """`(html, final_url)` of the original page at `url` (`article_date::fetch`).

    `fetcher` replaces the default public-only fetcher (tests pass one with a
    permissive resolver). Raises netguard, transport, `PageError` or
    `ValueError` (charset) errors; `failure_message` renders any of them.
    """
    fetcher = fetcher or netguard.PublicFetcher(timeout=TIMEOUT, max_redirects=MAX_REDIRECTS)
    with fetcher.stream(url) as page:
        if page.status >= 400:
            # 4xx and 5xx fail here as transport errors (`http status: N`), before
            # the status check below sees them.
            raise PageError(f"http status: {page.status}")
        if page.status != 200:
            raise PageError(f"original page returned HTTP {status_line(page.status)}")
        content_type = ascii_lower(_header(page.headers.get("content-type")))
        if (
            content_type
            and "text/html" not in content_type
            and "application/xhtml+xml" not in content_type
        ):
            raise PageError("original page is not HTML")
        # The cap counts bytes; text limits later count the decoded text.
        body = page.read(MAX_BODY)
        return charset.decode_html(body, content_type), page.url


def _errno(error: BaseException) -> int | None:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError) and not isinstance(current, ssl.SSLError):
            if isinstance(current.errno, int) and current.errno > 0:
                return current.errno
        current = current.__cause__ or current.__context__
    return None


def failure_message(error: BaseException) -> str:
    """The text stored for a failed read: the top-level message, with transport
    failures worded as the earlier Rust build stored them."""
    message = str(error)
    if isinstance(error, http.BodyTooLarge):
        return f"the response body is larger than request limit: {MAX_BODY}"
    if isinstance(error, netguard.Blocked):
        return message if message == "original-page URL is not public" else f"io: {message}"
    if isinstance(error, netguard.BadHost):
        return f"bad uri: {message}"
    if isinstance(error, netguard.LookupFailed):
        if message.startswith("public DNS returned HTTP "):
            return f"http status: {message.rsplit(' ', 1)[1]}"
        if message.startswith("host not found: "):
            cause = error.__cause__
            if isinstance(cause, socket.gaierror) and cause.strerror:
                return f"io: failed to lookup address information: {cause.strerror}"
            return "host not found"
        return message if message == "host not found" else f"io: {message}"
    if isinstance(error, TimeoutError):
        if message == "original-page read timed out":
            return message
        return f"io: {message}" if message == "public DNS budget exhausted" else "timeout: global"
    if isinstance(error, httpx.TimeoutException | httpcore.TimeoutException):
        return "timeout: global"
    if isinstance(error, PageError | netguard.RedirectError | validators.Invalid | ValueError):
        return message  # URL validation, redirects and the charset errors keep their own wording
    code = _errno(error)
    if code is not None:
        return f"io: {os.strerror(code)} (os error {code})"
    return message or type(error).__name__


def verify(
    url: str, start_date: str, end_date: str, *, fetcher: netguard.PublicFetcher | None = None
) -> dates.DateCheck:
    """One original-page read, at most three checked redirects, eight seconds total;
    a failed read is reported in `date_error`, never raised."""
    try:
        html, final_url = fetch_page(url, fetcher=fetcher)
    except Exception as error:
        return dates.DateCheck(date_error=failure_message(error))
    return dates.parse_html(html, final_url, start_date, end_date)


def fetch_article(url: str, *, fetcher: netguard.PublicFetcher | None = None) -> dict[str, object]:
    """`article::fetch`: the article record `article_cache` validates and stores."""
    html, final_url = fetch_page(url, fetcher=fetcher)
    try:
        paragraphs, title = text.parse(html, final_url)
    except ValueError as error:
        raise UnsupportedBody() from error
    # Keys in sorted order.
    return {
        "fetched_at": db.now(),
        "final_url": final_url,
        "paragraphs": paragraphs,
        "title": title,
    }

