"""Original-page bytes to text, as `article_date::decode_html` does with encoding_rs.

The charset comes from the `Content-Type` header, else from a `<meta>` in the
head of the first 8 KiB; without one the page must be UTF-8. Labels resolve
through the WHATWG Encoding Standard table (encoding_rs `for_label`), never
through Python's broader codec aliases, and decoding is strict: one invalid
sequence rejects the page instead of guessing or inserting U+FFFD.

Python's codecs differ from the WHATWG decoders in a few places, measured
against encoding_rs 0.8.42 over every one- and two-byte sequence (and every
four-byte GB18030 sequence): windows-125x/874 map their unassigned bytes
0x80–0x9F to C1 controls, KOI8-U is KOI8-RU, GBK/GB18030 read a lone 0x80 as
€ and use the GB18030-2022 mappings, Big5 includes the HKSCS-2008 additions and
compatibility mappings, and Shift_JIS rejects the single bytes 0xA0 and 0xFD–0xFF. Those
are corrected below; EUC-JP and ISO-2022-JP use Python's codecs as they are.
"""

import re
from functools import cache

from isbe.articles import dom
from isbe.core.validators import ascii_lower, trim

_ENCODINGS = {
    "UTF-8": "unicode-1-1-utf-8 unicode11utf8 unicode20utf8 utf-8 utf8 x-unicode20utf8",
    "IBM866": "866 cp866 csibm866 ibm866",
    "ISO-8859-2": "csisolatin2 iso-8859-2 iso-ir-101 iso8859-2 iso88592 iso_8859-2 iso_8859-2:1987 l2 latin2",
    "ISO-8859-3": "csisolatin3 iso-8859-3 iso-ir-109 iso8859-3 iso88593 iso_8859-3 iso_8859-3:1988 l3 latin3",
    "ISO-8859-4": "csisolatin4 iso-8859-4 iso-ir-110 iso8859-4 iso88594 iso_8859-4 iso_8859-4:1988 l4 latin4",
    "ISO-8859-5": "csisolatincyrillic cyrillic iso-8859-5 iso-ir-144 iso8859-5 iso88595 iso_8859-5 iso_8859-5:1988",
    "ISO-8859-6": "arabic asmo-708 csiso88596e csiso88596i csisolatinarabic ecma-114 iso-8859-6 iso-8859-6-e "
    "iso-8859-6-i iso-ir-127 iso8859-6 iso88596 iso_8859-6 iso_8859-6:1987",
    "ISO-8859-7": "csisolatingreek ecma-118 elot_928 greek greek8 iso-8859-7 iso-ir-126 iso8859-7 iso88597 "
    "iso_8859-7 iso_8859-7:1987 sun_eu_greek",
    "ISO-8859-8": "csiso88598e csisolatinhebrew hebrew iso-8859-8 iso-8859-8-e iso-ir-138 iso8859-8 iso88598 "
    "iso_8859-8 iso_8859-8:1988 visual",
    "ISO-8859-8-I": "csiso88598i iso-8859-8-i logical",
    "ISO-8859-10": "csisolatin6 iso-8859-10 iso-ir-157 iso8859-10 iso885910 l6 latin6",
    "ISO-8859-13": "iso-8859-13 iso8859-13 iso885913",
    "ISO-8859-14": "iso-8859-14 iso8859-14 iso885914",
    "ISO-8859-15": "csisolatin9 iso-8859-15 iso8859-15 iso885915 iso_8859-15 l9",
    "ISO-8859-16": "iso-8859-16",
    "KOI8-R": "cskoi8r koi koi8 koi8-r koi8_r",
    "KOI8-U": "koi8-ru koi8-u",
    "macintosh": "csmacintosh mac macintosh x-mac-roman",
    "windows-874": "dos-874 iso-8859-11 iso8859-11 iso885911 tis-620 windows-874",
    "windows-1250": "cp1250 windows-1250 x-cp1250",
    "windows-1251": "cp1251 windows-1251 x-cp1251",
    "windows-1252": "ansi_x3.4-1968 ascii cp1252 cp819 csisolatin1 ibm819 iso-8859-1 iso-ir-100 iso8859-1 "
    "iso88591 iso_8859-1 iso_8859-1:1987 l1 latin1 us-ascii windows-1252 x-cp1252",
    "windows-1253": "cp1253 windows-1253 x-cp1253",
    "windows-1254": "cp1254 csisolatin5 iso-8859-9 iso-ir-148 iso8859-9 iso88599 iso_8859-9 iso_8859-9:1989 "
    "l5 latin5 windows-1254 x-cp1254",
    "windows-1255": "cp1255 windows-1255 x-cp1255",
    "windows-1256": "cp1256 windows-1256 x-cp1256",
    "windows-1257": "cp1257 windows-1257 x-cp1257",
    "windows-1258": "cp1258 windows-1258 x-cp1258",
    "x-mac-cyrillic": "x-mac-cyrillic x-mac-ukrainian",
    "GBK": "chinese csgb2312 csiso58gb231280 gb2312 gb_2312 gb_2312-80 gbk iso-ir-58 x-gbk",
    "gb18030": "gb18030",
    "Big5": "big5 big5-hkscs cn-big5 csbig5 x-x-big5",
    "EUC-JP": "cseucpkdfmtjapanese euc-jp x-euc-jp",
    "ISO-2022-JP": "csiso2022jp iso-2022-jp",
    "Shift_JIS": "csshiftjis ms932 ms_kanji shift-jis shift_jis sjis windows-31j x-sjis",
    "EUC-KR": "cseuckr csksc56011987 euc-kr iso-ir-149 korean ks_c_5601-1987 ks_c_5601-1989 ksc5601 "
    "ksc_5601 windows-949",
    "replacement": "csiso2022kr hz-gb-2312 iso-2022-cn iso-2022-cn-ext iso-2022-kr replacement",
    "UTF-16BE": "unicodefffe utf-16be",
    "UTF-16LE": "csunicode iso-10646-ucs-2 ucs-2 unicode unicodefeff utf-16 utf-16le",
    "x-user-defined": "x-user-defined",
}
_LABELS = {label: name for name, labels in _ENCODINGS.items() for label in labels.split()}
_CODECS = {
    "UTF-8": "utf-8", "EUC-JP": "euc_jp", "ISO-2022-JP": "iso2022_jp", "EUC-KR": "cp949",
    "UTF-16BE": "utf-16-be", "UTF-16LE": "utf-16-le",
}  # fmt: skip
# Single-byte encodings other than ISO-8859-N and windows-N, whose codec names follow.
_SINGLE_BYTE = {
    "IBM866": "cp866", "ISO-8859-8-I": "iso8859_8", "KOI8-R": "koi8_r", "KOI8-U": "koi8_u",
    "macintosh": "mac_roman", "x-mac-cyrillic": "mac_cyrillic",
}  # fmt: skip
_SINGLE_BYTE_FIXES = {"KOI8-U": {0xAE: "ў", 0xBE: "Ў"}, "windows-1255": {0xCA: "ֺ"}}
# GB18030-2022 moved these characters out of the private use area; 0xA8BC and
# 0x8135F437 swap places. Python's decoder is one-to-one, so mapping its output suffices.
_GB18030_2022 = str.maketrans(
    "\ue5e5\ue78d\ue78e\ue78f\ue790\ue791\ue792\ue793\ue794\ue795\ue796\ue7c7\u1e3f"
    "\ue81e\ue826\ue82b\ue82c\ue832\ue843\ue854\ue864",
    "\u3000\ufe10\ufe12\ufe11\ufe13\ufe14\ufe15\ufe16\ufe17\ufe18\ufe19\u1e3f\ue7c7"
    "\u9fb4\u9fb5\u9fb6\u9fb7\u9fb8\u9fb9\u9fba\u9fbb",
)
# Big5 pairs whose WHATWG code point Python's big5hkscs lacks or maps differently.
_BIG5_FIXES = (
    "87" "7a㡵7b𡵓7c𣚞7d𦀡7e㻬a1𥣞a2㫵a3竼a4龗a5𤅡a6𨤍a7𣇪a8𠪊a9𣉞aa䌊ab蒄ac龖ad鐯ae䤰af蘓b0墖b1靊b2鈘b3秐"
    "b4稲b5晠b6権b7袝b8瑌b9篅ba枂bb稬bc剏bd遆be㓦bf珄c0𥶹c1瓆c2鿇c3垳c4䤯c5呌c6䄱c7𣚎c8堘c9穲ca𧭥cb讏"
    "cc䚮cd𦺈ce䆁cf𥶙d0箮d1𢒼d2鿈d3𢓁d4𢓉d5𢓌d6鿉d7蔄d8𣖻d9䂴da鿊db䓡dc𪷿dd拁de灮df鿋",
    "8e" "69箸6f簆7e糎ab緒b4縝cd者d0耨",
    "8f" "57菁69蒨6e萏cb覦cc覩fe起",
    "90" "6d都7a銹dc靜f1響",
    "91" "bf鼖",
    "92" "44蔃af兙b0兛b1兝b2兞c8鍮d1瑹",
    "94" "47浧ca禛",
    "95" "d9邗",
    "96" "44靝ed瀞fc嬨",
    "9b" "76爁78矗7b纇c6駖de釔ec惞f6澶",
    "9c" "42輶53侻62營68鄄6b鷰77菏bc尐bd秣d0婧",
    "9d" "57輋5a筑c4拐",
    "9e" "a9恢ef痹fd汊",
    "9f" "60鬮66鼗cb僭d8弌",
    "a0" "63蠏77拎d5瑨df煢e4牐",
    "a1" "45‧4e﹑c2¯e3～f2⊕f3⊙",
    "a2" "41∕42﹨44￥46￠47￡",
    "a3" "c0␀c1␁c2␂c3␃c4␄c5␅c6␆c7␇c8␈c9␉ca␊cb␋cc␌cd␍ce␎cf␏d0␐d1␑d2␒d3␓d4␔d5␕d6␖d7␗d8␘d9␙"
    "da␚db␛dc␜dd␝de␞df␟e0␡e1€",
    "c6" "cf廴d3无d5癶d7隶de〃df仝",
    "fa" "5f倩66偽bd包c5卄d5卿",
    "fb" "48嘅b8婷f3幵f9廐",
    "fc" "4f彘6c悤b9撐e2晴f1杞",
    "fd" "b7沜b8渝bb港f1煮",
    "fe" "52猪6f瑜aa瓩dd砉",
)
_BIG5_TOKENS = re.compile(rb"[\x00-\x7f]+|[\x81-\xfe][\x40-\x7e\xa1-\xfe]|.", re.S)
# Bytes surrogateescape kept because GB18030 could not decode them; a lone 0x80 is €.
_GB18030_INVALID = re.compile("[\udc81-\udcff]")


