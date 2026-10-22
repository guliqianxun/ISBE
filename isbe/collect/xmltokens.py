"""The XML tokenizer behind feed parsing. It accepts and rejects what the parser
of the earlier Rust build did.

Only the five predefined entities and character references are resolved
(unknown entities are kept as written: `&nbsp;` stays `&nbsp;`); a lone `&` and
mismatched or unmatched end tags are rejected; the end of a tag is found
outside quoted attribute values; and text, CDATA and references between two
tags join into one text event (comments, processing instructions and DOCTYPEs
do not split it). Error messages carry a "Parser error: " prefix, because they
reach the user in a collect or source error.

The tokenizer is lazy: damage after the last element the feed reader needs is
never seen.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass

_PREDEFINED = {"lt": "<", "gt": ">", "amp": "&", "apos": "'", "quot": '"'}
_WS = " \t\r\n"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_XMLNS_NS = "http://www.w3.org/2000/xmlns/"
_MAX_BINDINGS = 128
_MAX_DEPTH = 65535

_P = "Parser error: "
_SYNTAX = _P + "syntax error: "
_ILL = _P + "ill-formed document: "
UNCLOSED_TAG = _SYNTAX + "tag not closed: `>` not found before end of input"
_UNCLOSED = {
    "'": _SYNTAX + "attribute value not closed: `'` not found before end of input",
    '"': _SYNTAX + 'attribute value not closed: `"` not found before end of input',
    "comment": _SYNTAX + "comment not closed: `-->` not found before end of input",
    "cdata": _SYNTAX + "CDATA not closed: `]]>` not found before end of input",
    "doctype": _SYNTAX + "DOCTYPE not closed: `>` not found before end of input",
    "pi": _SYNTAX + "processing instruction not closed: `?>` not found before end of input",
    "decl": _SYNTAX + "XML declaration not closed: `?>` not found before end of input",
}
_BANG = _SYNTAX + "unknown or missed symbol in markup"
_UNCLOSED_REF = (
    _ILL + "entity or character reference not closed: `;` not found before end of input"
)
_NO_DOCTYPE_NAME = (
    _ILL + "`<!DOCTYPE>` declaration does not contain a name of a document type"
)


class XmlError(ValueError):
    pass


@dataclass(slots=True)
class Start:
    name: str  # local name: what follows the last `:`
    attrs: list[tuple[str, str]]
    ns: str | None  # the bound namespace URI; None: unbound or an unknown prefix
    end: int  # index just after the tag, for error positions


@dataclass(slots=True)
class End:
    name: str


@dataclass(slots=True)
class Text:
    text: str


type Event = Start | End | Text


def _number(digits: str, radix: int) -> int:
    """Parse digits in `radix`; a sign is refused."""
    if digits[:1] in ("+", "-"):
        raise ValueError("unexpected number sign")
    if not digits:
        raise ValueError("cannot parse integer from empty string")
    allowed = "0123456789abcdefABCDEF" if radix == 16 else "0123456789"
    if any(c not in allowed for c in digits):
        raise ValueError("invalid digit found in string")
    value = int(digits, radix)
    if value > 0xFFFFFFFF:
        raise ValueError("number too large to fit in target type")
    return value


def char_ref(body: str) -> str:
    """`&#...;` (`body` without `&#` and `;`) resolved to its character."""
    try:
        code = _number(body[1:], 16) if body.startswith("x") else _number(body, 10)
        if code == 0:
            raise ValueError("0x0 character is not permitted in XML")
        if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
            raise ValueError(f"`{code}` is not a valid codepoint")
    except ValueError as error:
        raise XmlError(f"{_P}invalid character reference: {error}") from None
    return chr(code)


def unescape(value: str) -> str | None:
    """`quick_xml::escape::unescape`, or None where it fails."""
    out, i = [], 0
    while (amp := value.find("&", i)) != -1:
        end = value.find(";", amp)
        if end == -1:
            return None
        body = value[amp + 1 : end]
        out.append(value[i:amp])
        if body.startswith("#"):
            try:
                out.append(char_ref(body[1:]))
            except XmlError:
                return None
        elif body in _PREDEFINED:
            out.append(_PREDEFINED[body])
        else:
            return None
        i = end + 1
    out.append(value[i:])
    return "".join(out)


def _skip_to_space(s: str, i: int) -> int | None:
    while i < len(s) and s[i] not in _WS:
        i += 1
    return i if i < len(s) else None


def attribute_items(
    content: str, start: int, checks: bool = True
) -> Iterator[tuple[str, str] | None]:
    """Attributes as (key, raw value), or None for an attribute in error; the
    resume point after an error is fixed by the tokenizer's rules."""
    n = len(content)
    keys: set[str] = set()
    at: int | None = start
    while at is not None:
        i = at
        while i < n and content[i] in _WS:
            i += 1
        if i >= n:
            return
        key_start = i
        i += 1  # the key's first character is never its end, even if it is `=`
        while i < n and content[i] != "=" and content[i] not in _WS:
            i += 1
        key_end = i
        if i < n and content[i] in _WS:
            while i < n and content[i] in _WS:
                i += 1
            if i < n and content[i] != "=":
                yield None  # a key without a value; the next key starts here
                at = i
                continue
        if i >= n:
            yield None
            return
        key = content[key_start:key_end]
        if checks:
            if key in keys:
                yield None  # duplicated: resume at the first space after the `=`
                at = _skip_to_space(content, i)
                continue
            keys.add(key)
        i += 1
        while i < n and content[i] in _WS:
            i += 1
        if i >= n:
            yield None
            return
        quote = content[i]
        if quote not in "\"'":
            yield None  # unquoted value: skipped up to the next space
            at = _skip_to_space(content, i)
            continue
        close = content.find(quote, i + 1)
        if close == -1:
            yield None
            return
        yield key, content[i + 1 : close]
        at = close + 1


