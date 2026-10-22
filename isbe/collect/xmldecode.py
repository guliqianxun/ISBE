"""Bytes of an XML feed to text, choosing the encoding as the earlier Rust build
did, plus a fallback for feeds that declare none.

A BOM or a UTF-16 byte pattern is read first, then the encoding named by the
XML declaration (WHATWG labels, so `gb2312` means GBK), and otherwise UTF-8;
invalid input is an error, never replaced. The whole document is decoded with
the chosen encoding, including GBK text within the first 64 bytes. A document
without a usable declaration that is not valid UTF-8 is decoded with the
`charset` of its Content-Type, so a GBK feed declared only by its server
works. A declared or valid-UTF-8 document decodes exactly as before.
"""

import codecs
import re

from isbe.core import validators

from .xmltokens import leading

# Chunk bounds for error text: 64 bytes, then 8 KiB, then 8 KiB at a time; an
# undecodable byte fails its whole chunk.
_PREFIX, _CHUNK = 64, 8192


class DecodeError(ValueError):
    """Undecodable input. `text` is what was decoded before the chunk
    holding the bad byte (UTF-8 chunk bounds; other encodings approximately)."""

    def __init__(self, message: str, text: str = "") -> None:
        super().__init__(message)
        self.text = text


def _chunk_start(offset: int) -> int:
    if offset < _PREFIX:
        return 0
    return _PREFIX if offset < _CHUNK else offset // _CHUNK * _CHUNK


