"""The paper source of question runs: essaies' OpenAlex mirror (`Essaies`) or the
OpenAlex API itself (`openalex.OpenAlex`, #121), as `ISBE_PAPER_SOURCE` chooses
(`from_settings`); search, lookup, and the paper record (`common#paper`) a work
becomes. What follows is said of essaies; the OpenAlex client shares the
request machinery (`Client`) and says where it differs.

Every request goes through `core.http.Http` (whole-request timeout, proxy rules)
with a minimum interval between requests and retries of what may pass: a
timeout or a dropped connection, 429 and 5xx (`RETRIES` more, `BACKOFF` s then
twice that). A 502 in which essaies reports that OpenAlex answered 4xx is the
query's own fault and is not retried. One failed query is a `SourceError` for
that query only; the caller carries on with the others.

When essaies is clearly gone the client says so at once instead of waiting out
every request's retries (docs/standards/backend.md B-U1): a request that could
not connect at all (refused, no route, DNS, a connect timeout) is not retried and
raises `Unreachable`, and so does everything after `STOP_AFTER` failed attempts
in a row, retries included, so a hung or failing essaies costs at most three
attempts (`STOP_AFTER` × `TIMEOUT` + the backoff, about 3 minutes when every one
hangs) before the stage stops. A 4xx about one query or paper does not count, and
any answer resets the count. A client lives for one stage, so a stage stops on
the first `Unreachable`; the next stage's client tries again. Every finished
request is noted in `upstreams` (for `/api/health`, which shows only the
failure's kind: `HTTP 503`, `ConnectError: …`, never essaies' reply) and every
failed one logged once at WARNING.

What essaies can do is read once from `/api/openalex/capabilities` (API 0.5,
zore/essaies#1); an essaies without it (404) is the earlier one, and the client
falls back to what that one did. A capabilities request that fails otherwise is
not remembered: it raises, and the next use asks again (B-U3).

| Capability | Used for | Without it |
| --- | --- | --- |
| `search_cursor` | `cursor=*`, then `meta.next_cursor`, page by page | one page of at most 100 |
| `search_filters` | `year_from` / `year_to` sent with the search | the caller filters by year |
| `optional_fields` | `include=abstract,keywords` | no abstract or keywords |
| `batch_lookup` | `POST /api/openalex/batch`, 100 ids a request | one lookup per id |
| `full_references` | `scope=all` lookups: every reference and citing works (`citations`) | archive citations only |

With `engine="arxiv"` the searches go to essaies' own arXiv index instead
(`/api/search`, SQLite FTS5 over the archive): one request of up to 2,000
papers, always within the years, with abstracts and no keywords. It is local,
so it answers when OpenAlex is slow or out of quota, but it holds only the
arXiv categories essaies collects. Its queries are written for it
(`compose.ARXIV`). Lookups stay on the OpenAlex mirror.
"""

import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from isbe.core import http, settings, upstreams
from isbe.core.errors import HttpError

log = logging.getLogger(__name__)

SEARCH = "/api/openalex/search"
LOOKUP = "/api/openalex/lookup"
BATCH = "/api/openalex/batch"
CAPABILITIES = "/api/openalex/capabilities"
ARXIV_SEARCH = "/api/search"
ENGINES = ("openalex", "arxiv")
# What one search page and one batch of essaies hold at most.
PAGE = 100
BATCH_SIZE = 100
# What one search of the arXiv index returns at most.
ARXIV_LIMIT = 2000
# The fields a search asks for when essaies offers them; screening reads both.
INCLUDE = "abstract,keywords"
# A search goes through essaies to OpenAlex: 2–3 s normally, 20 s when OpenAlex is slow.
TIMEOUT = 60.0
INTERVAL = 0.2
RETRIES = 2
BACKOFF = 2.0
MAX_PAGES = 5
# Failed attempts in a row (retries included) after which essaies counts as down.
STOP_AFTER = 3
# The reachability check before a run plans: one quick request, no retries.
CHECK_TIMEOUT = 10.0
_RETRIED = frozenset({429, 500, 502, 503, 504})
# essaies' 502 when OpenAlex rejected the query itself (a "?" in it, say).
_UPSTREAM_4XX = re.compile(r"HTTP Error 4\d\d")