def for_label(label: str) -> str | None:
    """`Encoding::for_label`: the WHATWG encoding a label names, or None."""
    return _LABELS.get(ascii_lower(label.strip("\t\n\x0c\r ")))


@cache
def _single_byte(name: str) -> tuple[bytes, dict[int, str]]:
    """The undefined bytes of a single-byte encoding and its decode map (latin-1 → text)."""
    codec = _SINGLE_BYTE.get(name) or name.lower().replace("iso-8859-", "iso8859_").replace("windows-", "cp")
    fixes = _SINGLE_BYTE_FIXES.get(name, {})
    undefined = bytearray()
    table: dict[int, str] = {}
    for byte in range(0x80, 0x100):
        try:
            char = fixes.get(byte) or bytes([byte]).decode(codec)
        except UnicodeDecodeError:
            # WHATWG fills the Windows code pages' holes with the C1 controls.
            if not (name.startswith("windows-") and byte < 0xA0):
                undefined.append(byte)
                continue
            char = chr(byte)
        table[byte] = char
    return bytes(undefined), table


@cache
def _big5() -> dict[int, str]:
    table: dict[int, str] = {}
    for lead in range(0x81, 0xFF):
        for trail in (*range(0x40, 0x7F), *range(0xA1, 0xFF)):
            try:
                table[lead << 8 | trail] = bytes((lead, trail)).decode("big5hkscs")
            except UnicodeDecodeError:
                pass
    for row in _BIG5_FIXES:
        lead = int(row[:2], 16)
        for match in re.finditer(r"([0-9a-f]{2})(.)", row[2:]):
            table[lead << 8 | int(match[1], 16)] = match[2]
    return table