# WHATWG encoding labels → (canonical name for messages, Python codec).
_GROUPS: tuple[tuple[str, str, str], ...] = (
    ("UTF-8", "utf-8", "unicode-1-1-utf-8 unicode11utf8 unicode20utf8 utf-8 utf8 x-unicode20utf8"),
    ("IBM866", "cp866", "866 cp866 csibm866 ibm866"),
    ("ISO-8859-2", "iso8859_2",
        "csisolatin2 iso-8859-2 iso-ir-101 iso8859-2 iso88592 iso_8859-2 iso_8859-2:1987 "
        "l2 latin2",
    ),
    ("ISO-8859-3", "iso8859_3",
        "csisolatin3 iso-8859-3 iso-ir-109 iso8859-3 iso88593 iso_8859-3 iso_8859-3:1988 "
        "l3 latin3",
    ),
    ("ISO-8859-4", "iso8859_4",
        "csisolatin4 iso-8859-4 iso-ir-110 iso8859-4 iso88594 iso_8859-4 iso_8859-4:1988 "
        "l4 latin4",
    ),
    ("ISO-8859-5", "iso8859_5",
        "csisolatincyrillic cyrillic iso-8859-5 iso-ir-144 iso8859-5 iso88595 iso_8859-5 "
        "iso_8859-5:1988",
    ),
    ("ISO-8859-6", "iso8859_6",
        "arabic asmo-708 csiso88596e csiso88596i csisolatinarabic ecma-114 iso-8859-6 "
        "iso-8859-6-e iso-8859-6-i iso-ir-127 iso8859-6 iso88596 iso_8859-6 "
        "iso_8859-6:1987",
    ),
    ("ISO-8859-7", "iso8859_7",
        "csisolatingreek ecma-118 elot_928 greek greek8 iso-8859-7 iso-ir-126 iso8859-7 "
        "iso88597 iso_8859-7 iso_8859-7:1987 sun_eu_greek",
    ),
    ("ISO-8859-8", "iso8859_8",
        "csiso88598e csisolatinhebrew hebrew iso-8859-8 iso-8859-8-e iso-ir-138 iso8859-8 "
        "iso88598 iso_8859-8 iso_8859-8:1988 visual",
    ),
    ("ISO-8859-8-I", "iso8859_8", "csiso88598i iso-8859-8-i logical"),
    ("ISO-8859-10", "iso8859_10",
        "csisolatin6 iso-8859-10 iso-ir-157 iso8859-10 iso885910 l6 latin6",
    ),
    ("ISO-8859-13", "iso8859_13", "iso-8859-13 iso8859-13 iso885913"),
    ("ISO-8859-14", "iso8859_14", "iso-8859-14 iso8859-14 iso885914"),
    ("ISO-8859-15", "iso8859_15", "csisolatin9 iso-8859-15 iso8859-15 iso885915 iso_8859-15 l9"),
    ("ISO-8859-16", "iso8859_16", "iso-8859-16"),
    ("KOI8-R", "koi8_r", "cskoi8r koi koi8 koi8-r koi8_r"),
    ("KOI8-U", "koi8_u", "koi8-ru koi8-u"),
    ("macintosh", "mac_roman", "csmacintosh mac macintosh x-mac-roman"),
    ("windows-874", "cp874", "dos-874 iso-8859-11 iso8859-11 iso885911 tis-620 windows-874"),
    ("windows-1250", "cp1250", "cp1250 windows-1250 x-cp1250"),
    ("windows-1251", "cp1251", "cp1251 windows-1251 x-cp1251"),
    ("windows-1252", "cp1252",
        "ansi_x3.4-1968 ascii cp1252 cp819 csisolatin1 ibm819 iso-8859-1 iso-ir-100 "
        "iso8859-1 iso88591 iso_8859-1 iso_8859-1:1987 l1 latin1 us-ascii windows-1252 "
        "x-cp1252",
    ),
    ("windows-1253", "cp1253", "cp1253 windows-1253 x-cp1253"),
    ("windows-1254", "cp1254",
        "cp1254 csisolatin5 iso-8859-9 iso-ir-148 iso8859-9 iso88599 iso_8859-9 "
        "iso_8859-9:1989 l5 latin5 windows-1254 x-cp1254",
    ),
    ("windows-1255", "cp1255", "cp1255 windows-1255 x-cp1255"),
    ("windows-1256", "cp1256", "cp1256 windows-1256 x-cp1256"),
    ("windows-1257", "cp1257", "cp1257 windows-1257 x-cp1257"),
    ("windows-1258", "cp1258", "cp1258 windows-1258 x-cp1258"),
    ("x-mac-cyrillic", "mac_cyrillic", "x-mac-cyrillic x-mac-ukrainian"),
    # WHATWG's GBK decoder is the gb18030 decoder.
    ("GBK", "gb18030",
        "chinese csgb2312 csiso58gb231280 gb2312 gb_2312 gb_2312-80 gbk iso-ir-58 x-gbk",
    ),
    ("gb18030", "gb18030", "gb18030"),
    ("Big5", "big5hkscs", "big5 big5-hkscs cn-big5 csbig5 x-x-big5"),
    ("EUC-JP", "euc_jp", "cseucpkdfmtjapanese euc-jp x-euc-jp"),
    ("ISO-2022-JP", "iso2022_jp", "csiso2022jp iso-2022-jp"),
    ("Shift_JIS", "cp932", "csshiftjis ms932 ms_kanji shift-jis shift_jis sjis windows-31j x-sjis"),
    ("EUC-KR", "cp949",
        "cseuckr csksc56011987 euc-kr iso-ir-149 korean ks_c_5601-1987 ks_c_5601-1989 "
        "ksc5601 ksc_5601 windows-949",
    ),
    ("UTF-16BE", "utf-16-be", "unicodefffe utf-16be"),
    ("UTF-16LE", "utf-16-le",
        "csunicode iso-10646-ucs-2 ucs-2 unicode unicodefeff utf-16 utf-16le",
    ),
)
LABELS: dict[str, tuple[str, str]] = {
    label: (name, codec) for name, codec, labels in _GROUPS for label in labels.split()
}

# windows-1252 as WHATWG decodes it: the five bytes cp1252 leaves undefined map
# to the C1 controls of the same value instead of failing.
_CP1252 = {
    i: ord(bytes([i]).decode("cp1252"))
    for i in range(0x80, 0xA0)
    if i not in (0x81, 0x8D, 0x8F, 0x90, 0x9D)
}