def attributes(content: str, start: int) -> list[tuple[str, str, str]]:
    """The attributes kept, as (key, unescaped value, raw value): checked,
    the ones in error dropped, a value that does not unescape kept raw."""
    found = []
    for item in attribute_items(content, start):
        if item is not None:
            key, raw = item
            value = unescape(raw)
            found.append((key, raw if value is None else value, raw))
    return found


def leading(content: str, start: int) -> Iterator[tuple[str, str]]:
    """Unchecked attributes up to the first in error, as namespace bindings
    and the declared encoding are read."""
    for item in attribute_items(content, start, checks=False):
        if item is None:
            return
        yield item


_QUOTE_OR_GT = re.compile(r"""[>"']""")
_DTD_BEFORE = re.compile(r"""['"\[>]""")
_DTD_INSIDE = re.compile(r"[\]<]")


def _tag_end(text: str, start: int, tail: str | None) -> int:
    """Index of the `>` ending a tag whose content starts at `start` (`ElementParser`)."""
    quote = None
    i = start
    while True:
        if quote is None:
            found = _QUOTE_OR_GT.search(text, i)
            if found is None:
                raise XmlError(tail or UNCLOSED_TAG)
            i = found.start()
            if text[i] == ">":
                return i
            quote = text[i]
        else:
            i = text.find(quote, i)
            if i == -1:
                raise XmlError(tail or _UNCLOSED[quote])
            quote = None
        i += 1


def _doctype_end(text: str, i: int) -> int | None:
    """Index of the `>` closing a DOCTYPE whose name starts at `i` (`DtdParser`)."""
    n, state = len(text), "before"
    while i < n:
        if state == "before":
            found = _DTD_BEFORE.search(text, i)
            if found is None:
                return None
            c, i = found.group(), found.start()
            if c == ">":
                return i
            if c == "[":
                state, i = "inside", i + 1
                continue
            close = text.find(c, i + 1)
            if close == -1:
                return None
            i = close + 1
        elif state == "after":
            found_gt = text.find(">", i)
            return None if found_gt == -1 else found_gt
        else:
            found = _DTD_INSIDE.search(text, i)
            if found is None:
                return None
            i = found.start()
            if text[i] == "]":
                state, i = "after", i + 1
                continue
            markup = i + 1
            if text.startswith("?", markup):
                close = text.find("?>", markup + 1)
                if close == -1:
                    return None
                i = close + 1
            elif text.startswith("!--", markup):
                close = text.find("-->", markup + 3)
                if close == -1:
                    return None
                i = close + 3
            elif text.startswith(("!ENTITY", "!ATTLIST", "!NOTATION"), markup):
                try:
                    i = _tag_end(text, markup, None)
                except XmlError:
                    return None
            else:
                close = text.find(">", markup)
                if close == -1:
                    return None
                i = close + 1
    return None


def _bang(text: str, i: int, tail: str | None) -> tuple[str, int]:
    """A `<!...>` at `i`: (`comment` | `cdata` | `doctype`, index after it)."""
    n = len(text)
    kind = {"[": "cdata", "-": "comment", "D": "doctype", "d": "doctype"}.get(text[i + 2 : i + 3])
    if kind is None:
        raise XmlError(tail if tail is not None and i + 2 >= n else _BANG)
    end: int | None = None
    if kind == "doctype":
        end = _doctype_end(text, i + 2)
    else:
        close = "--" if kind == "comment" else "]]"
        j = i + 2
        while (j := text.find(">", j)) != -1:
            if text.endswith(close, i + 2, j) and (kind == "cdata" or j - i > 5):
                end = j
                break
            j += 1
    if end is None:
        raise XmlError(tail or _UNCLOSED[kind])
    token = text[i : end + 1]
    if kind == "comment" and not token.startswith("<!--"):
        raise XmlError(_UNCLOSED["comment"])
    if kind == "cdata" and not token.startswith("<![CDATA["):
        raise XmlError(_UNCLOSED["cdata"])
    if kind == "doctype":
        if token[:9].upper() != "<!DOCTYPE":
            raise XmlError(_UNCLOSED["doctype"])
        if not token[9:-1].strip(_WS):
            raise XmlError(_NO_DOCTYPE_NAME)
    return kind, end + 1


