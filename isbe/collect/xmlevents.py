"""Element stream over the tokenizer (`xmltokens`): one event of lookahead,
depth tracking, `xml:base` scopes and namespace defaults.

Errors are `XmlError`s; `Element.pos` is the byte offset printed in
"at position N".
"""

import re
from collections.abc import Iterator

from isbe.core import validators

from .xmltokens import UNCLOSED_TAG, End, Event, Start, Text, XmlError, events

ATOM, RSS, UNKNOWN = "atom", "rss", "unknown"
CONTENT, DUBLIN_CORE, MEDIA_RSS = "content", "dc", "mediarss"
ITUNES, PODCAST, COMMENTS = "itunes", "podcast", "wfw"
_NAMESPACES = {
    "http://purl.org/rss/1.0/": RSS,
    "http://www.w3.org/2005/Atom": ATOM,
    "http://purl.org/rss/1.0/modules/content/": CONTENT,
    "http://purl.org/dc/elements/1.1/": DUBLIN_CORE,
    "http://search.yahoo.com/mrss/": MEDIA_RSS,
    "http://www.itunes.com/dtds/podcast-1.0.dtd": ITUNES,
    "https://podcastindex.org/namespace/1.0": PODCAST,
    "http://wellformedweb.org/CommentAPI/": COMMENTS,
}
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_C0_OR_SPACE = "".join(map(chr, range(0x21)))
_SPECIAL = {"http", "https", "ws", "wss", "ftp", "file"}
__all__ = ["UNCLOSED_TAG", "Element", "Source", "XmlError", "has_scheme", "parse_uri"]


def has_scheme(value: str) -> bool:
    """Whether `value` parses as an absolute URL (else: relative without base)."""
    cleaned = value.strip(_C0_OR_SPACE)
    for c in "\t\n\r":
        cleaned = cleaned.replace(c, "")
    return _SCHEME.match(cleaned) is not None


def _port_ok(port: str) -> bool:
    return port == "" or (port.isascii() and port.isdigit() and int(port) <= 65535)


def url_error(value: str) -> str:
    """The reason for rejecting `value` (which has a scheme). Only the fact that
    WHATWG parsing failed is known, so this names the likely part."""
    cleaned = value.strip(_C0_OR_SPACE).replace("\t", "").replace("\n", "").replace("\r", "")
    scheme, _, rest = cleaned.partition(":")
    special = scheme.lower() in _SPECIAL
    rest = rest.replace("\\", "/") if special else rest
    authority = re.split(r"[/?#]", rest.lstrip("/"), maxsplit=1)[0].rpartition("@")[2]
    if authority.startswith("["):
        _, _, after = authority.partition("]")
        if after and not (after.startswith(":") and _port_ok(after[1:])):
            return "invalid port number"
        return "invalid IPv6 address"
    host, _, port = authority.partition(":")
    if not _port_ok(port):
        return "invalid port number"
    if not host and special:
        return "empty host"
    last = host.rstrip(".").rpartition(".")[2]
    if special and last and (last.isdigit() or last.lower().startswith("0x")):
        return "invalid IPv4 address"
    # Special hosts go through IDNA, which rejects forbidden characters too.
    return "invalid international domain name" if special else "invalid domain character"


def parse_uri(value: str, base: str | None) -> str | None:
    """Absolute as is, relative joined to `base`, else None."""
    try:
        if has_scheme(value):
            return validators.whatwg(value).href
        return validators.whatwg(value, base).href if base is not None else None
    except ValueError:
        return None


class Element:
    """An element start, read lazily from its `Source`."""

    def __init__(
        self, source: Source, start: Start, ns: str, base: str | None, depth: int
    ) -> None:
        self.source = source
        self.name = start.name
        self.attrs = start.attrs
        self.ns = ns
        self.base = base
        self.depth = depth
        self._end = start.end

    @property
    def pos(self) -> int:
        """UTF-8 bytes read when the start tag ended."""
        return len(self.source.text[: self._end].encode("utf-8", "surrogatepass"))

    def tag(self) -> tuple[str, str]:
        return self.ns, self.name

    def attr(self, name: str) -> str | None:
        return next((value for key, value in self.attrs if key == name), None)

    def text(self) -> str | None:
        """The text directly after the start tag, if the next event is text."""
        return self.source.text_node()

    def text_sloppy(self) -> str:
        """That text, or else the children re-serialized."""
        found = self.source.text_node()
        return found if found is not None else self.source.children_markup(self.depth + 1)

    def xhtml(self) -> str:
        return self.source.children_markup(self.depth + 1, xhtml=True)

    def children(self) -> Iterator[Element]:
        while (child := self.source.next_element(self.depth + 1)) is not None:
            yield child


