"""What the reader takes from an HTML page:
listing links, feed links, the `<title>` and the visible text.

lxml builds the tree, which closes elements whose optional end tag is omitted
(`</p>`, `</li>`, `</head>`) as HTML does: a hidden `<p hidden>` cannot swallow
the rest of the page and a long `<li>` list cannot overflow the nesting limit.
A walk over the tree replays the start, text and end steps of a tokenizer.
"""

from dataclasses import dataclass, field

from lxml import etree  # type: ignore[import-untyped]  # no stubs in the lock

from isbe.collect.items import split_whitespace, squash
from isbe.core import validators

MAX_BODY = 2 << 20
MAX_LINKS = 4096
MAX_DEPTH = 128

_IGNORED = frozenset(
    {"script", "style", "template", "noscript", "nav", "footer", "form", "button",
     "textarea", "select", "svg"}
)
_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
     "source", "track", "wbr"}
)
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_SPACED = frozenset({"p", "div", "li", "br"})
# lxml always adds these; they count only when the page wrote them.
_IMPLIED = frozenset({"html", "head", "body"})
_FEED_TYPES = frozenset({"application/rss+xml", "application/atom+xml"})
_ASCII_SPACE = " \t\n\x0c\r"
_NOT_TITLES = frozenset(
    {"首页", "返回首页", "返回列表", "上一页", "下一页", "更多", "基本信息", "招标信息",
     "home", "next", "previous", "read more", "more", "back"}
)


class PageError(Exception):
    pass


@dataclass
class Link:
    href: str
    text: str = ""
    title_text: str = ""
    title_attribute: str = ""


@dataclass
class Page:
    links: list[Link] = field(default_factory=list)
    feeds: list[Link] = field(default_factory=list)
    title: str = ""
    text: str = ""
    truncated: bool = False


def human_title(value: str) -> bool:
    return len(value) >= 4 and validators.ascii_lower(value) not in _NOT_TITLES


def _hidden(el: etree._Element) -> bool:
    if el.get("hidden") is not None:
        return True
    if validators.ascii_lower(el.get("aria-hidden") or "-") == "true":
        return True
    for rule in (el.get("style") or "").split(";"):
        if ":" not in rule:
            continue
        name, value = rule.split(":", 1)
        name = validators.ascii_lower(validators.trim(name))
        value = validators.ascii_lower(validators.trim(value.split("!", 1)[0]))
        if (name, value) == ("display", "none") or (
            name == "visibility" and value in ("hidden", "collapse")
        ):
            return True
    return False


def _feed_link(el: etree._Element) -> bool:
    rel, kind = el.get("rel"), el.get("type")
    if rel is None or kind is None:
        return False
    alternate = any(validators.ascii_lower(part) == "alternate" for part in split_whitespace(rel))
    return alternate and validators.ascii_lower(validators.trim(kind.split(";", 1)[0])) in _FEED_TYPES


