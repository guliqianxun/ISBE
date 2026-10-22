"""Date, struct-decoding and URI rules the research and briefing inputs depend on.

The expected messages were recorded from the earlier Rust build on the same inputs.
"""

from dataclasses import dataclass
from typing import Any

import pytest

from isbe.reports import research_compat as compat
from isbe.reports import research_uri
from isbe.reports.research_compat import (
    STR,
    USIZE,
    DecodeError,
    Field,
    Opt,
    Seq,
    Struct,
)

# --- dates -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "date"),
    [
        ("-0000000123456-1-2", (-123456, 1, 2)),
        ("    -123456 - 1 - 2    ", (-123456, 1, 2)),
        ("-12345-1-2", (-12345, 1, 2)),
        ("-1234-12-31", (-1234, 12, 31)),
        ("-7-6-5", (-7, 6, 5)),
        ("350-2-28", (350, 2, 28)),
        ("360-02-29", (360, 2, 29)),
        ("0360-02-29", (360, 2, 29)),
        ("2015-2 -18", (2015, 2, 18)),
        ("2015-02-18", (2015, 2, 18)),
        ("+70-2-18", (70, 2, 18)),
        ("+70000-2-18", (70000, 2, 18)),
        ("+00007-2-18", (7, 2, 18)),
        (" 2024-1-1 ", (2024, 1, 1)),
    ],
)
def test_dates_parse_with_the_calendar_grammar(text: str, date: compat.Date) -> None:
    assert compat.parse_date(text) == date


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "premature end of input"),
        ("x", "input contains invalid characters"),
        ("Fri, 09 Aug 2013 GMT", "input contains invalid characters"),
        ("2014/02/03", "input contains invalid characters"),
        ("2014", "premature end of input"),
        ("2014-01", "premature end of input"),
        ("2014-01-00", "input is out of range"),
        ("2014-11-32", "input is out of range"),
        ("2014-13-01", "input is out of range"),
        ("2024-02-30", "input is out of range"),
        ("9999999-9-9", "input contains invalid characters"),
        ("2026-10-16x", "trailing input"),
        ("+300000-01-01", "input is out of range"),
        ("1900-02-29", "input is out of range"),
    ],
)
def test_bad_dates_fail_with_the_calendar_message(text: str, message: str) -> None:
    with pytest.raises(DecodeError) as error:
        compat.parse_date(text)
    assert str(error.value) == message


def test_strftime_form_takes_no_space_before_a_dash_and_display_adds_signs() -> None:
    with pytest.raises(DecodeError, match="invalid characters"):
        compat.parse_date("2026 -10-12", spaces=False)
    assert compat.parse_date(" 2026-10-12", spaces=False) == (2026, 10, 12)
    with pytest.raises(DecodeError, match="trailing input"):
        compat.parse_date("2026-10-12 ", spaces=False)
    assert compat.format_date((2026, 1, 2)) == "2026-01-02"
    assert compat.format_date((-1, 1, 1)) == "-0001-01-01"
    assert compat.format_date((10000, 1, 1)) == "+10000-01-01"


# --- struct decoding -----------------------------------------------------------------


@dataclass
class Row:
    name: str
    count: int
    note: str | None
    tags: list[str]


ROW = Struct(
    "Row",
    lambda v: Row(**v),
    (Field("name", STR), Field("count", USIZE), Field("note", Opt(STR)),
     Field("tags", Seq(STR), default=list)),
)


def fails(value: Any, *, streamed: bool = False, spec: Struct = ROW) -> str:
    with pytest.raises(DecodeError) as error:
        compat.decode(spec, value, streamed=streamed)
    return str(error.value)


