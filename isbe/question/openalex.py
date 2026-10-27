"""The OpenAlex API itself as the paper source of question runs (#121), for a
deployment without essaies: the same calls the flow makes of `sources.Essaies`,
answered from https://api.openalex.org (`OPENALEX_API_URL`).

| Call | OpenAlex request | Credits (2026-10) |
| --- | --- | --- |
| `search` | `/works?search=…` with `cursor`, `publication_year` | 10 a page |
| `lookup_many` | `/works?filter=ids.openalex:…` / `doi:…`, `BATCH` ids a request | 1 a request |
| `lookup(scope="all")` | `/works/<id>`, the references by id, `/works?filter=cites:<id>` | 0 + 1 + 1 |
| `check` | `/` | 0 |

`lookup_many` gives each work and the ids of what it cites (`referenced_works`)
with no citing works: both ends of a citation between candidates are looked up,
so the reference lists alone hold every such edge. A full lookup adds the cited
works' records and up to `limit` citing works, most cited first, which the
expansion reads. The works become essaies-shaped dicts (`work`), so
`sources.paper` makes the same paper record of either source.

`OPENALEX_API_KEY` (the key's own daily budget) and `OPENALEX_MAILTO` go with every
request when set; without a key every caller on the IP shares a small free budget
(about 100 searches a day), so a keyless client asks for the full citations of
at most `KEYLESS_FULL` papers a run (`full_cap`). Requests are spaced `INTERVAL` apart, under OpenAlex's
10 a second. A 429 is retried after its `Retry-After` when that is at most
`MAX_WAIT`; one whose budget resets later (the daily credits are spent) fails at
once with a message saying so (`problems.OPENALEX_QUOTA`); `upstreams` notes it
as failed (`HTTP 429 (quota)`), though OpenAlex is not down. A 429 holds off
every thread of the client, not only the one it answered. DOIs with "," or "|"
would split an OR filter, so `lookup_many` looks them up one by one. Timeouts, dropped
connections and 5xx are retried with backoff like essaies' (`sources.Client`).
"""

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote

from isbe.core import http, settings, upstreams
from isbe.question import sources