def _decode_big5(data: bytes) -> str | None:
    table = _big5()
    out: list[str] = []
    for match in _BIG5_TOKENS.finditer(data):
        token = match[0]
        if token[0] < 0x80:
            out.append(token.decode("ascii"))
        elif len(token) == 2 and (char := table.get(token[0] << 8 | token[1])) is not None:
            out.append(char)
        else:
            return None
    return "".join(out)


def decode(data: bytes, name: str) -> str | None:
    """`decode_without_bom_handling_and_without_replacement`: None if malformed."""
    try:
        if name in ("GBK", "gb18030"):
            text = data.decode("gb18030", "surrogateescape")
            if _GB18030_INVALID.search(text):
                return None
            return text.replace("\udc80", "€").translate(_GB18030_2022)
        if name == "Big5":
            return _decode_big5(data)
        if name == "Shift_JIS":
            text = data.decode("cp932")
            # cp932 decodes the single bytes 0xA0 and 0xFD-0xFF to private-use characters.
            return None if any(c in "\uf8f0\uf8f1\uf8f2\uf8f3" for c in text) else text
        if name == "x-user-defined":
            return data.decode("latin-1").translate({b: 0xF780 + b - 0x80 for b in range(0x80, 0x100)})
        if name == "replacement":
            return None if data else ""
        if name in _CODECS:
            return data.decode(_CODECS[name])
        undefined, table = _single_byte(name)
        if undefined and len(data.translate(None, undefined)) != len(data):
            return None
        return data.decode("latin-1").translate(table)
    except UnicodeDecodeError:
        return None