def _pi_error(token: str) -> str:
    decl = token.startswith("<?xml") and (len(token) == 5 or token[5] in _WS + "?")
    return _UNCLOSED["decl" if decl else "pi"]


def _bind(scope: list[tuple[str, str]], content: str, start: int, bound: int) -> None:
    """Namespace bindings and their checks, on raw attribute values."""
    for key, raw in leading(content, start):
        if key == "xmlns":
            prefix = ""
        elif key.startswith("xmlns:"):
            prefix = key[6:]
        else:
            continue
        if prefix == "xml":
            if raw != _XML_NS:
                raise XmlError(f"{_P}the namespace prefix 'xml' cannot be bound to '{raw}'")
            continue
        if prefix == "xmlns":
            raise XmlError(f"{_P}the namespace prefix 'xmlns' cannot be bound to '{raw}'")
        if prefix and raw in (_XML_NS, _XMLNS_NS):
            raise XmlError(f"{_P}the namespace prefix '{prefix}' cannot be bound to '{raw}'")
        if bound + len(scope) >= _MAX_BINDINGS:
            raise XmlError(
                f"{_P}more than {_MAX_BINDINGS} namespace bindings in scope; "
                "raise the limit with NamespaceResolver::set_max_namespace_bindings"
            )
        scope.append((prefix, raw))


def _resolve(qname: str, scopes: list[list[tuple[str, str]]]) -> str | None:
    prefix, colon, _ = qname.partition(":")
    if colon and prefix == "xml":
        return _XML_NS
    if colon and prefix == "xmlns":
        return _XMLNS_NS
    key = prefix if colon else ""
    for scope in reversed(scopes):
        for bound_prefix, uri in reversed(scope):
            if bound_prefix == key:
                return uri or None
    return None


def events(text: str, tail: str | None = None) -> Iterator[Event]:
    """Start/End/Text events of `text`; raises XmlError where parsing fails.

    `tail` is the error met reading past the end of `text` (bytes that could
    not be decoded); without it, the end of `text` is the end of the input.
    """
    n = len(text)
    i = 0
    pending: list[str] = []
    open_names: list[str] = []
    scopes: list[list[tuple[str, str]]] = []
    bound = 0

    def flush() -> Iterator[Event]:
        joined = "".join(pending)
        pending.clear()
        if joined:
            yield Text(joined)

    while True:
        lt = text.find("<", i)
        stop = n if lt == -1 else lt
        while i < stop:
            amp = text.find("&", i, stop)
            if amp == -1:
                pending.append(text[i:stop])
                break
            pending.append(text[i:amp])
            semi = amp + 1
            while semi < n and text[semi] not in ";&<":
                semi += 1
            if semi >= n and tail is not None:
                raise XmlError(tail)
            if semi >= n or text[semi] != ";":
                raise XmlError(_UNCLOSED_REF)
            body = text[amp + 1 : semi]
            if body.startswith("#"):
                pending.append(char_ref(body[1:]))
            else:
                pending.append(_PREDEFINED.get(body, f"&{body};"))
            i = semi + 1
        if lt == -1:
            if tail is not None:
                raise XmlError(tail)
            break
        i = lt
        if i + 1 >= n:
            raise XmlError(tail or UNCLOSED_TAG)
        mark = text[i + 1]
        if mark == "!":
            kind, i = _bang(text, i, tail)
            if kind == "cdata":
                pending.append(text[lt + 9 : i - 3])
        elif mark == "?":
            close = text.find("?>", i + 1)
            if close == -1:
                raise XmlError(tail or _pi_error(text[i:]))
            if close == i + 1:  # `<?>`
                raise XmlError(_pi_error(text[i : close + 2]))
            i = close + 2
        elif mark == "/":
            end = _tag_end(text, i + 1, tail)
            content = text[i + 2 : end]
            name = content.rstrip(_WS) or content
            if not open_names:
                raise XmlError(f"{_ILL}close tag `</{name}>` does not match any open tag")
            expected = open_names.pop()
            bound -= len(scopes.pop())
            if expected != name:
                raise XmlError(f"{_ILL}expected `</{expected}>`, but `</{name}>` was found")
            yield from flush()
            yield End(name.split(":")[-1])
            i = end + 1
        else:
            end = _tag_end(text, i + 1, tail)
            content = text[i + 1 : end]
            empty = content.endswith("/")
            if empty:
                content = content[:-1]
            length = 0
            while length < len(content) and content[length] not in _WS:
                length += 1
            qname = content[:length]
            attrs = attributes(content, length)
            if len(scopes) >= _MAX_DEPTH:
                raise XmlError(
                    f"{_P}document nests elements deeper than the supported limit of {_MAX_DEPTH}"
                )
            scope: list[tuple[str, str]] = []
            _bind(scope, content, length, bound)
            scopes.append(scope)
            bound += len(scope)
            yield from flush()
            local = qname.split(":")[-1]
            yield Start(local, [(k, v) for k, v, _ in attrs], _resolve(qname, scopes), end + 1)
            if empty:
                bound -= len(scopes.pop())
                yield End(local)
            else:
                open_names.append(qname)
            i = end + 1
    yield from flush()