ORDER = ("openalex", "doi", "arxiv")
_OPENALEX = re.compile(r"(?:https?://openalex\.org/)?W?([0-9]{1,12})", re.I)
_DOI = re.compile(r"10\.[0-9]{4,9}/\S+")
_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", re.I)
_ARXIV_NEW = re.compile(r"([0-9]{4}\.[0-9]{4,5})(?:v[0-9]+)?")
_ARXIV_OLD = re.compile(r"([a-z-]+(?:\.[A-Z]{2})?/[0-9]{7})(?:v[0-9]+)?")
_ARXIV_DOI = re.compile(r"10\.48550/arxiv\.(.+)", re.I)


class SourceError(Exception):
    """A request to the paper source failed after its retries; `status` is the last
    HTTP status, `upstream` the source (`upstreams.ESSAIES` or `upstreams.OPENALEX`)."""

    def __init__(
        self, message: str, status: int | None = None, *, kind: str | None = None, os_error: str | None = None,
        upstream: str = upstreams.ESSAIES,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind  # the httpx error class of a request that got no reply
        self.os_error = os_error  # the OS's own words for it, as "[Errno 111] Connection refused"
        self.upstream = upstream
        # The source answered and the request itself was the trouble (a query it
        # rejects, a used-up quota): not retried, and the source is not down.
        self.final = False

    def public(self) -> str:
        """What failed without essaies' own words, for `/api/health` (B-U6)."""
        if self.status is not None:
            return f"HTTP {self.status}"
        if self.kind and self.os_error:
            return f"{self.kind}: {self.os_error}"
        return self.kind or "no reply"


class Unreachable(SourceError):
    """The source is gone: a request could not connect (`connect`), or `STOP_AFTER`
    failed in a row. Every further request of this client would fail too."""

    def __init__(
        self, message: str, status: int | None = None, *, connect: bool = False,
        kind: str | None = None, os_error: str | None = None, upstream: str = upstreams.ESSAIES,
    ) -> None:
        super().__init__(message, status, kind=kind, os_error=os_error, upstream=upstream)
        self.connect = connect


@dataclass
class Search:
    """The works one query found, in rank order, essaies' replies as returned, and
    whether essaies applied the years itself."""

    works: list[dict[str, Any]] = field(default_factory=list)
    bodies: list[Any] = field(default_factory=list)
    status: int | None = None
    filtered: bool = False


class Source(Protocol):
    """What the question flow asks of a paper source: `Essaies`, or `openalex.OpenAlex`."""

    NAME: str
    LOOKUP_KINDS: tuple[str, ...]
    engine: str
    last_error: SourceError | None

    def features(self) -> frozenset[str]: ...
    def check(self) -> None: ...
    def search(self, query: str, *, limit: int = ..., year_from: int | None = None,
               year_to: int | None = None) -> Search: ...
    def lookup(self, ident: str, *, scope: str | None = None, limit: int | None = None) -> dict[str, Any] | None: ...
    def lookup_many(
        self, idents: Iterable[str]) -> tuple[dict[str, dict[str, Any]], list[str], dict[str, str]]: ...


def from_settings(engine: str = "openalex") -> Source:
    """The paper source the settings choose (`settings.paper_source`), searching with
    `engine`. The arXiv engine is essaies' own index, so it needs essaies:
    `HttpError(400)` with the OpenAlex API, as when essaies has no URL."""
    if settings.paper_source() == "essaies":
        return Essaies.from_settings(engine)
    if engine != "openalex":
        raise HttpError(400, "The arXiv engine searches essaies' arXiv index; the paper source is the OpenAlex "
                             "API (ISBE_PAPER_SOURCE), so search OpenAlex")
    from isbe.question.openalex import OpenAlex  # it builds on this module

    return OpenAlex.from_settings()


def engines() -> tuple[str, ...]:
    """The engines a question run may search with the settings' paper source."""
    return ENGINES if settings.paper_source() == "essaies" else ("openalex",)


class Client:
    """The request machinery a paper source shares (see the module docstring):
    spacing, retries, the run of failures after which the source counts as down,
    and what `upstreams` hears. `NAME` is how messages name the source."""

    NAME = "essaies"
    UPSTREAM = upstreams.ESSAIES
    # The identity kinds `lookup` takes, preferred first (`citations.lookup_id`).
    LOOKUP_KINDS: tuple[str, ...] = ("arxiv", "doi")
    MAX_BYTES = http.MAX_BODY

    def __init__(
        self,
        client: http.Http,
        base: str,
        *,
        interval: float = INTERVAL,
        retries: int = RETRIES,
        backoff: float = BACKOFF,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.base = base.rstrip("/")
        self.interval = interval
        self.retries = retries
        self.backoff = backoff
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._next = 0.0
        self._failures = 0  # failed attempts in a row
        self._down: Unreachable | None = None  # why the source counts as down, once it does
        self.last_error: SourceError | None = None  # of the latest request that failed

    def _params(self, params: list[tuple[str, str]] | None) -> list[tuple[str, str]] | None:
        """What every request adds to its query (an API key, say)."""
        return params

    def _judge(self, response: http.Response, text: str) -> tuple[str, bool]:
        """A non-2xx answer's message, and whether it is final: the request's own
        fault (or a quota that will not pass soon), so it is not retried."""
        final = response.status not in _RETRIED or bool(_UPSTREAM_4XX.search(text))
        return f"{self.NAME} answered HTTP {response.status}: {text}", final

    def _pause(self, response: http.Response | None, attempt: int) -> float:
        """Seconds to wait before retry `attempt` (1, 2, …); `response` is the answer
        retried, None after a request that got none."""
        return self.backoff * 2 ** (attempt - 1)

    def _request(
        self, method: str, path: str, *, params: list[tuple[str, str]] | None = None, json: Any = None
    ) -> tuple[int, Any]:
        """The status and JSON body of a 2xx reply, after the retries; else `SourceError`
        (`Unreachable` when the source is gone, see the module docstring)."""
        if self._down is not None:
            raise self._gone()
        try:
            out = self._attempts(method, path, params, json)
        except Unreachable as error:
            self._failed(path, error)
            raise
        except SourceError as error:
            if error.final:
                # The source answered: it is up, and the request was the problem;
                # a spent quota still fails every request until it resets.
                self._answered()
                if error.status == 429:
                    self.last_error = error
                    upstreams.record(self.UPSTREAM, False, "HTTP 429 (quota)")
                raise
            self._failed(path, error)
            if self._down is not None:
                raise self._gone() from error
            raise
        self._answered()
        return out

    def _gone(self) -> Unreachable:
        down = self._down
        assert down is not None
        return Unreachable(str(down), down.status, connect=down.connect, kind=down.kind, os_error=down.os_error,
                           upstream=self.UPSTREAM)

    def _attempts(
        self, method: str, path: str, params: list[tuple[str, str]] | None, json: Any
    ) -> tuple[int, Any]:
        attempt = 0
        while True:
            self._wait()
            error: SourceError
            response: http.Response | None = None
            try:
                response = self.client.request(method, self.base + path, params=self._params(params), json=json,
                                               max_bytes=self.MAX_BYTES)
            except (httpx.ConnectError, httpx.ConnectTimeout) as failure:
                # No connection at all: nothing listens there, or no route to it.
                with self._lock:
                    self._failures += 1
                raise Unreachable(f"{self.NAME} could not be reached: {failure}", connect=True,
                                  upstream=self.UPSTREAM, **_kind(failure)) from None
            except (httpx.HTTPError, http.BodyTooLarge) as failure:
                error = SourceError(f"{self.NAME} could not be reached: {failure}", upstream=self.UPSTREAM,
                                    **_kind(failure))
            else:
                body = _json(response)
                if 200 <= response.status < 300 and body is not None:
                    return response.status, body
                detail = body.get("detail") or body.get("message") if isinstance(body, dict) else None
                text = detail if isinstance(detail, str) and detail else _snippet(response)
                message, final = self._judge(response, text)
                error = SourceError(message, response.status, upstream=self.UPSTREAM)
                if final:
                    error.final = True
                    raise error
            with self._lock:
                self._failures += 1
                failures = self._failures
            if attempt >= self.retries or failures >= STOP_AFTER:
                raise error
            attempt += 1
            self._sleep(self._pause(response, attempt))

    def _answered(self) -> None:
        with self._lock:
            self._failures = 0
        upstreams.record(self.UPSTREAM, True)

    def _failed(self, path: str, error: SourceError) -> None:
        """Note a request that failed after its attempts (`_attempts` counted them)."""
        self.last_error = error
        with self._lock:
            if self._down is None and isinstance(error, Unreachable):
                self._down = error
            elif self._down is None and self._failures >= STOP_AFTER:
                self._down = Unreachable(
                    f"{self.NAME} failed {self._failures} attempts in a row; the last: {error}", error.status,
                    kind=error.kind, os_error=error.os_error, upstream=self.UPSTREAM)
        upstreams.record(self.UPSTREAM, False, error.public())
        log.warning("%s %s failed (%s): %s", self.NAME, path, error.kind or type(error).__name__, error)

    def _wait(self) -> None:
        with self._lock:
            now = self._clock()
            start = max(now, self._next)
            self._next = start + self.interval
        if start > now:
            self._sleep(start - now)


class Essaies(Client):
    """An essaies server at `base`, searching with `engine` (one of `ENGINES`).
    Thread-safe; requests are spaced `interval` apart. `features` replaces the
    capabilities essaies would report (tests of an old one)."""

    def __init__(
        self,
        client: http.Http,
        base: str,
        *,
        engine: str = "openalex",
        features: Iterable[str] | None = None,
        **options: Any,
    ) -> None:
        if engine not in ENGINES:
            raise ValueError(f"unknown search engine {engine!r}; expected one of {ENGINES}")
        super().__init__(client, base, **options)
        self.engine = engine
        self._features = None if features is None else frozenset(features)

    @classmethod
    def from_settings(cls, engine: str = "openalex") -> Essaies:
        """The essaies of `ESSAIES_URL`, or `HttpError(400)` when none is set."""
        base = settings.get("ESSAIES_URL")
        if not base:
            raise HttpError(400, "Set the essaies URL (ESSAIES_URL) to search for papers")
        return cls(http.Http(TIMEOUT), base, engine=engine)

    def features(self) -> frozenset[str]:
        """essaies' capabilities, asked once; none for an essaies that predates them
        (404: the client then does what it did before). Any other failure is a
        `SourceError` and is not remembered, so the next call asks again."""
        if self._features is None:
            try:
                _, body = self._request("GET", CAPABILITIES)
            except SourceError as error:
                if error.status != 404:
                    raise
                body = {}
            listed = body.get("capabilities") if isinstance(body, dict) else None
            self._features = frozenset(c for c in listed if isinstance(c, str)) if isinstance(listed, list) else frozenset()
        return self._features

    def check(self) -> None:
        """Whether essaies answers at all, before a run spends model time on a plan
        (B-U2): one capabilities request with `CHECK_TIMEOUT` and no retries.
        `Unreachable` when it could not connect, `SourceError` when it answered
        5xx or not at all in time; an essaies without capabilities (404) is fine."""
        if self._features is not None:
            return
        saved, self.retries = self.retries, 0
        try:
            with http.deadline(CHECK_TIMEOUT):
                self.features()
        finally:
            self.retries = saved

    def search(
        self, query: str, *, limit: int = PAGE, year_from: int | None = None, year_to: int | None = None
    ) -> Search:
        """Up to `limit` works for `query`, page by page where essaies pages (at most
        `MAX_PAGES`), within the years when essaies filters them."""
        if self.engine == "arxiv":
            return self._search_arxiv(query, limit, year_from, year_to)
        features = self.features()
        out = Search(filtered="search_filters" in features)
        base = [("q", query)]
        if "optional_fields" in features:
            base.append(("include", INCLUDE))
        if out.filtered:
            base += [(name, str(year)) for name, year in (("year_from", year_from), ("year_to", year_to)) if year]
        paged = "search_cursor" in features
        cursor: str | None = "*" if paged else None
        for _ in range(MAX_PAGES if paged else 1):
            params = [*base, ("limit", str(min(limit - len(out.works), PAGE)))]
            if cursor is not None:
                params.append(("cursor", cursor))
            status, body = self._request("GET", SEARCH, params=params)
            out.status = status
            out.bodies.append(body)
            items = body.get("items") if isinstance(body, dict) else None
            if not isinstance(items, list):
                raise SourceError("essaies answered the search without an items list", status)
            out.works += [item for item in items if isinstance(item, dict)]
            meta = body.get("meta")
            cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
            if not items or not isinstance(cursor, str) or not cursor or len(out.works) >= limit:
                break
        out.works = out.works[:limit]
        return out

    def _search_arxiv(self, query: str, limit: int, year_from: int | None, year_to: int | None) -> Search:
        params = [("q", query), ("limit", str(max(1, min(limit, ARXIV_LIMIT))))]
        params += [(name, str(year)) for name, year in (("year_from", year_from), ("year_to", year_to)) if year]
        status, body = self._request("GET", ARXIV_SEARCH, params=params)
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise SourceError("essaies answered the search without an items list", status)
        works = [item for item in items if isinstance(item, dict)][:limit]
        return Search(works=works, bodies=[body], status=status, filtered=True)

    def lookup(self, ident: str, *, scope: str | None = None, limit: int | None = None) -> dict[str, Any] | None:
        """essaies' record of one DOI or arXiv id (`work`, `cites`, `cited_by`), or None
        when it does not know the paper. `scope="all"` asks for the full references and
        citing works through OpenAlex (`full_references`), `limit` of each at most."""
        params = [("q", ident)]
        if scope is not None:
            params.append(("scope", scope))
        if limit is not None:
            params.append(("limit", str(limit)))
        try:
            _, body = self._request("GET", LOOKUP, params=params)
        except SourceError as error:
            if error.status == 404:
                return None
            raise
        return body if isinstance(body, dict) and isinstance(body.get("work"), dict) else None

    def lookup_many(
        self, idents: Iterable[str]
    ) -> tuple[dict[str, dict[str, Any]], list[str], dict[str, str]]:
        """Each id looked up, in batches where essaies has them: the records found, the
        ids essaies does not know, and the ids whose lookup failed with why."""
        wanted = list(dict.fromkeys(idents))
        found: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        failed: dict[str, str] = {}
        try:
            batched = "batch_lookup" in self.features()
        except SourceError as error:
            return found, missing, dict.fromkeys(wanted, str(error))
        if batched:
            for start in range(0, len(wanted), BATCH_SIZE):
                self._batch(wanted[start : start + BATCH_SIZE], found, missing, failed)
            return found, missing, failed
        for n, ident in enumerate(wanted):
            try:
                record = self.lookup(ident)
            except Unreachable as error:
                # essaies is gone: the rest would fail the same way.
                failed.update(dict.fromkeys(wanted[n:], str(error)))
                break
            except SourceError as error:
                failed[ident] = str(error)
                continue
            if record is None:
                missing.append(ident)
            else:
                found[ident] = record
        return found, missing, failed

    def _batch(
        self, ids: list[str], found: dict[str, dict[str, Any]], missing: list[str], failed: dict[str, str]
    ) -> None:
        try:
            _, body = self._request("POST", BATCH, json={"ids": ids})
        except SourceError as error:
            failed.update(dict.fromkeys(ids, str(error)))
            return
        body = body if isinstance(body, dict) else {}
        for item in body.get("items") or []:
            if isinstance(item, dict) and item.get("query") in ids and isinstance(item.get("work"), dict):
                found[item["query"]] = {k: v for k, v in item.items() if k != "query"}
        missing += [i for i in body.get("missing") or [] if i in ids]
        for entry in body.get("errors") or []:
            ident = entry.get("query") or entry.get("id") if isinstance(entry, dict) else entry
            if ident in ids:
                why = entry.get("error") or entry.get("detail") if isinstance(entry, dict) else None
                failed[ident] = str(why or "essaies could not look it up")
        for ident in ids:
            if ident not in found and ident not in failed and ident not in missing:
                missing.append(ident)


def _kind(failure: Exception) -> dict[str, str | None]:
    """An httpx failure's class, and its message when that is the OS's own words
    (no host, no reply: safe for `/api/health`)."""
    text = str(failure)
    return {
        "kind": type(failure).__name__,
        "os_error": text if re.fullmatch(r"\[Errno -?\d+\] [\w ,'-]+", text) else None,
    }


def _json(response: http.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _snippet(response: http.Response) -> str:
    try:
        text = " ".join(response.text.split())
    except UnicodeDecodeError:
        return "(binary body)"
    return text[:200] or "(empty body)"


# --- identity and the paper record -----------------------------------------------------


def openalex_key(value: Any) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    m = _OPENALEX.fullmatch(value.strip())
    return f"openalex:W{int(m[1])}" if m and int(m[1]) > 0 else None


def doi_key(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    doi = _DOI_PREFIX.sub("", value.strip()).lower()
    return f"doi:{doi}" if _DOI.fullmatch(doi) else None


def arxiv_key(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    text = text[6:] if text.lower().startswith("arxiv:") else text
    m = _ARXIV_NEW.fullmatch(text) or _ARXIV_OLD.fullmatch(text)
    return f"arxiv:{m[1]}" if m else None


def identity_keys(work: dict[str, Any]) -> list[str]:
    """A work's identity keys, preferred first: its OpenAlex work, its DOI, its arXiv
    id (also read from an arXiv DOI, 10.48550/arxiv.<id>). essaies' `id` is either
    a DOI or an arXiv id, and the only id of a `cites` entry."""
    keys = [
        openalex_key(work.get("work_id")) or openalex_key(work.get("openalex_id")),
        doi_key(work.get("doi")),
        arxiv_key(work.get("arxiv_id")),
    ]
    ident = work.get("id")
    keys += [doi_key(ident), arxiv_key(ident)]
    for key in list(keys):
        if key and key.startswith("doi:") and (m := _ARXIV_DOI.fullmatch(key[4:])):
            keys.append(arxiv_key(m[1]))
    out = list(dict.fromkeys(k for k in keys if k))
    return sorted(out, key=key_rank)


def key_rank(key: str) -> int:
    return ORDER.index(key.partition(":")[0])


def paper(work: dict[str, Any]) -> dict[str, Any] | None:
    """The paper record of an essaies work, or None without a title or an identity."""
    keys = identity_keys(work)
    title = _squash(work.get("title"))
    if not keys or not title:
        return None
    year = work.get("year")
    cited = work.get("cited_by_count")
    record: dict[str, Any] = {
        "key": keys[0],
        "keys": keys,
        "title": title,
        "year": year if _is_int(year) and 1000 <= year <= 2999 else None,
        "authors": authors(work.get("authors")),
        "venue": _squash(work.get("venue")) or None,
        "type": _squash(work.get("type")) or None,
    }
    if _is_int(cited) and cited >= 0:
        record["cited_by_count"] = cited
    record["abstract"] = _squash(work.get("abstract")) or None
    record["keywords"] = _keywords(work.get("keywords"))
    url = work.get("url")
    record["url"] = url.strip() if isinstance(url, str) and url.strip() else _url(keys)
    if isinstance(work.get("in_archive"), bool):
        record["in_archive"] = work["in_archive"]
    elif "has_pdf" in work:
        # `cites` and `cited_by` entries are archive papers.
        record["in_archive"] = True
    return record


def authors(value: Any) -> list[str]:
    """essaies' author string as names. It comes as `First Last, First Last`, as
    arXiv's `Last, First, Last, First` or as `Last First; Last First`."""
    if isinstance(value, list):
        return [name for v in value if (name := _squash(v))]
    if not isinstance(value, str):
        return []
    if ";" in value:
        return [name for part in value.split(";") if (name := _squash(part))]
    parts = [_squash(p) for p in value.split(",")]
    parts = [p for p in parts if p]
    # Pairs of a one-word surname and the given names: the arXiv form. A full name
    # always has a space, so a list of full names never looks like this.
    if len(parts) % 2 == 0 and all(" " not in p for p in parts[0::2]):
        return [f"{first} {last}" for last, first in zip(parts[0::2], parts[1::2], strict=True)]
    return parts


def _keywords(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        name = item.get("display_name") if isinstance(item, dict) else item
        text = _squash(name)
        if text and text not in out:
            out.append(text)
    return out


def _url(keys: list[str]) -> str | None:
    for key in keys:
        kind, _, value = key.partition(":")
        if kind == "doi":
            return f"https://doi.org/{value}"
        if kind == "arxiv":
            return f"https://arxiv.org/abs/{value}"
    return None


def _squash(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
