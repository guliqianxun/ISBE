"""A small HTML tree builder for original pages.

Feeding a tokenizer straight into a stack of open tags (as the earlier Rust
build did) leaves elements open that a browser closes when optional end tags
are omitted (`</head>`, `</p>`, `</li>`, `</td>`): a page without `</head>` has
no visible body, and an unclosed `<p hidden>` hides the paragraphs after it.
Here the stdlib tokenizer (HTML5 tokenization: raw text, RCDATA, character
references) drives the parts of the HTML5 tree-construction algorithm that
decide nesting: the implied `html`/`head`/`body`, implied end tags, scope
checks, the "any other end tag" rule, table cells and rows, and foreign
(SVG/MathML) content with its HTML breakout. Formatting-element
reconstruction and foster parenting are left out; they move inline elements
but do not change which text is inside which block.

Each element records whether its own end tag closed it (`closed`); an element
closed implicitly — by an ancestor's end tag, an implied end tag or the end of
the input — is not `closed`. The article extractor needs this to tell a
complete body from a truncated one, which a DOM alone does not keep.

Nesting is bounded: an element that would be the `max_depth`-th open element
stops the build and sets `Document.overflow`.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser

MAX_DEPTH = 128

VOID = frozenset(
    "area base basefont bgsound br col embed frame hr img input keygen link meta param "
    "source track wbr".split()
)
# Raw text and RCDATA elements; <noscript> too, as in a browser with scripting enabled.
_RAW = frozenset("script style textarea title xmp iframe noembed noframes noscript".split())
_SPECIAL = frozenset(
    "address applet area article aside base basefont bgsound blockquote body br button "
    "caption center col colgroup dd details dir div dl dt embed fieldset figcaption figure "
    "footer form frame frameset h1 h2 h3 h4 h5 h6 head header hgroup hr html iframe img "
    "input keygen li link listing main marquee menu meta nav noembed noframes noscript "
    "object ol p param plaintext pre script search section select source style summary "
    "table tbody td template textarea tfoot th thead title tr track ul wbr xmp".split()
)
_SCOPE = frozenset("applet caption html table td th marquee object template".split())
_FOREIGN_SCOPE = {
    "math": frozenset("mi mo mn ms mtext annotation-xml".split()),
    "svg": frozenset("foreignobject desc title".split()),
}
_IMPLIED = frozenset("dd dt li optgroup option p rb rp rt rtc".split())
_CLOSES_P = frozenset(
    "address article aside blockquote center details dialog dir div dl fieldset figcaption "
    "figure footer header hgroup main menu nav ol p search section summary ul pre listing "
    "form table hr xmp plaintext".split()
)
_HEADINGS = frozenset("h1 h2 h3 h4 h5 h6".split())
_BLOCK_END = frozenset(
    "address article aside blockquote button center details dialog dir div dl fieldset "
    "figcaption figure footer form header hgroup listing main menu nav ol pre search "
    "section summary ul applet marquee object template".split()
)
_TABLE = frozenset("caption col colgroup tbody td tfoot th thead tr".split())
_HEAD_ONLY = frozenset(
    "base basefont bgsound link meta noframes noscript script style template title".split()
)
_BREAKOUT = frozenset(
    "b big blockquote body br center code dd div dl dt em embed h1 h2 h3 h4 h5 h6 head hr i "
    "img li listing menu meta nobr ol p pre ruby s small span strong strike sub sup table tt "
    "u ul var".split()
)
_ASCII_SPACE = "\t\n\x0c\r "


@dataclass(eq=False, slots=True)
class Element:
    name: str
    attrs: dict[str, str]
    foreign: str | None = None  # "svg" or "math" outside the HTML namespace
    children: list[Element | str] = field(default_factory=list)
    closed: bool = False

    @property
    def void(self) -> bool:
        return self.foreign is None and self.name in VOID

    def attr(self, name: str) -> str | None:
        return self.attrs.get(name)

    def html(self, *names: str) -> bool:
        return self.foreign is None and self.name in names


@dataclass(slots=True)
class Document:
    root: Element
    head: Element
    overflow: bool = False


class _Stop(Exception):
    pass


class _Builder(HTMLParser):
    def __init__(self, max_depth: int) -> None:
        super().__init__(convert_charrefs=True, scripting=True)
        self.max_depth = max_depth
        self.html = Element("html", {})
        self.head: Element | None = None
        self.body: Element | None = None
        self.stack: list[Element] = [self.html]
        self.overflow = False
        self._foreign_start = False

    # --- tokenizer hooks -------------------------------------------------------------

    def set_cdata_mode(self, elem: str, *, escapable: bool = False) -> None:
        # <style>, <title> and <script> inside SVG or MathML hold markup, not raw text.
        if not self._foreign_start:
            super().set_cdata_mode(elem, escapable=escapable)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self_closing=True)
        # HTML ignores the self-closing flag: <script/> still starts script data.
        if not self._foreign_start and tag in _RAW:
            self.set_cdata_mode(tag, escapable=tag in ("textarea", "title"))

    def handle_endtag(self, tag: str) -> None:
        self._end(tag)

    def handle_data(self, data: str) -> None:
        self._text(data)

    def unknown_decl(self, data: str) -> None:
        if data.startswith("CDATA[") and self.stack[-1].foreign:
            self._text(data[6:])

    # --- the tree --------------------------------------------------------------------

    @property
    def current(self) -> Element:
        return self.stack[-1]

    def _insert(
        self, name: str, attrs: dict[str, str], *, foreign: str | None = None,
        self_closing: bool = False, parent: Element | None = None,
    ) -> Element:  # fmt: skip
        element = Element(name, attrs, foreign)
        (parent or self.current).children.append(element)
        if element.void:
            return element
        if foreign and self_closing:
            element.closed = True
            return element
        if len(self.stack) >= self.max_depth:
            self.overflow = True
            raise _Stop
        self.stack.append(element)
        return element

    def _pop_until(self, target: Element, *, closed: bool) -> None:
        while self.stack[-1] is not target:
            self.stack.pop()
        self.stack.pop()
        target.closed = closed

    def _find(
        self, names: frozenset[str] | tuple[str, ...], extra: frozenset[str] = frozenset(),
        *, table: bool = False,
    ) -> Element | None:  # fmt: skip
        """The innermost open HTML element named in `names` that is in (table) scope."""
        for element in reversed(self.stack):
            if element.foreign is None:
                if element.name in names:
                    return element
                boundary = (
                    element.name in ("html", "table", "template")
                    if table
                    else element.name in _SCOPE or element.name in extra
                )
            else:
                boundary = not table and element.name in _FOREIGN_SCOPE[element.foreign]
            if boundary:
                return None
        return None

    def _table_context(self) -> Element | None:
        """The innermost open table when content goes directly into it, not into a cell."""
        for element in reversed(self.stack):
            if element.html("td", "th", "caption", "template", "html"):
                return None
            if element.html("table"):
                return element
        return None

    def _close_p(self) -> None:
        p = self._find(("p",), frozenset({"button"}))
        if p is not None:
            self._pop_until(p, closed=False)

    # --- insertion modes -------------------------------------------------------------

    def _ensure_head(self) -> Element:
        if self.head is None:
            self.head = self._insert("head", {})
        return self.head

    def _leave_head(self) -> None:
        """Close the head (and anything open in it) before body content."""
        head = self._ensure_head()
        if head in self.stack:
            self._pop_until(head, closed=False)

    def _ensure_body(self) -> None:
        if self.body is None:
            self._leave_head()
            self.body = self._insert("body", {})

    def _start(self, tag: str, raw: list[tuple[str, str | None]], *, self_closing: bool) -> None:
        if self.overflow:
            return
        attrs: dict[str, str] = {}
        for name, value in raw:
            attrs.setdefault(name, value or "")  # the first of duplicate attributes wins
        self._foreign_start = False
        try:
            self._dispatch_start(tag, attrs, self_closing)
        except _Stop:
            pass

    def _dispatch_start(self, tag: str, attrs: dict[str, str], self_closing: bool) -> None:
        current = self.current
        if current.foreign is not None and current.name not in _FOREIGN_SCOPE[current.foreign]:
            breakout = tag in _BREAKOUT or (
                tag == "font" and any(a in attrs for a in ("color", "face", "size"))
            )
            if not breakout:
                self._foreign_start = True
                self._insert(tag, attrs, foreign=current.foreign, self_closing=self_closing)
                return
            while self.current.foreign is not None and (
                self.current.name not in _FOREIGN_SCOPE[self.current.foreign]
            ):
                self.stack.pop()
        if tag == "html":
            for name, value in attrs.items():
                self.html.attrs.setdefault(name, value)
            return
        if self.body is None and self.current not in (self.html, self.head):
            self._body_start(tag, attrs, self_closing)  # inside a <template> in the head
            return
        if self.body is None:
            if tag == "head":
                if self.head is None:
                    self._ensure_head()
                return
            if tag in _HEAD_ONLY:
                head = self._ensure_head()
                # After </head>, head content still goes into the head element.
                self._insert(tag, attrs, parent=head if self.current is self.html else None)
                return
            if tag == "body":
                self._leave_head()
                self.body = self._insert("body", attrs)
                return
            self._ensure_body()
        elif tag in ("head", "frameset", "frame"):
            return
        elif tag == "body":
            for name, value in attrs.items():
                self.body.attrs.setdefault(name, value)
            return
        self._body_start(tag, attrs, self_closing)

    def _body_start(self, tag: str, attrs: dict[str, str], self_closing: bool) -> None:
        if tag in ("svg", "math"):
            self._foreign_start = True
            self._insert(tag, attrs, foreign=tag, self_closing=self_closing)
            return
        if tag == "image":
            tag = "img"
        if tag in _TABLE:
            self._table_start(tag, attrs)
            return
        if tag == "table" and (open_table := self._table_context()) is not None:
            self._pop_until(open_table, closed=False)
        if tag == "li" or tag in ("dd", "dt"):
            names = ("li",) if tag == "li" else ("dd", "dt")
            for element in reversed(self.stack):
                if element.html(*names):
                    self._pop_until(element, closed=False)
                    break
                if element.foreign is None and element.name in _SPECIAL and element.name not in (
                    "address", "div", "p"
                ):
                    break
        if tag in _CLOSES_P or tag in _HEADINGS or tag in ("li", "dd", "dt"):
            self._close_p()
        if tag in _HEADINGS and self.current.html(*_HEADINGS):
            self.stack.pop()
        elif tag in ("button", "a", "nobr") and (open_ := self._find((tag,))) is not None:
            self._pop_until(open_, closed=False)
        elif tag in ("option", "optgroup") and self.current.html("option"):
            self.stack.pop()
        elif tag in ("rb", "rtc", "rp", "rt") and self._find(("ruby",)) is not None:
            keep = ("rtc",) if tag in ("rp", "rt") else ()
            while self.current.html(*_IMPLIED) and not self.current.html(*keep):
                self.stack.pop()
        self._insert(tag, attrs)

    def _table_start(self, tag: str, attrs: dict[str, str]) -> None:
        table = self._find(("table",), table=True)
        if table is None:
            return  # table parts outside a table are parse errors and ignored
        if tag in ("td", "th", "tr"):
            cell = self._find(("td", "th"), table=True)
            if cell is not None:
                self._pop_until(cell, closed=False)
        if tag == "tr" and (row := self._find(("tr",), table=True)) is not None:
            self._pop_until(row, closed=False)
        if tag in ("tbody", "thead", "tfoot", "caption", "colgroup", "col"):
            while self.current is not table:
                self.stack.pop()
        self._insert(tag, attrs)

    def _end(self, tag: str) -> None:
        if self.overflow:
            return
        current = self.current
        if current.foreign is not None:
            # Foreign elements close by name until the nearest HTML element.
            for element in reversed(self.stack):
                if element.foreign is None:
                    break
                if element.name == tag:
                    self._pop_until(element, closed=True)
                    return
        if current.html(*_RAW) and current.name == tag:
            self._pop_until(current, closed=True)
            return
        if self.body is None and current in (self.html, self.head):
            if tag == "head" and current is self.head:
                self._pop_until(current, closed=True)
                return
            if tag not in ("body", "html", "br"):
                return
            self._ensure_body()
        if tag in ("body", "html", "head"):
            return  # </body> does not close the body; later content still belongs to it
        if tag == "br":
            self._insert("br", {})
        elif tag == "p":
            p = self._find(("p",), frozenset({"button"}))
            if p is None:
                p = self._insert("p", {})
            self._pop_until(p, closed=True)
        elif tag in _BLOCK_END or tag in ("li", "dd", "dt"):
            extra = frozenset({"ol", "ul"}) if tag == "li" else frozenset()
            if (open_ := self._find((tag,), extra)) is not None:
                self._pop_until(open_, closed=True)
        elif tag in _HEADINGS:
            if (open_ := self._find(_HEADINGS)) is not None:
                self._pop_until(open_, closed=True)
        elif tag == "table" or tag in _TABLE:
            if (open_ := self._find((tag,), table=True)) is not None:
                self._pop_until(open_, closed=True)
        else:
            for element in reversed(self.stack):
                if element.html(tag):
                    self._pop_until(element, closed=True)
                    return
                if element.foreign is None and element.name in _SPECIAL:
                    return

    def _text(self, data: str) -> None:
        if self.overflow:
            return
        try:
            if self.body is None and self.current in (self.html, self.head):
                kept = data.lstrip(_ASCII_SPACE)
                # Whitespace before the body stays in the head (or after it); text starts the body.
                if len(kept) < len(data) and self.head is not None:
                    self._append(data[: len(data) - len(kept)])
                if not kept:
                    return
                self._ensure_body()
                data = kept
            self._append(data)
        except _Stop:
            pass

    def _append(self, data: str) -> None:
        children = self.current.children
        if children and isinstance(children[-1], str):
            children[-1] += data
        else:
            children.append(data)


def parse(html: str, max_depth: int = MAX_DEPTH) -> Document:
    """Build the tree of `html`; never raises on malformed markup."""
    builder = _Builder(max_depth)
    # Input preprocessing: CR LF and lone CR become LF; a byte-order mark is not
    # content (the decoder drops it), so it must not start the body.
    builder.feed(html.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n"))
    builder.close()
    head = builder.head or Element("head", {})
    return Document(builder.html, head, builder.overflow)


def iter_elements(node: Element) -> Iterator[Element]:
    """`node`'s descendants in tree order."""
    for child in node.children:
        if isinstance(child, Element):
            yield child
            yield from iter_elements(child)


class Sink:
    """Receives a document in tree order: `start` for every element, `end` for every
    element that can have content, `text` for character data between them."""

    def stopped(self) -> bool:
        return False

    def start(self, element: Element) -> None: ...

    def end(self, element: Element) -> None: ...

    def text(self, data: str) -> None: ...


def walk(node: Element, sink: Sink) -> None:
    """Feed `node` and its subtree to `sink`, until `sink.stopped()`."""
    sink.start(node)
    if node.void:
        return
    for child in node.children:
        if sink.stopped():
            return
        if isinstance(child, str):
            sink.text(child)
        else:
            walk(child, sink)
    if not sink.stopped():
        sink.end(node)