class _Walk:
    """The state of the walk: open elements, links, title and text."""

    def __init__(self) -> None:
        self.page = Page()
        self.stack: list[tuple[str, bool]] = []
        self.counted = 0  # stack entries other than lxml's implied html/head/body
        self.anchor: tuple[int, Link] | None = None
        self.title_depth: int | None = None

    def finish_anchor(self) -> None:
        self.title_depth = None
        if self.anchor is None:
            return
        link = self.anchor[1]
        self.anchor = None
        if validators.trim(link.title_text):
            link.text = link.title_text
        else:
            visible, title = squash(link.text), squash(link.title_attribute)
            cuts = [i for i in (visible.find("…"), visible.find("...")) if i >= 0]
            prefix = _trim_end(visible[: min(cuts)]) if cuts else ""
            if (
                human_title(visible)
                and human_title(title)
                and (visible.startswith(title) or (prefix and title.startswith(prefix)))
            ):
                link.text = title
        if len(self.page.links) < MAX_LINKS:
            self.page.links.append(link)
        else:
            self.page.truncated = True

    def start(self, el: etree._Element, name: str) -> bool:
        """The start tag; whether the element went on the stack."""
        if name == "a":
            self.finish_anchor()
        ignored = bool(self.stack and self.stack[-1][1]) or name in _IGNORED or _hidden(el)
        if not ignored and name == "a" and (href := el.get("href")) is not None:
            self.anchor = (len(self.stack), Link(href, title_attribute=el.get("title") or ""))
        if (
            not ignored
            and (name in _HEADINGS or _has_class(el, "title"))
            and self.title_depth is None
            and self.anchor is not None
            and not validators.trim(self.anchor[1].title_text)
        ):
            self.title_depth = len(self.stack)
        if not ignored and name == "link" and _feed_link(el):
            if len(self.page.feeds) < MAX_LINKS:
                self.page.feeds.append(Link(el.get("href") or "", text=el.get("title") or ""))
            else:
                self.page.truncated = True
        if name in _VOID:
            return False
        implied = name in _IMPLIED
        if not implied and self.counted == MAX_DEPTH:
            raise PageError("HTML nesting exceeds 128 levels")
        self.counted += not implied
        self.stack.append((name, ignored))
        return True

    def end(self, name: str) -> None:
        index = len(self.stack) - 1
        if self.title_depth is not None and self.title_depth >= index:
            self.title_depth = None
        if self.anchor is not None and self.anchor[0] >= index:
            self.finish_anchor()
        self.counted -= name not in _IMPLIED
        self.stack.pop()
        if name in _SPACED:
            self.page.text += " "

    def text(self, value: str | None) -> None:
        if not value or (self.stack and self.stack[-1][1]):
            return
        self.page.text += value
        if self.stack and self.stack[-1][0] == "title":
            self.page.title += value
        if self.anchor is not None:
            self.anchor[1].text += value
            if self.title_depth is not None:
                self.anchor[1].title_text += value


def _trim_end(value: str) -> str:
    end = len(value)
    while end and validators.is_space(value[end - 1]):
        end -= 1
    return value[:end]


def _has_class(el: etree._Element, name: str) -> bool:
    """Whether `name` is one of the element's classes (split on ASCII whitespace only)."""
    classes = el.get("class") or ""
    for space in _ASCII_SPACE[1:]:
        classes = classes.replace(space, " ")
    return name in classes.split(" ")


def _tree(html: bytes) -> etree._Element | None:
    # A parser per call: lxml parsers are not meant to be shared between threads.
    parser = etree.HTMLParser(
        encoding="utf-8", recover=True, no_network=True, remove_comments=True, remove_pis=True,
        compact=False, default_doctype=False, collect_ids=False,
    )
    try:
        return etree.fromstring(html, parser)
    except etree.XMLSyntaxError:
        return None  # nothing but blanks or comments: an empty page


def _utf8_error(error: UnicodeDecodeError) -> str:
    """The message for a UTF-8 decoding failure: where it starts and how long it is."""
    if error.end == len(error.object) and error.reason == "unexpected end of data":
        return f"incomplete utf-8 byte sequence from index {error.start}"
    return f"invalid utf-8 sequence of {error.end - error.start} bytes from index {error.start}"


def parse(raw: bytes) -> Page:
    """Links, feed links, title and text of an HTML page (at most 2 MiB, UTF-8)."""
    if len(raw) > MAX_BODY:
        raise PageError("HTML exceeds 2 MiB")
    # shortcut: non-UTF-8 pages fail explicitly; add encoding support
    # when such a source is configured.
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PageError("source HTML must be UTF-8") from PageError(_utf8_error(error))
    walk = _Walk()
    root = _tree(raw)
    todo: list[tuple[etree._Element, int]] = [] if root is None else [(root, 0)]
    # 0: enter the element; 1: leave a stacked one; 2: leave one that was not stacked.
    while todo:
        el, step = todo.pop()
        if step:
            if step == 1:
                walk.end(str(el.tag))
            walk.text(el.tail)
            continue
        if not isinstance(el.tag, str):  # an entity reference or similar: only its tail is text
            walk.text(el.tail)
            continue
        name = el.tag
        stacked = walk.start(el, name)
        walk.text(el.text)
        todo.append((el, 1 if stacked else 2))
        todo.extend((child, 0) for child in reversed(el))
    walk.finish_anchor()
    return walk.page


def plain_text(value: str) -> str:
    """Feed text as shown to the user: markup dropped, whitespace squashed; "" if unparseable."""
    try:
        return squash(parse(value.encode()).text)
    except PageError:
        return ""