def _escape(text: str) -> str:
    """Escape `&`, `<` and carriage returns."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace("\r", "&#13;")


def _start_markup(start: Start) -> str:
    return "<" + start.name + "".join(f' {k}="{v}"' for k, v in start.attrs) + ">"


class Source:
    """One event of lookahead over `events(text)`."""

    def __init__(self, text: str, base: str | None = None, tail: str | None = None) -> None:
        self.text = text
        self._events = events(text, tail)
        self.depth = 0
        self.bases: list[tuple[int, str]] = [] if base is None else [(0, base)]
        self.default_ns = UNKNOWN
        self._next = self._fetch()

    def _fetch(self) -> Event | XmlError | None:
        try:
            return next(self._events)
        except StopIteration:
            return None
        except XmlError as error:
            return error

    def _advance(self) -> Event | None:
        current = self._next
        self._next = self._fetch()
        if isinstance(current, XmlError):
            raise current
        return current

    def set_default_ns(self, ns: str) -> None:
        if self.default_ns == UNKNOWN:
            self.default_ns = ns

    def root(self) -> Element | None:
        try:
            return self.next_element(1)
        except XmlError:
            return None

    def _push_base(self, attrs: list[tuple[str, str]]) -> None:
        value = next((v for k, v in attrs if k == "xml:base"), None)
        if value is None:
            return
        if has_scheme(value):
            try:
                self.bases.append((self.depth, validators.whatwg(value).href))
            except ValueError:
                raise XmlError(f"Url error: {url_error(value)}") from None
        elif self.bases:
            try:
                self.bases.append((self.depth, validators.whatwg(value, self.bases[-1][1]).href))
            except ValueError:
                pass

    def next_element(self, depth: int) -> Element | None:
        while (event := self._advance()) is not None:
            if isinstance(event, Start):
                self.depth += 1
                self._push_base(event.attrs)
                if self.depth == depth:
                    base = self.bases[-1][1] if self.bases else None
                    known = None if event.ns is None else _NAMESPACES.get(event.ns, UNKNOWN)
                    ns = self.default_ns if known is None else known
                    return Element(self, event, ns, base, self.depth)
            elif isinstance(event, End):
                self.depth -= 1
                while self.bases and self.bases[-1][0] > self.depth:
                    self.bases.pop()
            if self.depth < depth - 1:
                return None
        if self.depth > 0:
            raise XmlError(UNCLOSED_TAG)
        return None

    def text_node(self) -> str | None:
        found: list[str] = []
        while isinstance(self._next, Text):
            found.append(self._next.text)
            self._advance()
        return "".join(found) if found else None

    def children_markup(self, depth: int, xhtml: bool = False) -> str:
        """The subtree as markup (local names, attribute values unescaped); with
        `xhtml`, a lone root `div` is removed."""
        out: list[str] = []
        current = depth
        first, div_open, div_close = True, None, None
        while True:
            event = self._next
            if isinstance(event, XmlError):
                raise event
            if event is None:
                break
            if isinstance(event, Start):
                if xhtml and current == depth:
                    if not first:
                        div_open = div_close = None
                    elif event.name != "div":
                        first = False
                current += 1
                out.append(_start_markup(event))
                if xhtml and first:
                    first = False
                    div_open = len(out)
            elif isinstance(event, Text):
                if xhtml and current == depth and event.text.strip(" \t\n\x0c\r"):
                    first, div_open, div_close = False, None, None
                out.append(_escape(event.text))
            else:
                current -= 1
                if current < depth:
                    break
                if xhtml and current == depth and div_open is not None:
                    div_close = len(out)
                out.append(f"</{event.name}>")
            self._advance()
        if div_open is not None and div_close is not None:
            out = out[div_open:div_close]
        return "".join(out)
