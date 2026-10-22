"""Narrow article-header fallbacks, checked against frozen publisher HTML fixtures.

Each supported site has one
article-header shape: a root container, a title that must appear before the
date inside it, and a date field whose text must be the publication date (not
an update, an event or a byline label). Anything outside that shape, hidden,
repeated or ambiguous yields no date rather than a guessed one.
"""

import dataclasses
import re

from isbe.articles import dom
from isbe.articles.datetimes import hour_minute, parse_date, unix_rfc3339
from isbe.articles.text import split_ascii
from isbe.core.validators import ascii_lower, byte_len, trim, whatwg

MAX_DATES = 32
MAX_FIELD_TEXT = 512
_HIDDEN = frozenset("head script style nav footer aside template noscript".split())
_RECOMMENDATIONS = frozenset(
    {"related", "related-news", "recommend", "recommendations", "recommend-list"}
)
_BOND_HEADER = ("@container", "gap-3", "2xl:gap-4", "flex", "flex-col")
_BOND_ROW = ("flex", "items-center", "gap-4", "h-7", "mb-2", "@[500px]:mb-0")
_JINGJI_LABELS = ("时间", "日期", "活动", "会议", "截止", "报名", "event", "date", "time", "deadline")
_ROOTS = {
    "chinanews": "cms-news-article-title",
    "21jingji": "titleHead",
    "jiemian": "article-view",
    "yicai": "m-on",
    "chinagongyi": "d_picnews_bt",
    "xinmin": "dzb-enter-info-box",
}
_DATE_CONTEXT = {
    "chinanews": "cms-news-article-title-source",
    "21jingji": "author-infos",
    "eastmoney": "infos",
    "jiemian": "info-s",
    "jiemian-mobile": "info",
    "chinagongyi": "d_pnfubt",
    "xinmin": "dzb-enter-banmian-box",
}
_BOND_PATH = re.compile(r"/[0-9]{8}/c[0-9]+\.shtml")
# Runs of Unicode White_Space.
_WHITESPACE = re.compile(
    "[\t\n\x0b\x0c\r \x85\xa0  -     　]+"
)


def recommendation_region(element: dom.Element) -> bool:
    return any(part in _RECOMMENDATIONS for part in split_ascii(element.attr("class") or ""))


def hidden_style(element: dom.Element) -> bool:
    style = ascii_lower((element.attr("style") or "").replace(" ", ""))
    return "display:none" in style or "visibility:hidden" in style


def unix_date(raw: str) -> str | None:
    """The publisher's article JS multiplies this exact attribute by 1e3 before
    `new Date`; its formatter uses browser-local getters, so keep the UTC instant."""
    if len(raw) != 10 or not all("0" <= c <= "9" for c in raw):
        return None
    seconds = int(raw)
    if not 946_684_800 <= seconds < 4_102_444_800:
        return None
    return unix_rfc3339(seconds)


def site_of(url: str) -> str | None:
    """The supported site whose article URL `url` is, if any."""
    try:
        parsed = whatwg(url)
    except ValueError:
        return None
    host, path = parsed.hostname, parsed.pathname
    html = path.endswith(".html")
    if host == "www.sh.chinanews.com.cn" and path.endswith(".shtml"):
        return "chinanews"
    sites = {
        ("m.21jingji.com", "/article/"): "21jingji",
        ("finance.eastmoney.com", "/a/"): "eastmoney",
        ("www.jiemian.com", "/article/"): "jiemian",
        ("m.jiemian.com", "/article/"): "jiemian-mobile",
        ("www.yicai.com", "/brief/"): "yicai",
        ("www.chinagongyi.com.cn", "/esg/"): "chinagongyi",
        ("paper.xinmin.cn", "/html/xmwb/"): "xinmin",
    }
    for (site_host, prefix), site in sites.items():
        if host == site_host and path.startswith(prefix) and html:
            return site
    if host == "bond.10jqka.com.cn" and _BOND_PATH.fullmatch("/" + path.lstrip("/")):
        return "10jqka"
    return None


@dataclasses.dataclass(eq=False, slots=True)
class _Frame:
    classes: list[str]
    id: str
    hidden: bool
    title: bool
    is_title: bool
    field: str | None
    date_context: bool
    unix: str | None
    text: str = ""
    has_date: bool = False
    pending: list[tuple[str, str]] = dataclasses.field(default_factory=list)

    def has(self, *names: str) -> bool:
        return all(name in self.classes for name in names)


