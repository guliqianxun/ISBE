"""Paper text and figure candidates from arXiv's HTML, read token by token
without building a DOM.

Only `script`, `style` (raw text) and `textarea`, `title` (RCDATA) switch the
tokenizer state, and an article that is never closed is never complete.
`html.parser.HTMLParser` is the same kind of tokenizer; it is narrowed to
those four elements here.
"""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from isbe.core import validators
from isbe.core.validators import ascii_lower, byte_len, trim

from .text import WHITESPACE, collapse, split_whitespace

MAX_HTML = 10 << 20
MAX_DEPTH = 256
MAX_FIGURES = 32
MAX_PANELS = 12

_IGNORED = frozenset({
    "nav", "form", "button", "script", "style", "aside", "footer", "template", "noscript",
    "textarea", "select", "annotation", "annotation-xml",
})
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_BLOCKS = frozenset({"p", "div", "section", "figure", "blockquote", "pre"})
_CLOSE_BLOCKS = _HEADINGS | _BLOCKS | {"table"}
_IMAGE_BLOCKING = ("ltx_equation", "ltx_equationgroup", "ltx_math", "ltx_Math", "ltx_table")
_VOID = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
    "source", "track", "wbr",
})
# Raw text (False) and RCDATA (True, character references decoded).
_RAW = {"script": False, "style": False, "textarea": True, "title": True}
_RUNS = re.compile(f"(?P<space>[{WHITESPACE}]+)|[^{WHITESPACE}]+")

type Attrs = list[tuple[str, str | None]]


@dataclass
class Candidate:
    images: list[str] = field(default_factory=list)
    caption: str = ""
    invalid: str | None = None


@dataclass
class _Frame:
    name: str
    ignored: bool
    link: str | None
    figure: int | None
    caption: tuple[int, int, bool] | None
    image_blocked: bool


def attribute(attrs: Attrs, name: str) -> str | None:
    """The first attribute of that name (later duplicates are ignored)."""
    for key, value in attrs:
        if key == name:
            return "" if value is None else value
    return None


def has_class(attrs: Attrs, name: str) -> bool:
    classes = attribute(attrs, "class")
    return classes is not None and name in split_whitespace(classes)


def hidden(attrs: Attrs) -> bool:
    if attribute(attrs, "hidden") is not None or has_class(attrs, "ltx_hidden"):
        return True
    aria = attribute(attrs, "aria-hidden")
    if aria is not None and ascii_lower(aria) == "true":
        return True
    for rule in (attribute(attrs, "style") or "").split(";"):
        name, colon, value = rule.partition(":")
        if not colon:
            continue
        name, value = ascii_lower(trim(name)), ascii_lower(trim(value.split("!")[0]))
        if (name == "display" and value == "none") or (
            name == "visibility" and value in ("hidden", "collapse")
        ):
            return True
    return False


def link_url(href: str) -> str | None:
    """A link target shown after its text, resolved against arxiv.org as the WHATWG URL standard does."""
    if href.startswith("#"):
        return None
    try:
        url = validators.whatwg(href, "https://arxiv.org/")
    except ValueError:
        return None
    if url.protocol not in ("http:", "https:") or url.username or url.password:
        return None
    return url.href