def charset_label(value: str) -> str | None:
    """The `charset=` parameter of a Content-Type value, unquoted."""
    for part in value.split(";"):
        name, eq, label = part.partition("=")
        if eq and ascii_lower(trim(name)) == "charset":
            return trim(label).strip("'\"")
    return None


def meta_charset(document: dom.Document) -> str | None:
    """The first charset declared by a `<meta>` in the head."""
    for element in dom.iter_elements(document.head):
        if not element.html("meta"):
            continue
        declared = element.attr("charset")
        if declared is None:
            equiv = element.attr("http-equiv")
            content = element.attr("content")
            if equiv is not None and ascii_lower(equiv) == "content-type" and content is not None:
                declared = charset_label(content)
        if declared is not None:
            return declared
    return None


def decode_html(data: bytes, content_type: str) -> str:
    """The page text; raises ValueError for an unknown charset or mismatching bytes."""
    declared = charset_label(content_type)
    if declared is None:
        head = data[:8192].decode("utf-8", "replace")
        declared = meta_charset(dom.parse(head))
    name = "UTF-8" if declared is None else for_label(declared)
    if name is None:
        raise ValueError("original page declares an unsupported charset")
    text = decode(data, name)
    if text is None:
        raise ValueError("original page bytes do not match the declared charset")
    return text.lstrip("\ufeff")
