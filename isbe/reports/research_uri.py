"""URI grammar for research source URLs, modelled on the `http` crate's `Uri` parser (1.5.0).

A manifest URL must parse as a URI and then have an http(s) scheme.
`validators.http_uri` models only absolute URIs; this parser also accepts path
(`/x`), `*` and bare-authority (`example.com`) forms, which fail the scheme
check rather than the parse, and it names the reason a parse failed. Both
decide the message the operator sees, so the grammar is here.
"""

_URI_CHARS = frozenset(
    b"!#$&'()*+,-./0123456789:;=?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[]_abcdefghijklmnopqrstuvwxyz~"
)
_SCHEME_CHARS = frozenset(b"+-.0123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz~")
_PATH = frozenset(
    {0x21, *range(0x24, 0x3C), 0x3D, *range(0x40, 0x60), *range(0x61, 0x7B), 0x7C, 0x7E}
    | set(b'"{}')
    | set(range(0x80, 0x100))
)
_QUERY = frozenset({0x21, *range(0x24, 0x3C), 0x3D, *range(0x3F, 0x7F), *range(0x80, 0x100)})
_MAX_LEN = 0xFFFE
_MAX_SCHEME_LEN = 64


class UriError(ValueError):
    """A URI that does not parse; the message names the reason."""


def _authority_end(s: bytes) -> int:
    """Where the authority ends (the first `/`, `?` or `#`)."""
    if not s:
        raise UriError("empty string")
    colons, start_bracket, end_bracket, percent = 0, False, False, False
    end = at = len(s)
    for i, b in enumerate(s):
        if b in b"/?#":
            end = i
            break
        if b not in _URI_CHARS:
            if b != ord("%"):
                raise UriError("invalid uri character")
            # A `%` is only allowed in userinfo or an IPv6 zone; cleared below if so.
            percent = True
        elif b == ord(":"):
            if colons >= 8:
                raise UriError("invalid authority")
            colons += 1
        elif b == ord("["):
            if percent or start_bracket:
                raise UriError("invalid authority")
            start_bracket = True
        elif b == ord("]"):
            if not start_bracket or end_bracket:
                raise UriError("invalid authority")
            end_bracket, colons, percent = True, 0, False
        elif b == ord("@"):
            at, colons, percent = i, 0, False
    if start_bracket != end_bracket or colons > 1 or (end > 0 and at == end - 1) or percent:
        raise UriError("invalid authority")
    return end


def _path_and_query(s: bytes) -> None:
    """Scan the path and query; a fragment ends the scan."""
    if not s:
        raise UriError("empty string")
    if len(s) > _MAX_LEN:
        raise UriError("uri too long")
    if s == b"*":
        return
    if s[0] not in b"/?#":
        raise UriError("path does not start with slash")
    query = None
    for i, b in enumerate(s):
        if b == ord("?"):
            query = i + 1
            break
        if b == ord("#"):
            return
        if b not in _PATH:
            raise UriError("invalid uri character")
    if query is not None:
        for b in s[query:]:
            if b == ord("#"):
                return
            if b not in _QUERY:
                raise UriError("invalid uri character")


def _scheme(s: bytes) -> tuple[str | None, bytes]:
    """`Scheme2::parse`: the scheme, if any, and the rest after its `://`."""
    if s[:7].lower() == b"http://":
        return "http", s[7:]
    if s[:8].lower() == b"https://":
        return "https", s[8:]
    if len(s) > 3:
        for i, b in enumerate(s):
            if b not in _SCHEME_CHARS:
                break
            if b == ord(":"):
                if len(s) < i + 3 or s[i + 1 : i + 3] != b"//":
                    break
                if i > _MAX_SCHEME_LEN:
                    raise UriError("scheme too long")
                return s[:i].decode(), s[i + 3 :]
    return None, s


def scheme(value: str) -> str | None:
    """The scheme of the parsed URI: None for a path or authority form.

    Raises `UriError` with the reason when it does not parse.
    """
    s = value.encode()
    if len(s) > _MAX_LEN:
        raise UriError("uri too long")
    if not s:
        raise UriError("empty string")
    if len(s) == 1:
        if s in (b"/", b"*"):
            return None
        if _authority_end(s) != 1:
            raise UriError("invalid uri character")
        return None
    if s.startswith(b"/"):
        _path_and_query(s)
        return None
    name, rest = _scheme(s)
    end = _authority_end(rest)
    if name is None:
        if end != len(rest):
            raise UriError("invalid format")
        return None
    if end == 0:
        raise UriError("invalid format")
    if rest[end:]:
        _path_and_query(rest[end:])
    return name