class Extract(HTMLParser):
    """Feed it the page; read `text`, `characters`, `figures` and the flags after `close()`."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[_Frame] = []
        self.article: int | None = None
        self.finished = False
        self.overflow = False
        # One entry per character: popping and slicing stay cheap on long papers.
        self.out: list[str] = []
        self.size = 0  # UTF-8 bytes in `out`, the unit of the size limit
        self.characters = 0
        self.figures: list[Candidate] = []
        self.figure_limit_reached = False

    @property
    def text(self) -> str:
        return "".join(self.out)

    # --- the text being built --------------------------------------------------------

    def _push(self, text: str) -> None:
        self.out.extend(text)
        self.size += byte_len(text)

    def append(self, text: str) -> None:
        for run in _RUNS.finditer(text):
            if run.lastgroup == "space":
                if self.out and self.out[-1] not in " \n":
                    self._push(" ")
            else:
                chunk = run.group()
                self._push(chunk)
                self.characters += len(chunk)
        self.overflow |= self.size > MAX_HTML

    def newline(self, count: int) -> None:
        while self.out and self.out[-1] == " ":
            self.out.pop()
            self.size -= 1
        if self.out:
            present = 0
            while present < len(self.out) and self.out[-1 - present] == "\n":
                present += 1
            for _ in range(present, count):
                self._push("\n")

    # --- tokens ----------------------------------------------------------------------

    def set_cdata_mode(self, elem: str, *, escapable: bool = False) -> None:
        # HTMLParser follows the tree builder's list (xmp, iframe, plaintext, ...);
        # only these four switch the tokenizer here.
        if elem in _RAW:
            super().set_cdata_mode(elem, escapable=_RAW[elem])

    def handle_starttag(self, tag: str, attrs: Attrs) -> None:
        if not (self.overflow or self.finished):
            self.start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: Attrs) -> None:
        if self.overflow or self.finished:
            return
        self.start(tag, attrs, self_closing=True)
        # The tokenizer switches on the tag name alone, `/>` or not.
        self.set_cdata_mode(tag)

    def handle_endtag(self, tag: str) -> None:
        if not (self.overflow or self.finished):
            self.end(tag)

    def handle_data(self, data: str) -> None:
        if self.overflow or self.finished or self.article is None:
            return
        if self.stack and self.stack[-1].ignored:
            return
        # NUL characters are dropped from text.
        self.append(data.replace("\0", ""))

    def start(self, name: str, attrs: Attrs, *, self_closing: bool) -> None:
        parent = self.stack[-1] if self.stack else None
        ignored = (parent is not None and parent.ignored) or name in _IGNORED or hidden(attrs)
        if (
            not ignored
            and self.article is None
            and not self.finished
            and name == "article"
            and has_class(attrs, "ltx_document")
        ):
            self.article = len(self.stack)
        active = self.article is not None and not self.finished and not ignored
        href = attribute(attrs, "href") if active and name == "a" else None
        link = None if href is None else link_url(href)
        group = next((f.figure for f in reversed(self.stack) if f.figure is not None), None)
        image_blocked = (
            (parent is not None and parent.image_blocked)
            or name in ("math", "svg")
            or any(has_class(attrs, c) for c in _IMAGE_BLOCKING)
            or (name == "table" and group is None)
        )
        figure = None
        if (
            active and not image_blocked and group is None
            and name == "figure" and has_class(attrs, "ltx_figure")
        ):
            if len(self.figures) < MAX_FIGURES:
                self.figures.append(Candidate())
                figure = len(self.figures) - 1
            else:
                self.figure_limit_reached = True
        caption = None
        if active and name == "figcaption":
            self.newline(1)
            if group is not None:
                nearest = next((f for f in reversed(self.stack) if f.name == "figure"), None)
                main = nearest is not None and nearest.figure == group
                caption = (group, len(self.out), main)
        if active and not image_blocked and name == "img" and group is not None:
            self._panel(self.figures[group], attribute(attrs, "src"))
        if active:
            ignored = self._open(name, attrs) or ignored
        if name not in _VOID and not self_closing:
            if len(self.stack) >= MAX_DEPTH:
                self.overflow = True
                return
            self.stack.append(_Frame(name, ignored, link, figure, caption, image_blocked))

    def _panel(self, candidate: Candidate, src: str | None) -> None:
        if candidate.invalid is not None:
            return
        if src is None or not trim(src):
            candidate.invalid = "missing image src"
        elif byte_len(src) > 4096:
            candidate.invalid = "image src exceeds 4096 bytes"
        elif len(candidate.images) < MAX_PANELS:
            candidate.images.append(src)
        else:
            candidate.invalid = "more than 12 panels"
        if candidate.invalid is not None:
            candidate.images.clear()

    def _open(self, name: str, attrs: Attrs) -> bool:
        """Text structure for an active start tag; True when its content is replaced."""
        if name in _HEADINGS:
            self.newline(2)
            self._push("## ")
        elif name in _BLOCKS:
            self.newline(2)
        elif name in ("br", "hr"):
            self.newline(1)
        elif name == "li":
            self.newline(1)
            self._push("- ")
        elif name == "table":
            # shortcut: table cells stay in source order; add span-aware layout if consumers need a reconstructed grid.
            self.newline(2)
            self._push("### Table\n")
        elif name == "tr":
            self.newline(1)
            self._push("| ")
        elif name == "math":
            alt = attribute(attrs, "alttext")
            if alt is not None and trim(alt):
                self.append(alt)
                return True
        return False

    def end(self, name: str) -> None:
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i].name == name), None)
        if index is None:
            return
        frame = self.stack[index]
        del self.stack[index:]
        if frame.caption is not None:
            group, start, main = frame.caption
            caption = collapse("".join(self.out[start:]))[:4096]
            candidate = self.figures[group]
            if caption:
                if not candidate.caption:
                    candidate.caption = caption
                elif main:
                    candidate.caption = f"{caption} {candidate.caption}"
                else:
                    candidate.caption += " " + caption
                candidate.caption = candidate.caption[:4096]
        if self.article is not None and not self.finished and not frame.ignored:
            if frame.link is not None:
                self.append(f" ({frame.link})")
            if name in _CLOSE_BLOCKS:
                self.newline(2)
            elif name in ("td", "th"):
                self._push(" | ")
            elif name in ("tr", "caption", "li"):
                self.newline(1)
        if self.article is not None and index <= self.article:
            self.finished = self.article == index and frame.name == "article"
            self.article = None