class _Headers(dom.Sink):
    def __init__(self, site: str) -> None:
        self.site = site
        self.stack: list[_Frame] = []
        self.roots = 0
        self.dates: list[tuple[str, str]] = []
        self.overflow = False
        self.updated_context = False

    def parent(self, back: int) -> _Frame | None:
        return self.stack[-back] if len(self.stack) >= back else None

    def has(self, back: int, *names: str, title: bool = False, id: str | None = None) -> bool:
        frame = self.parent(back)
        return (
            frame is not None
            and frame.has(*names)
            and (not title or frame.title)
            and (id is None or frame.id == id)
        )

    def push(self, field: str, value: str) -> bool:
        if len(self.dates) >= MAX_DATES:
            self.overflow = True
            return False
        self.dates.append((field, value))
        return True

    def start(self, element: dom.Element) -> None:
        name, site = element.name, self.site
        classes = split_ascii(element.attr("class") or "")
        element_id = element.attr("id") or ""
        hidden = (
            (bool(self.stack) and self.stack[-1].hidden)
            or name in _HIDDEN
            or element.attr("hidden") is not None
            or element.attr("aria-hidden") == "true"
            or recommendation_region(element)
            or hidden_style(element)
        )
        if site == "eastmoney":
            root = element_id == "topbox"
        elif site == "10jqka":
            root = all(c in classes for c in _BOND_HEADER)
        else:
            root = _ROOTS.get(site, "\0") in classes
        if root and not hidden and site != "jiemian":
            self.roots += 1
        date_field = None if hidden else self.field(name, classes)
        unix = None
        if (
            not hidden
            and site == "jiemian-mobile"
            and name == "div"
            and "info" in classes
            and self.has(1, "article-detail")
            and self.has(2, "article-main", title=True)
        ):
            raw = element.attr("data-article-publish-time")
            unix = raw if raw is not None and unix_date(raw) is not None else None
        if site == "10jqka":
            date_context = self.has(1, *_BOND_HEADER) and "justify-between" in classes
        elif site == "yicai":
            date_context = name == "h1" and self.has(1, "m-on")
        else:
            date_context = _DATE_CONTEXT.get(site, "\0") in classes
        if element.void:
            return
        self.stack.append(
            _Frame(
                classes,
                element_id,
                hidden,
                title=site == "jiemian-mobile"
                and "article-detail" in classes
                and self.has(1, "article-main", title=True),
                is_title=self.is_title(name, classes),
                field=date_field,
                date_context=date_context and not hidden,
                unix=unix,
            )
        )

    def field(self, name: str, classes: list[str]) -> str | None:
        site, has = self.site, self.has
        if site == "chinanews":
            if "cms-news-article-title-source" in classes and has(1, "col-md-8", title=True):
                return "dom:chinanews:article-title-source"
        elif site == "21jingji":
            if (
                name == "span"
                and has(1, "author-infos")
                and any(p.has("titleHead") and p.title for p in self.stack)
            ):
                return "dom:21jingji:titleHead.author-infos"
        elif site == "eastmoney":
            if (
                name == "div"
                and "item" in classes
                and has(1, "infos")
                and has(2, "tipbox")
                and has(3, id="topbox", title=True)
            ):
                return "dom:eastmoney:topbox.infos.item"
        elif site == "jiemian":
            if (
                name == "span"
                and has(1, "info-s")
                and has(2, "article-info")
                and has(3, "article-view", title=True)
            ):
                return "dom:jiemian:article-info.info-s"
        elif site == "yicai":
            if has(1, "m-on") and has(2, "m-text"):
                return {"h1": "yicai:day", "p": "yicai:time-title"}.get(name)
        elif site == "chinagongyi":
            if (
                name == "span"
                and has(1, "d_pnfubt")
                and has(2, "d_picnews_bt", title=True)
                and has(3, "d_picnews")
            ):
                return "dom:chinagongyi:d_picnews_bt.d_pnfubt"
        elif site == "xinmin":
            if (
                name == "span"
                and "dzb-banmian-date" in classes
                and has(1, "dzb-enter-banmian-box")
                and has(2, "dzb-enter-info-box")
            ):
                return "dom:xinmin:edition_date"
        elif site == "10jqka":
            if (
                name == "span"
                and "text-black/40" in classes
                and "text-[12px]" in classes
                and has(1, *_BOND_ROW)
                and has(2, "flex", "justify-between", "items-center")
                and has(3, *_BOND_HEADER, title=True)
            ):
                return "dom:10jqka:bond.article-header.time"
        return None

    def is_title(self, name: str, classes: list[str]) -> bool:
        site, has = self.site, self.has
        if site == "yicai":
            return False  # Its h1 is the day; the brief's title is bracketed in the paragraph.
        if site == "chinagongyi":
            return "d_pn_con" in classes and has(1, "d_picnews_bt")
        if site == "xinmin":
            return (
                name == "h2"
                and "dzb-title-box" in classes
                and has(1, "dzb-enter-desc-box")
                and has(2, "dzb-enter-info-box")
            )
        if site == "10jqka":
            return name == "h1" and has(1, *_BOND_HEADER)
        if site == "jiemian-mobile":
            return (
                name == "h1"
                and "article-title" in classes
                and has(1, "article-img")
                and has(2, "article-main")
            )
        return (
            name == "h1"
            or "cms-news-article-title" in classes
            or ("title" in classes and has(1, id="topbox"))
        )

    def end(self, element: dom.Element) -> None:
        frame = self.stack.pop()
        site = self.site
        if frame.title and not frame.hidden:
            dates: list[tuple[str, str]] = []
            if site == "yicai" and frame.has("m-on"):
                days = [value for name, value in frame.pending if name == "yicai:day"]
                times = [value for name, value in frame.pending if name == "yicai:time-title"]
                if len(days) == 1 and len(times) == 1:
                    value = f"{days[0]} {times[0]}"
                    if parse_date(value) is not None:
                        dates.append(("dom:yicai:brief.day-and-time", value))
            elif site == "xinmin" and frame.has("dzb-enter-info-box"):
                dates = list(frame.pending)
            for name, found in dates:
                self.push(name, found)
        if frame.title and frame.has_date and (
            (site == "jiemian" and frame.has("article-view"))
            or (site == "jiemian-mobile" and frame.has("article-detail"))
        ):
            self.roots += 1
        if frame.date_context and _updated(frame.text):
            self.updated_context = True
        if frame.unix is not None:
            stamp = unix_date(frame.unix)
            source = f"dom:jiemian:article-detail.info[data-article-publish-time={frame.unix};unix-seconds]"
            if stamp is not None and self.push(source, stamp):
                for parent in self.stack:
                    if parent.has("article-detail"):
                        parent.has_date = True
        if frame.field is not None:
            self.field_end(frame.field, trim(frame.text))

    def field_end(self, field: str, value: str) -> None:
        if _updated(value):
            return
        site = self.site
        if site in ("yicai", "xinmin"):
            found: str | None = None
            if field in ("yicai:day", "dom:xinmin:edition_date"):
                if byte_len(value) == 10 and parse_date(value) is not None:
                    found = value
            elif field == "yicai:time-title" and "【" in value:
                time, title = value.split("【", 1)
                time = trim(time)
                if byte_len(time) == 5 and hour_minute(time) and "】" in title:
                    if trim(title.split("】", 1)[0]):
                        found = time
            if found is None:
                return
            root = "m-on" if site == "yicai" else "dzb-enter-info-box"
            for parent in reversed(self.stack):
                if parent.has(root):
                    if len(parent.pending) >= MAX_DATES:
                        self.overflow = True
                    else:
                        parent.pending.append((field, found))
                        if field == "yicai:time-title":
                            parent.title = True
                    break
            return
        candidate: str | None = value
        if site == "chinanews":
            date, sep, source = value.partition("来源：")
            candidate = trim(date) if sep and trim(source) else None
        elif site == "21jingji":
            parts = [part for part in _WHITESPACE.split(value) if part]
            if len(parts) < 2:
                candidate = None
            else:
                prefix = ascii_lower(" ".join(parts[:-2]))
                labelled = ":" in prefix or "：" in prefix or any(
                    label in prefix for label in _JINGJI_LABELS
                )
                candidate = None if labelled else " ".join(parts[-2:])
        elif site == "chinagongyi":
            for label in ("时间:", "时间："):
                if value.startswith(label):
                    candidate = trim(value[len(label) :])
                    break
            else:
                candidate = None
        if candidate is not None and parse_date(candidate) is not None:
            if self.push(field, candidate):
                for parent in self.stack:
                    if parent.has("article-view"):
                        parent.has_date = True

    def text(self, data: str) -> None:
        if self.stack and self.stack[-1].hidden:
            return
        title = bool(trim(data)) and any(frame.is_title for frame in self.stack)
        for frame in self.stack:
            if title:
                frame.title = True
            if (frame.field is not None or frame.date_context) and byte_len(
                frame.text
            ) <= MAX_FIELD_TEXT:
                # Yicai puts the whole brief in p; only its leading time/title
                # belongs to the header. Body text must not set parsing limits.
                yicai = frame.field == "yicai:time-title"
                if not yicai or "】" not in frame.text:
                    frame.text += data
                    if yicai and "】" in frame.text:
                        frame.text = frame.text[: frame.text.index("】") + 1]
        if any(
            (frame.field is not None or frame.date_context)
            and byte_len(frame.text) > MAX_FIELD_TEXT
            for frame in self.stack
        ):
            self.overflow = True


def _updated(text: str) -> bool:
    return "更新" in text or "updated" in ascii_lower(text)


def extract(document: dom.Document, url: str) -> list[tuple[str, str]]:
    """`(field, value)` publication dates from the known article header of `url`'s site."""
    site = site_of(url)
    if site is None:
        return []
    sink = _Headers(site)
    dom.walk(document.root, sink)
    conflicting_mobile = (
        site == "jiemian-mobile"
        and sink.roots > 1
        and any(a[1] != b[1] for a, b in zip(sink.dates, sink.dates[1:], strict=False))
    )
    if (
        (sink.roots == 1 or conflicting_mobile)
        and not sink.overflow
        and not document.overflow
        and not sink.updated_context
    ):
        return sink.dates
    return []