API = settings.OPENALEX_API
FEATURES = frozenset({"search_cursor", "search_filters", "optional_fields", "full_references", "batch_lookup"})
PAGE = 100  # a run asks for 100 works a query
# OpenAlex's largest page: a batch's answer may hold more works than ids (one DOI
# can name two works), so it is read whole.
MAX_PAGE = 200
BATCH = 50  # ids in one OR filter (OpenAlex allows 100; URLs stay short)
TIMEOUT = 30.0
INTERVAL = 0.15
# The longest Retry-After waited out; a later reset means the budget is spent.
MAX_WAIT = 30.0
MAX_BYTES = 32 * 1024 * 1024
SELECT = ",".join((
    "id", "doi", "title", "display_name", "publication_year", "type", "authorships", "primary_location",
    "locations", "cited_by_count", "abstract_inverted_index", "keywords", "referenced_works",
))
_ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/([^?#]+?)(?:v\d+)?(?:\.pdf)?$", re.I)
_FILTER_SYNTAX = re.compile(r"[,|]")
# Kept papers whose full citations a keyless client looks up per run (about 3
# credits each), so one run leaves some of the shared daily budget.
KEYLESS_FULL = 50


class OpenAlex(sources.Client):
    """The OpenAlex API at `base`, searching OpenAlex itself (its only engine)."""

    NAME = "OpenAlex"
    UPSTREAM = upstreams.OPENALEX
    LOOKUP_KINDS = ("openalex", "doi", "arxiv")
    MAX_BYTES = MAX_BYTES
    engine = "openalex"

    def __init__(
        self, client: http.Http, base: str = API, *, key: str | None = None, mailto: str | None = None,
        interval: float = INTERVAL, **options: Any,
    ) -> None:
        super().__init__(client, base, interval=interval, **options)
        self._auth = [(name, value) for name, value in (("api_key", key), ("mailto", mailto)) if value]
        self._checked = False
        # Full citation lookups a run may make (`citations.run`); None: no cap.
        self.full_cap: int | None = None if key else KEYLESS_FULL

    @classmethod
    def from_settings(cls) -> OpenAlex:
        return cls(http.Http(TIMEOUT), settings.get("OPENALEX_API_URL") or API,
                   key=settings.get("OPENALEX_API_KEY"), mailto=settings.get("OPENALEX_MAILTO"))

    def features(self) -> frozenset[str]:
        return FEATURES

    def check(self) -> None:
        """Whether OpenAlex answers, before a run spends model time on a plan: one
        free request with `sources.CHECK_TIMEOUT` and no retries."""
        if self._checked:
            return
        saved, self.retries = self.retries, 0
        try:
            with http.deadline(sources.CHECK_TIMEOUT):
                self._request("GET", "/")
        finally:
            self.retries = saved
        self._checked = True

    def search(
        self, query: str, *, limit: int = PAGE, year_from: int | None = None, year_to: int | None = None
    ) -> sources.Search:
        """Up to `limit` works for `query` (OpenAlex's boolean search syntax), page by
        page with the cursor (at most `sources.MAX_PAGES`), within the years."""
        out = sources.Search(filtered=True)
        base = [("search", query), ("select", SELECT)]
        if year_from or year_to:
            years = [f"publication_year:>{year_from - 1}" if year_from else "",
                     f"publication_year:<{year_to + 1}" if year_to else ""]
            base.append(("filter", ",".join(y for y in years if y)))
        cursor: str | None = "*"
        for _ in range(sources.MAX_PAGES):
            params = [*base, ("per_page", str(max(1, min(limit - len(out.works), PAGE)))), ("cursor", cursor or "*")]
            status, body = self._request("GET", "/works", params=params)
            results = body.get("results") if isinstance(body, dict) else None
            if not isinstance(results, list):
                raise sources.SourceError("OpenAlex answered the search without a results list", status,
                                          upstream=self.UPSTREAM)
            works = [work(r) for r in results if isinstance(r, dict)]
            meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
            out.status = status
            # What is kept of the reply: the works as the run reads them, and the paging.
            out.bodies.append({"meta": {k: meta.get(k) for k in ("count", "per_page", "next_cursor")},
                               "items": works})
            out.works += works
            cursor = meta.get("next_cursor")
            if not results or not isinstance(cursor, str) or not cursor or len(out.works) >= limit:
                break
        out.works = out.works[:limit]
        return out

    def lookup(self, ident: str, *, scope: str | None = None, limit: int | None = None) -> dict[str, Any] | None:
        """One work (`work`) and what it cites (`cites`), or None when OpenAlex does not
        know it. `scope="all"` gives the cited works' records, at most `limit` (100),
        and up to `limit` citing works, most cited first (`cited_by`)."""
        path = _path(ident)
        if path is None:
            return None
        try:
            _, raw = self._request("GET", path, params=[("select", SELECT)])
        except sources.SourceError as error:
            if error.status == 404:
                return None
            raise
        if not isinstance(raw, dict) or not raw.get("id"):
            return None
        record = _record(raw)
        if scope != "all":
            return record
        size = max(1, min(limit or PAGE, PAGE))
        refs = [r for r in raw.get("referenced_works") or [] if isinstance(r, str)][:size]
        cited: list[dict[str, Any]] = []
        for start in range(0, len(refs), BATCH):
            cited += map(work, self._works("ids.openalex", [_short(r) for r in refs[start : start + BATCH]]))
        # Referenced works OpenAlex has no record of stay as bare ids.
        known = {w["work_id"] for w in cited}
        record["cites"] = cited + [{"work_id": _short(r)} for r in refs if _short(r) not in known]
        _, body = self._request("GET", "/works", params=[
            ("filter", f"cites:{_short(raw['id'])}"), ("sort", "cited_by_count:desc"),
            ("per_page", str(size)), ("select", SELECT)])
        results = body.get("results") if isinstance(body, dict) else None
        record["cited_by"] = [work(r) for r in results or [] if isinstance(r, dict)]
        return record

    def lookup_many(
        self, idents: Iterable[str]
    ) -> tuple[dict[str, dict[str, Any]], list[str], dict[str, str]]:
        """Each id's work and what it cites, `BATCH` ids of a kind a request: the
        records found, the ids OpenAlex does not know, and the failed ones with why."""
        wanted = list(dict.fromkeys(idents))
        found: dict[str, dict[str, Any]] = {}
        failed: dict[str, str] = {}
        groups: dict[str, list[tuple[str, str, str]]] = {"ids.openalex": [], "doi": []}
        single: list[str] = []
        for ident in wanted:
            if (target := _target(ident)) is None:
                continue
            if _FILTER_SYNTAX.search(target[1]):
                single.append(ident)  # "," or "|" would break the OR filter
            else:
                groups[target[0]].append((ident, target[1], target[2]))
        for field, members in groups.items():
            for start in range(0, len(members), BATCH):
                chunk = members[start : start + BATCH]
                try:
                    works = self._works(field, [value for _, value, _ in chunk])
                except sources.Unreachable as error:
                    # OpenAlex is gone: the rest would fail the same way.
                    rest = [i for i in wanted if i not in found]
                    failed.update(dict.fromkeys(rest, str(error)))
                    return found, [], failed
                except sources.SourceError as error:
                    failed.update(dict.fromkeys((i for i, _, _ in chunk), str(error)))
                    continue
                index = {key: raw for raw in works for key in sources.identity_keys(work(raw))}
                for ident, _, key in chunk:
                    if key in index:
                        found[ident] = _record(index[key])
        for ident in single:
            try:
                record = self.lookup(ident)
            except sources.SourceError as error:
                failed[ident] = str(error)
                continue
            if record is not None:
                found[ident] = record
        missing = [i for i in wanted if i not in found and i not in failed]
        return found, missing, failed

    def _works(self, field: str, values: list[str]) -> list[dict[str, Any]]:
        """The works (as OpenAlex returns them) whose `field` is one of `values`."""
        if not values:
            return []
        status, body = self._request("GET", "/works", params=[
            ("filter", f"{field}:{'|'.join(values)}"), ("per_page", str(MAX_PAGE)), ("select", SELECT)])
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list):
            raise sources.SourceError("OpenAlex answered without a results list", status, upstream=self.UPSTREAM)
        return [r for r in results if isinstance(r, dict) and isinstance(r.get("id"), str)]

    # --- requests -------------------------------------------------------------------

    def _params(self, params: list[tuple[str, str]] | None) -> list[tuple[str, str]] | None:
        return [*(params or []), *self._auth] or None

    def _judge(self, response: http.Response, text: str) -> tuple[str, bool]:
        if response.status == 429:
            wait = _wait(response)
            if wait is None or wait <= MAX_WAIT:
                return f"OpenAlex is rate-limiting requests (HTTP 429): {text}", False
            keyed = any(n == "api_key" for n, _ in self._auth)
            advice = "" if keyed else "; set OPENALEX_API_KEY for a daily budget of your own"
            return (f"OpenAlex quota used up: the {'API key' if keyed else 'shared keyless'} daily budget "
                    f"resets in {wait / 3600:.1f} h{advice} (HTTP 429: {text})"), True
        message = f"OpenAlex answered HTTP {response.status}: {text}"
        if response.status in (401, 403):
            message += ". Check OPENALEX_API_KEY"
        return message, response.status not in sources._RETRIED

    def _pause(self, response: http.Response | None, attempt: int) -> float:
        wait = _wait(response) if response is not None and response.status == 429 else None
        pause = max(wait or 0.0, super()._pause(response, attempt))
        if wait:
            # Every thread holds off, not only the one OpenAlex answered.
            with self._lock:
                self._next = max(self._next, self._clock() + pause)
        return pause


def _wait(response: http.Response) -> float | None:
    """Seconds OpenAlex asks to wait: `Retry-After`, else its budget's reset."""
    for name in ("retry-after", "x-ratelimit-reset"):
        value = response.headers.get(name)
        try:
            return max(0.0, float(value)) if value is not None else None
        except ValueError:
            continue
    return None


# --- ids ------------------------------------------------------------------------------


def _short(value: str) -> str:
    """`W123` of `https://openalex.org/W123`."""
    return value.rstrip("/").rsplit("/", 1)[-1]


def _target(ident: str) -> tuple[str, str, str] | None:
    """The filter field and value that find `ident`, and the identity key the found
    work carries for it. An arXiv id is looked up by its DataCite DOI."""
    if key := sources.openalex_key(ident):
        return "ids.openalex", key.partition(":")[2], key
    if key := sources.doi_key(ident):
        return "doi", key.partition(":")[2], key
    if key := sources.arxiv_key(ident):
        return "doi", f"10.48550/arxiv.{key.partition(':')[2]}", key
    return None


def _path(ident: str) -> str | None:
    target = _target(ident)
    if target is None:
        return None
    field, value, _ = target
    return f"/works/{value}" if field == "ids.openalex" else f"/works/doi:{quote(value, safe='/')}"


# --- works ----------------------------------------------------------------------------


def work(raw: dict[str, Any]) -> dict[str, Any]:
    """An OpenAlex work as an essaies-shaped one (`sources.paper` reads either)."""
    primary = raw.get("primary_location") if isinstance(raw.get("primary_location"), dict) else {}
    source = primary.get("source") if isinstance(primary.get("source"), dict) else {}
    doi = raw.get("doi")
    out: dict[str, Any] = {
        "work_id": _short(raw["id"]) if isinstance(raw.get("id"), str) else None,
        "doi": doi,
        "arxiv_id": _arxiv(raw),
        "title": raw.get("title") or raw.get("display_name"),
        "year": raw.get("publication_year"),
        "type": raw.get("type"),
        "authors": [
            a["author"]["display_name"] for a in raw.get("authorships") or []
            if isinstance(a, dict) and isinstance(a.get("author"), dict) and a["author"].get("display_name")
        ],
        "venue": source.get("display_name"),
        "cited_by_count": raw.get("cited_by_count"),
        "url": primary.get("landing_page_url") or doi or raw.get("id"),
    }
    if "abstract_inverted_index" in raw:
        out["abstract"] = abstract(raw.get("abstract_inverted_index"))
    if "keywords" in raw:
        out["keywords"] = raw.get("keywords") or []
    return out


def _record(raw: dict[str, Any]) -> dict[str, Any]:
    return {"work": work(raw), "cites": [{"work_id": _short(r)} for r in raw.get("referenced_works") or []
                                         if isinstance(r, str)], "cited_by": []}


def abstract(index: Any) -> str | None:
    """The text of an OpenAlex abstract inverted index (word → positions)."""
    if not isinstance(index, dict) or not index:
        return None
    at: dict[int, str] = {}
    for word, positions in index.items():
        for n in positions if isinstance(positions, list) else []:
            if isinstance(n, int):
                at[n] = word
    return " ".join(at[n] for n in sorted(at)) or None


def _arxiv(raw: dict[str, Any]) -> str | None:
    for location in raw.get("locations") or []:
        if not isinstance(location, dict):
            continue
        for name in ("landing_page_url", "pdf_url"):
            url = location.get(name)
            if isinstance(url, str) and (m := _ARXIV_URL.search(url)) and sources.arxiv_key(m[1]):
                return m[1]
    return None
