"""Visible original article text, without search-snippet completion or model calls.

The body is the one container a page
marks as its article (`itemprop="articleBody"`, or a publisher container
checked against the original pages); text outside it, inside page chrome or
hidden markup is never presented as the article. A body that is missing,
ambiguous, truncated or over the limits is refused rather than shortened.
"""

import re
from dataclasses import dataclass

from isbe.articles import dom
from isbe.core.validators import ascii_lower, byte_len, trim, whatwg

MAX_HTML = 1 << 20
MAX_TEXT = 256 << 10
MAX_DEPTH = 128
MAX_PARAGRAPHS = 512
MAX_TITLE = 2048

_IGNORED = frozenset(
    "head script style nav aside footer form button select textarea template noscript svg "
    "iframe".split()
)
_BLOCK = frozenset("p div section article blockquote pre li br hr h1 h2 h3 h4 h5 h6 tr".split())
_NON_ARTICLE = ("related", "recommend", "recommendations", "comment", "comments", "advert", "share")
# Unicode White_Space.
_WHITESPACE = re.compile(
    "[\t\n\x0b\x0c\r \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+"
)
_ASCII_WHITESPACE = re.compile("[\t\n\x0c\r ]+")


def split_ascii(value: str) -> list[str]:
    """Split on ASCII whitespace."""
    return [part for part in _ASCII_WHITESPACE.split(value) if part]


def has_class(element: dom.Element, name: str) -> bool:
    return name in split_ascii(element.attr("class") or "")


def clean_title(value: str) -> str | None:
    value = " ".join(part for part in _WHITESPACE.split(value) if part)
    return value if value and byte_len(value) <= MAX_TITLE else None


def hidden(element: dom.Element) -> bool:
    if element.attr("hidden") is not None:
        return True
    if ascii_lower(element.attr("aria-hidden") or "") == "true":
        return True
    for rule in (element.attr("style") or "").split(";"):
        name, colon, value = rule.partition(":")
        if not colon:
            continue
        name, value = ascii_lower(trim(name)), ascii_lower(trim(value.split("!")[0]))
        if (name == "display" and value == "none") or (
            name == "visibility" and value in ("hidden", "collapse")
        ):
            return True
    return False


def non_article_region(element: dom.Element) -> bool:
    for name in ("class", "id"):
        for part in split_ascii(element.attr(name) or ""):
            part = ascii_lower(part)
            if any(part == p or part.startswith(p + "-") for p in _NON_ARTICLE):
                return True
    return False


@dataclass(slots=True)
class _Frame:
    element: dom.Element
    ignored: bool
    article_content: bool
    yicai_brief: bool