def test_structs_follow_the_strict_decoding_rules() -> None:
    assert compat.decode(ROW, {"name": "a", "count": 2}, streamed=False) == Row("a", 2, None, [])
    assert compat.decode(ROW, ["a", 2, None], streamed=False) == Row("a", 2, None, [])
    assert fails({"name": "a", "count": 1, "extra": 1}) == (
        "unknown field `extra`, expected one of `name`, `count`, `note`, `tags`"
    )
    assert fails({"count": 1}) == "missing field `name`"
    assert fails({"name": None, "count": 1}) == "invalid type: null, expected a string"
    assert fails({"name": "a", "count": -1}) == "invalid value: integer `-1`, expected usize"
    assert fails({"name": "a", "count": 1.0}) == (
        "invalid type: floating point `1.0`, expected usize"
    )
    assert fails({"name": "a", "count": True}) == "invalid type: boolean `true`, expected usize"
    assert fails({"name": "a", "count": 1, "tags": "x"}) == (
        'invalid type: string "x", expected a sequence'
    )
    assert fails("row") == 'invalid type: string "row", expected struct Row'
    assert fails(["a"]) == "invalid length 1, expected struct Row with 4 elements"
    assert fails(["a", 1, None, [], 5]) == (
        "invalid length 5, expected fewer elements in array"
    )
    assert fails(["a", 1, None, [], 5], streamed=True) == "trailing characters"
    # A sorted map (from_value) reports the first bad member in key order.
    assert fails({"name": 1, "count": "x"}) == 'invalid type: string "x", expected usize'
    loose = Struct("Loose", lambda v: v, (Field("a", STR),), deny_unknown=False)
    assert compat.decode(loose, {"a": "x", "b": 1}, streamed=False) == {"a": "x"}


def test_streamed_documents_keep_member_order_and_reject_repeats() -> None:
    assert fails(compat.loads('{"name":"a","name":"b","count":1}'), streamed=True) == (
        "duplicate field `name`"
    )
    assert fails(compat.loads('{"count":"x","name":1}'), streamed=True) == (
        'invalid type: string "x", expected usize'
    )
    with pytest.raises(ValueError):
        compat.loads('{"a": NaN}')
    assert fails(compat.loads('{"name":"\\ud800","count":1}'), streamed=True) == (
        "lone leading surrogate in hex escape"
    )


def test_utf8_errors_use_the_established_text() -> None:
    with pytest.raises(DecodeError, match="^incomplete utf-8 byte sequence from index 2$"):
        compat.utf8(b"ok\xe4\xb8")
    with pytest.raises(DecodeError, match="^invalid utf-8 sequence of 1 bytes from index 1$"):
        compat.utf8(b"a\xffb")
    assert compat.utf8("中文".encode()) == "中文"


# --- URIs --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "scheme"),
    [
        ("https://example.invalid/a(b)", "https"),
        ("HTTP://Example.NET:8080/p|q", "http"),
        ("https://example.org/a(b)/路径{x}?q=[1]&r=~2#frag", "https"),
        ("https://[::1]:8/x", "https"),
        ("ftp://x/y", "ftp"),
        ("://x", ""),
        ("a~b://x/", "a~b"),
        ("example.com", None),
        ("/x", None),
        ("*", None),
        ("a", None),
        ("javascript:alert(1)", None),
        ("http://a%20b@c/", "http"),
    ],
)
def test_uris_parse_like_the_http_crate(url: str, scheme: str | None) -> None:
    assert research_uri.scheme(url) == scheme


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("", "empty string"),
        ("a b", "invalid uri character"),
        ("?", "invalid uri character"),
        ("example.com/path", "invalid format"),
        ("http://", "empty string"),
        ("http:///x", "invalid format"),
        ("http://a:1:2/", "invalid authority"),
        ("http://[::1/", "invalid authority"),
        ("http://a%20b/", "invalid authority"),
        ("a" * 70 + "://x", "scheme too long"),
        ("https://x/a<b", "invalid uri character"),
        ("https://x/a?b c", "invalid uri character"),
        ("/x y", "invalid uri character"),
    ],
)
def test_bad_uris_fail_with_the_http_crates_reason(url: str, message: str) -> None:
    with pytest.raises(research_uri.UriError) as error:
        research_uri.scheme(url)
    assert str(error.value) == message
