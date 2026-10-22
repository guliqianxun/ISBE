"""Original-page publication dates; unknown and conflicting dates remain reviewable.

Evidence comes
from head metadata, the JSON-LD article that names this URL, a supported
site's article header, and dates written right after an explicit
publication or update label in visible text. Every piece is kept as
evidence; a page whose pieces disagree is a conflict, not a choice.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from isbe.articles import date_headers, dom
from isbe.articles.datetimes import Date, day_at, naive_date, parse_date, plausible
from isbe.core import jsonfmt
from isbe.core.validators import ascii_lower, byte_len, trim, whatwg

MAX_BODY = 1 << 20
MAX_EVIDENCE = 32
MAX_VALUE = 128
MAX_JSON_DEPTH = 127  # the 128th nested array or object is refused
LABEL_WINDOW = 35
# Date-shaped text tried after labels per page. A real page reaches the evidence cap
# long before (33 dates take under 900 tries); without a bound a crafted 1 MiB page of
# labels and digits costs about a million parses.
MAX_LABEL_TRIES = 8192
_HIDDEN = frozenset(
    "head script style nav footer aside template noscript textarea select".split()
)
_BREAKS = frozenset("p div li br section article".split())
_META = {
    **dict.fromkeys(
        (
            "article:published_time", "bytedance:published_time", "weibo: article:create_at",
            "pubdate", "publishdate", "publish_date", "publish_time", "pubtime",
            "publication_date", "datepublished", "dc.date.issued", "dcterms.issued",
            "og:release_date",
        ),
        "published",
    ),
    **dict.fromkeys(
        ("article:modified_time", "bytedance:updated_time", "weibo: article:update_at",
         "lastmod", "datemodified"),
        "updated",
    ),
}  # fmt: skip
_ARTICLE_TYPES = frozenset(
    "Article NewsArticle ReportageNewsArticle AnalysisNewsArticle BlogPosting "
    "ScholarlyArticle".split()
)
_LABELS = (
    ("发布日期", "published"),
    ("发布时间", "published"),
    ("更新日期", "updated"),
    ("更新时间", "updated"),
)
# Unicode White_Space plus the separators allowed between a label and its date.
_LABEL_GAP = re.compile(
    "[\t\n\x0b\x0c\r \x85\xa0  -     　:：\\[【(（]*"
)


@dataclass(frozen=True, slots=True)
class DateEvidence:
    kind: str  # "published" or "updated"
    field: str  # where it came from, e.g. "meta:pubdate", "jsonld:datePublished", "dom:..."
    value: str  # the page's text, trimmed

    def to_json(self) -> dict[str, str]:
        return {"kind": self.kind, "field": self.field, "value": self.value}


@dataclass(slots=True)
class DateCheck:
    published_at: str | None = None
    updated_at: str | None = None
    date_status: str = "unknown"  # unknown | in_range | out_of_range | conflict
    date_evidence: list[DateEvidence] = field(default_factory=list)
    date_error: str | None = None

    def to_json(self) -> dict[str, Any]:
        """The fields in declaration order."""
        return {
            "published_at": self.published_at,
            "updated_at": self.updated_at,
            "date_status": self.date_status,
            "date_evidence": [evidence.to_json() for evidence in self.date_evidence],
            "date_error": self.date_error,
        }


class _Page(dom.Sink):
    def __init__(self) -> None:
        self.evidence: list[DateEvidence] = []
        self.scripts: list[str] = []
        self.hidden: list[bool] = []
        self.heads = 0  # open <head> elements
        self.visible: list[str] = []
        # Set once a 33rd date is found; nothing is recorded or scanned after it.
        self.overflow = False

    def record(self, kind: str, source: str, value: str) -> None:
        if self.overflow:
            return
        if byte_len(value) <= MAX_VALUE and parse_date(value) is not None:
            if len(self.evidence) == MAX_EVIDENCE:
                self.overflow = True
                return
            self.evidence.append(DateEvidence(kind, source, trim(value)))

    def start(self, element: dom.Element) -> None:
        name = element.name
        aria = element.attr("aria-hidden")
        hidden = (
            (bool(self.hidden) and self.hidden[-1])
            or name in _HIDDEN
            or element.attr("hidden") is not None
            or date_headers.recommendation_region(element)
            or (aria is not None and ascii_lower(aria) == "true")
            or date_headers.hidden_style(element)
        )
        if element.html("meta") and self.heads:
            source = next(
                (v for v in map(element.attr, ("property", "name", "itemprop")) if v is not None),
                "",
            )
            source = ascii_lower(source)
            kind = _META.get(source)
            content = element.attr("content")
            if kind is not None and content is not None:
                self.record(kind, f"meta:{source}", content)
        if name in _BREAKS:
            self.visible.append("\n")
        if element.void:
            return
        self.hidden.append(hidden)
        if element.html("head"):
            self.heads += 1

    def end(self, element: dom.Element) -> None:
        self.hidden.pop()
        # JSON-LD is read whether or not its script is visible, once its end tag is seen.
        if element.closed and element.html("script") and _ld_json(element):
            self.scripts.append("".join(c for c in element.children if isinstance(c, str)))
        if element.html("head"):
            self.heads -= 1
        if element.name in _BREAKS:
            self.visible.append("\n")

    def text(self, data: str) -> None:
        if not (self.hidden and self.hidden[-1]):
            self.visible.append(data)


def _ld_json(element: dom.Element) -> bool:
    return ascii_lower(element.attr("type") or "\0") == "application/ld+json"


def _json_ok(value: Any) -> bool:
    """Whether the text `value` was parsed from is acceptable JSON: nesting
    below the recursion limit and no unpaired surrogate escapes."""
    pending: list[tuple[Any, int]] = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, str):
            if any("\ud800" <= c <= "\udfff" for c in item):
                return False
        elif isinstance(item, list | dict):
            if depth >= MAX_JSON_DEPTH:
                return False
            children = [*item.keys(), *item.values()] if isinstance(item, dict) else item
            pending.extend((child, depth + 1) for child in children)
    return True


def _is_article(kind: Any) -> bool:
    return isinstance(kind, str) and re.split("[/#]", kind)[-1] in _ARTICLE_TYPES


def _articles(value: Any, output: list[dict[str, Any]], depth: int) -> None:
    if depth > 32:
        return
    if isinstance(value, dict):
        kind = value.get("@type")
        if _is_article(kind) or (isinstance(kind, list) and any(map(_is_article, kind))):
            output.append(value)
        for key in sorted(value):  # keys in sorted order
            _articles(value[key], output, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _articles(child, output, depth + 1)


def _identities(article: dict[str, Any]) -> list[str]:
    found = []
    for key in ("url", "mainEntityOfPage"):
        value = article.get(key)
        if isinstance(value, dict):
            value = value.get("@id")
        if isinstance(value, str):
            found.append(value)
    return found


def matches_url(candidate: str, url: str) -> bool:
    try:
        base = whatwg(url).href
        joined = whatwg(candidate, url).href
    except ValueError:
        return False
    return joined.split("#", 1)[0] == base


def _labels(page: _Page, visible: str) -> None:
    tries = 0
    for label, kind in _LABELS:
        start = visible.find(label)
        while start >= 0:
            if page.overflow:
                return  # the cap ends the scan instead of trying every later label
            gap = _LABEL_GAP.match(visible, start + len(label))
            begin = gap.end() if gap else start + len(label)
            candidate = visible[begin : begin + LABEL_WINDOW]
            # Try longest prefixes, only accepting a date immediately after the explicit label.
            for end in range(len(candidate), -1, -1):
                value = trim(candidate[:end])
                if not plausible(value):
                    continue  # parse_date would refuse it without parsing
                tries += 1
                if tries > MAX_LABEL_TRIES:
                    page.overflow = True
                    return
                if parse_date(value) is not None:
                    page.record(kind, f"text:{label}", value)
                    break
            start = visible.find(label, start + len(label))


def _agree(a: Date, b: Date, offsets: set[int]) -> bool:
    """Whether two dates can name the same publication (by instant, not by the
    calendar day of whatever offset each happened to be written in)."""
    if a.instant is not None and b.instant is not None:
        return a.instant == b.instant
    if a.instant is None and b.instant is None:
        return a.day == b.day and not (len(a.value) > 10 and len(b.value) > 10 and a.value != b.value)
    timed, naive = (a, b) if a.instant is not None else (b, a)
    assert timed.instant is not None
    # A naive date is the source's local day: the instant's day in its own offset, or
    # in another offset the page states for its dates.
    return naive.day == timed.day or any(
        day_at(timed.instant, offset) == naive.day for offset in offsets
    )


def _consistent(dates: list[Date], offsets: set[int]) -> bool:
    return all(_agree(a, b, offsets) for a in dates for b in dates)


def _longest(dates: list[Date]) -> Date | None:
    """`max_by_key(len)`: the last of the longest values."""
    best: Date | None = None
    for date in dates:
        if best is None or len(date.value) >= len(best.value):
            best = date
    return best


def parse_html(html: str, url: str, start_date: str, end_date: str) -> DateCheck:
    """The publication date of `html` (served from `url`) checked against the range.

    The range is inclusive, in the publication's stated calendar timezone. The
    size cap counts characters: bytes were capped when fetched.
    """
    result = DateCheck()
    start, end = naive_date(start_date), naive_date(end_date)
    if start is None or end is None:
        result.date_error = "invalid date range"
        return result
    if start > end or len(html) > MAX_BODY:
        result.date_error = "invalid date range or oversized HTML"
        return result
    document = dom.parse(html)
    page = _Page()
    dom.walk(document.root, page)
    for source, value in date_headers.extract(document, url):
        page.record("published", source, value)
    found: list[dict[str, Any]] = []
    for script in page.scripts:
        try:
            data = jsonfmt.loads(script)
        except (ValueError, RecursionError):
            continue
        if _json_ok(data):
            _articles(data, found, 0)
    for article in found:
        identities = _identities(article)
        if not (
            any(matches_url(identity, url) for identity in identities)
            or (not identities and len(found) == 1)
        ):
            continue
        for key, kind in (("datePublished", "published"), ("dateModified", "updated")):
            stated = article.get(key)
            if isinstance(stated, str):
                page.record(kind, f"jsonld:{key}", stated)
    _labels(page, "".join(page.visible))
    result.date_evidence = page.evidence
    if page.overflow or document.overflow:
        result.date_error = "original-page date evidence exceeded parsing limits"
        return result
    parsed = {kind: _dates(page.evidence, kind) for kind in ("published", "updated")}
    updated, published = parsed["updated"], parsed["published"]
    offsets = {date.offset for date in (*published, *updated) if date.offset is not None}
    if _consistent(updated, offsets) and (latest := _longest(updated)) is not None:
        result.updated_at = latest.value
    if not _consistent(published, offsets):
        result.date_status = "conflict"
    elif (chosen := _longest(published)) is not None:
        result.published_at = chosen.value
        # Agreeing instants may fall on different days in different offsets; the
        # article is in range when its day is, in any offset it was stated in.
        within = any(start <= date.day <= end for date in published)
        result.date_status = "in_range" if within else "out_of_range"
    return result


def _dates(evidence: list[DateEvidence], kind: str) -> list[Date]:
    return [d for e in evidence if e.kind == kind and (d := parse_date(e.value)) is not None]

