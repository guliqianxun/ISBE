"""Strict charset decoding (`article_date::decode_html` with encoding_rs).

The decoder fixes were measured against encoding_rs over every one- and
two-byte sequence; the cases below pin each correction."""

from collections.abc import Callable

import pytest

from isbe.articles import charset
from isbe.articles.charset import decode, decode_html, for_label


def test_declared_chinese_charsets_decode_without_guessing_or_replacement(
    fixture: Callable[[str], bytes],
) -> None:
    data = fixture("cnr.gb2312.html")
    html = decode_html(data, "text/html")
    assert "央广网" in html
    original = "<head><meta name='publishdate' content='2026-10-12'></head><p>中文新聞</p>"
    for codec, label in [("gbk", "gbk"), ("gb18030", "gb18030"), ("big5", "big5")]:
        assert decode_html(original.encode(codec), f'text/html; charset="{label}"') == original
    with pytest.raises(ValueError, match="do not match"):
        decode_html(data, "text/html; charset=utf-8")
    with pytest.raises(ValueError, match="do not match"):
        decode_html(b"\xff", "text/html")
    with pytest.raises(ValueError, match="unsupported charset"):
        decode_html(b"<head><meta charset='invented'></head>", "text/html")
    with pytest.raises(ValueError):
        decode_html(b"<head><!-- <meta charset='gbk'> --></head>\xff", "text/html")


def test_labels_follow_the_whatwg_table() -> None:
    assert for_label(" \tGB2312\n") == "GBK"
    assert for_label("latin1") == "windows-1252"
    assert for_label("x-sjis") == "Shift_JIS"
    assert for_label("utf-16") == "UTF-16LE"
    assert for_label("gb_2312-80") == "GBK"
    # Python codec aliases that are not WHATWG labels are unsupported.
    assert for_label("cp936") is None and for_label("utf8mb4") is None and for_label("") is None
    assert charset.charset_label("text/html; Charset = 'gbk' ") == "gbk"
    assert charset.charset_label("text/html") is None


def test_meta_charset_comes_from_the_head_of_the_first_8_kib() -> None:
    body = "<p>中文</p>".encode("gbk")
    assert decode_html(b"<meta charset=gbk>" + body, "") == "<meta charset=gbk><p>中文</p>"
    equiv = b'<head><meta http-equiv="Content-Type" content="text/html; charset=GB2312"></head>'
    assert decode_html(equiv + body, "text/html").endswith("<p>中文</p>")
    late = b"<head>" + b" " * 9000 + b"<meta charset=gbk></head>" + body
    with pytest.raises(ValueError, match="do not match"):
        decode_html(late, "text/html")
    # The byte-order mark is dropped, and does not move a following <head> into the body.
    assert decode_html(b"\xef\xbb\xbf<p>x</p>", "text/html") == "<p>x</p>"
    with_bom = b"\xef\xbb\xbf<head><meta charset=windows-1252></head><p>\xe9</p>"
    assert decode_html(with_bom, "text/html") == "ï»¿<head><meta charset=windows-1252></head><p>é</p>"


@pytest.mark.parametrize(
    ("data", "name", "expected"),
    [
        (b"\x81\x8d\x8f\x90\x9d", "windows-1252", "\x81\x8d\x8f\x90\x9d"),
        (b"\x98", "windows-1251", "\x98"),
        (b"\xca", "windows-1255", "ֺ"),
        (b"\xae\xbe", "KOI8-U", "ўЎ"),
        (b"\x80\xff", "x-user-defined", ""),
        (b"\x80", "GBK", "€"),
        (b"\xa6\xd9\xa8\xbc\xfe\x59", "gb18030", "︐ḿ龴"),
        (b"\x81\x35\xf4\x37", "gb18030", ""),
        (b"\x87\x40", "Big5", "䏰"),
        (b"\xa1\x45\xa3\xe1", "Big5", "‧€"),
        (b"\x82\xa0", "Shift_JIS", "あ"),
        (b"", "replacement", ""),
    ],
)
def test_decoders_match_encoding_rs(data: bytes, name: str, expected: str) -> None:
    assert decode(data, name) == expected


@pytest.mark.parametrize(
    ("data", "name"),
    [
        (b"\xaa", "windows-1253"),  # unassigned above 0x9F stays an error
        (b"\xa0", "Shift_JIS"),
        (b"\xfd", "Shift_JIS"),
        (b"\xff", "GBK"),
        (b"\x81\x30", "gb18030"),
        (b"\x80\x40", "Big5"),
        (b"x", "replacement"),
    ],
)
def test_malformed_bytes_are_refused(data: bytes, name: str) -> None:
    assert decode(data, name) is None