class _Page(dom.Sink):
    def __init__(self, host: str, path: str) -> None:
        self.host = host
        self.path = path
        self.stack: list[_Frame] = []
        self.root: int | None = None
        self.roots = 0
        self.complete = False
        self.overflow = False
        self.bytes = 0
        self.buffer: list[str] = []
        self.trailing_space = False
        self.paragraphs: list[str] = []
        self.og_title: str | None = None
        self.title: dom.Element | None = None

    def stopped(self) -> bool:
        return self.overflow

    def article_body(self, element: dom.Element) -> bool:
        if "articleBody" in split_ascii(element.attr("itemprop") or ""):
            return True
        # These containers were checked against the original publisher pages, not snippets.
        host, path, name = self.host, self.path, element.name
        if host == "news.qq.com" and path.startswith("/rain/a/"):
            return has_class(element, "rich_media_content") and any(
                frame.article_content for frame in self.stack
            )
        if host == "www.cnr.cn":
            return has_class(element, "article-content")
        if host == "m.jiemian.com" and path.startswith("/article/"):
            return has_class(element, "article-content")
        if host == "www.sh.chinanews.com.cn":
            return has_class(element, "cms-news-article-content-blocknew")
        if host == "www.sohu.com" and path.startswith("/a/"):
            return name == "article" and element.attr("id") == "mp-editor"
        if host == "k.sina.com.cn" and path.startswith("/article_"):
            return element.attr("id") == "article"
        if host == "news.sina.cn":
            return has_class(element, "art_content")
        if host == "www.163.com" and path.startswith("/dy/article/"):
            return has_class(element, "post_body")
        if host == "www.thepaper.cn" and path.startswith("/newsDetail_forward_"):
            classes = split_ascii(element.attr("class") or "")
            return any(part.startswith("cententWrap__") for part in classes)
        if host == "www.prnasia.com" and path.startswith("/story/"):
            return element.attr("id") == "dvContent"
        if host == "bond.10jqka.com.cn":
            return has_class(element, "news-content-parsed")
        if host == "www.yicai.com" and path.startswith("/brief/"):
            return name == "p" and bool(self.stack) and self.stack[-1].yicai_brief
        return False

    def active(self) -> bool:
        return self.root is not None and not (self.stack and self.stack[-1].ignored)

    def paragraph(self) -> None:
        value = "".join(self.buffer).strip(" ")  # whitespace is already folded to single spaces
        self.buffer.clear()
        self.trailing_space = False
        if value:
            if len(self.paragraphs) >= MAX_PARAGRAPHS:
                self.overflow = True
            else:
                self.paragraphs.append(value)

    def append(self, value: str) -> None:
        # Each whitespace run becomes one space, never at the start of a paragraph.
        for index, piece in enumerate(_WHITESPACE.split(value)):
            if index and self.buffer and not self.trailing_space:
                self.buffer.append(" ")
                self.bytes += 1
                self.trailing_space = True
            if piece:
                self.buffer.append(piece)
                self.bytes += byte_len(piece)
                self.trailing_space = False
        if self.bytes > MAX_TEXT:
            self.overflow = True

    def start(self, element: dom.Element) -> None:
        name = element.name
        if element.html("meta") and element.attr("property") == "og:title":
            content = element.attr("content")
            self.og_title = None if content is None else clean_title(content)
        if element.html("title") and self.title is None:
            # document.title: the first HTML <title>; an SVG icon's <title> is not the page's.
            self.title = element
        ignored = (
            (bool(self.stack) and self.stack[-1].ignored)
            or name in _IGNORED
            or hidden(element)
            or non_article_region(element)
            or (self.host == "www.sohu.com" and element.attr("id") == "backsohucom")
            or (self.host == "m.jiemian.com" and has_class(element, "j_ads"))
        )
        if not ignored and self.article_body(element):
            self.roots += 1
            if self.root is None:
                self.root = len(self.stack)
        if not ignored and self.active() and name in _BLOCK:
            self.paragraph()
        if not element.void:
            self.stack.append(
                _Frame(
                    element,
                    ignored,
                    article_content=element.attr("id") == "article-content",
                    yicai_brief=has_class(element, "m-on"),
                )
            )

    def end(self, element: dom.Element) -> None:
        if self.active() and element.name in _BLOCK:
            self.paragraph()
        if self.root is not None and len(self.stack) - 1 == self.root:
            self.paragraph()
            # Closed by its own end tag; an ancestor's end tag or the end of input truncated it.
            self.complete = element.closed
            self.root = None
        self.stack.pop()

    def text(self, data: str) -> None:
        if self.stack and self.stack[-1].element.html("title"):
            return
        if self.active():
            self.append(data)


def element_text(element: dom.Element) -> str:
    """The concatenated text of `element`'s subtree (`textContent`)."""
    return "".join(
        child if isinstance(child, str) else element_text(child) for child in element.children
    )


def parse(html: str, url: str) -> tuple[list[str], str | None]:
    """The article paragraphs of the page at `url`, and its title (`og:title`, else `<title>`).

    Raises ValueError when the page has no complete, unique, supported body.
    The size cap counts characters: the fetch already capped the bytes, and a
    page in a two-byte charset must not count twice.
    """
    if len(html) > MAX_HTML:
        raise ValueError("Article HTML exceeds extraction limit")
    parsed = whatwg(url)
    page = _Page(parsed.hostname, parsed.pathname)
    document = dom.parse(html, MAX_DEPTH)
    dom.walk(document.root, page)
    if page.overflow or document.overflow:
        raise ValueError("Article exceeds extraction limits")
    if page.roots != 1:
        raise ValueError("No unique supported article body found")
    if not (page.complete and page.root is None):
        raise ValueError("Article body is incomplete")
    if not page.paragraphs:
        raise ValueError("Article body is empty")
    title = page.og_title
    if title is None and page.title is not None:
        title = clean_title(element_text(page.title))
    return page.paragraphs, title