_DECL = re.compile(rb"[^<]*<\?xml(?=[ \t\r\n?])")
_CHARSET = re.compile(r"""charset[ \t]*=[ \t]*["']?([^"';, \t]+)""", re.IGNORECASE)


def lookup(label: str) -> tuple[str, str] | None:
    """`Encoding::for_label`: ASCII whitespace trimmed, ASCII case ignored."""
    return LABELS.get(validators.ascii_lower(label.strip("\t\n\x0c\r ")))


def declared(raw: bytes) -> str | None:
    """The `encoding` of the XML declaration: the first markup (text may
    precede it), ending at the first `?>`, with an attribute in error before
    `encoding` hiding it. It is honoured wherever it is in the document."""
    decl = _DECL.match(raw)
    if decl is None:
        return None
    start = decl.end() - 3  # at `xml`
    end = raw.find(b"?>", start)
    if end == -1:
        return None
    content = raw[start:end].decode("latin-1")
    return next((value for key, value in leading(content, 3) if key == "encoding"), None)


def charset(content_type: str | None) -> str | None:
    """The `charset` parameter of a Content-Type header."""
    found = _CHARSET.search(content_type or "")
    return found.group(1) if found else None


def _decode(raw: bytes, name: str, codec: str, skip: int = 0) -> str:
    """`raw[skip:]` decoded; `skip` is the BOM, which counts towards the chunks."""
    if codec == "cp1252":
        return raw[skip:].decode("latin-1").translate(_CP1252)
    try:
        return raw[skip:].decode(codec)
    except UnicodeDecodeError as error:
        if error.end == len(raw) - skip and error.reason in ("unexpected end of data", "truncated data"):
            # An unfinished character at the very end fails only when input ends.
            start = len(raw)
        else:
            # The byte that showed the sequence was bad decides the failing chunk.
            late = ("invalid continuation byte", "illegal UTF-16 surrogate")
            seen = error.end if error.reason in late else error.start
            start = max(_chunk_start(skip + seen), skip)
        # Characters split by the chunk boundary had not been produced yet.
        text = codecs.getincrementaldecoder(codec)().decode(raw[skip:start], final=False)
        raise DecodeError(f"cannot decode input using {name}", text) from None


def decode(raw: bytes, content_type: str | None = None) -> str:
    """The document as text, BOM removed; raises `DecodeError`."""
    if raw.startswith(b"\xfe\xff"):
        return _decode(raw, "UTF-16BE", "utf-16-be", 2)
    if raw.startswith(b"\xff\xfe"):
        return _decode(raw, "UTF-16LE", "utf-16-le", 2)
    if raw.startswith(b"\xef\xbb\xbf"):
        label = declared(raw[3:])
        found = lookup(label) if label is not None else None
        if found is not None and not found[1].startswith("utf-16"):
            return _decode(raw, *found, 3)
        return _decode(raw, "UTF-8", "utf-8", 3)
    if raw.startswith(b"\x00<\x00?"):
        return _decode(raw, "UTF-16BE", "utf-16-be")
    if raw.startswith(b"<\x00?\x00"):
        return _decode(raw, "UTF-16LE", "utf-16-le")
    label = declared(raw)
    found = lookup(label) if label is not None else None
    # A UTF-16 label in an ASCII-compatible document cannot be right: the
    # declaration could not have been read otherwise.
    if found is not None and not found[1].startswith("utf-16"):
        return _decode(raw, *found)
    try:
        return _decode(raw, "UTF-8", "utf-8")
    except DecodeError as error:
        header = charset(content_type)
        fallback = lookup(header) if header is not None else None
        if fallback is None or fallback[1].startswith("utf-16"):
            raise
        try:
            return _decode(raw, *fallback)
        except DecodeError:
            raise error from None
