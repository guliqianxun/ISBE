"""The feed parser against feed-rs 3.0: `feed_cases.json` holds inputs and
what a feed-rs build printed for them (ids it invents are left out)."""

import base64
import json
import re
from pathlib import Path
from typing import Any

import pytest

from isbe.collect import feed, timestamps, xmldecode
from isbe.core.errors import describe

CASES = json.loads((Path(__file__).parent / "feed_cases.json").read_text())
TYPES = {"atom": "Atom", "json": "JSON", "rss0": "RSS0", "rss1": "RSS1", "rss2": "RSS2"}


def model(raw: bytes, base: str | None) -> dict[str, Any]:
    try:
        f = feed.parse(raw, base=base)
    except feed.FeedError as error:
        return {"error": describe(error)}

    def ts(d: Any) -> str | None:
        return None if d is None else timestamps.format_rfc3339(d)

    return {
        "type": TYPES[f.type], "title": f.title, "language": f.language,
        "entries": [{
            "id": e.id, "title": e.title, "summary": e.summary, "content": e.content,
            "links": [[x.href, x.rel] for x in e.links], "published": ts(e.published),
            "updated": ts(e.updated), "authors": e.authors, "categories": e.categories,
        } for e in f.entries],
    }


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_matches_feed_rs(case: dict[str, Any]) -> None:
    expected = case["expected"]
    if "error" in expected and expected["error"].startswith("unable to parse JSON"):
        # The JSON parser's " at line L column C" suffix is not part of the message.
        expected = {"error": re.sub(r" at line \d+ column \d+", "", expected["error"])}
    assert model(base64.b64decode(case["b64"]), case["base"]) == expected


def test_errors_chain_the_xml_error() -> None:
    with pytest.raises(feed.FeedError) as failure:
        feed.parse(b'<rss version="2.0"><channel><title>A & B</title></channel></rss>')
    message = "Parser error: ill-formed document: entity or character reference not closed: `;` not found before end of input"
    assert str(failure.value) == f"unable to parse XML: {message}"
    assert describe(failure.value) == f"unable to parse XML: {message}: {message}"


def test_atom_type_errors_name_the_byte_position() -> None:
    raw = '<feed xmlns="http://www.w3.org/2005/Atom">ééé<title type="weird">x</title></feed>'
    with pytest.raises(feed.FeedError, match="^unable to parse feed: unsupported content type weird at position 68$"):
        feed.parse(raw.encode())


def test_undecodable_bytes_fail_where_quick_xml_failed() -> None:
    head = b'<rss version="2.0"><channel><title>T</title>'
    item = b"<item><title>x</title><link>https://e/1</link></item>"
    early = head + b"<item><title>\xff</title></item></channel></rss>"
    late = head + item * 3 + b"<item><title>\xff</title></item></channel></rss>"
    with pytest.raises(feed.FeedError, match="^unable to parse feed: no root element$"):
        feed.parse(early)  # inside the first 64 bytes: before the root element
    with pytest.raises(feed.FeedError, match="^unable to parse XML: Parser error: cannot decode input using UTF-8$"):
        feed.parse(late)
    with pytest.raises(xmldecode.DecodeError) as failure:
        xmldecode.decode(late)
    assert failure.value.text == late[:64].decode()


def test_declared_encodings_and_the_fallback() -> None:
    gbk = '<?xml version="1.0" encoding="GB2312"?><rss version="2.0"><channel><title>雷达</title></channel></rss>'
    assert feed.parse(gbk.encode("gbk")).title == "雷达"
    # GBK text inside the first 64 bytes must be decoded as GBK, not as UTF-8 until the declaration is read.
    short = '<?xml encoding="gbk"?><rss version=""><channel><title>雷达回波</title></channel></rss>'
    assert feed.parse(short.encode("gbk")).title == "雷达回波"
    bare = '<rss version="2.0"><channel><title>雷达</title></channel></rss>'.encode("gbk")
    assert feed.parse(bare, content_type="text/xml; charset=gbk").title == "雷达"
    assert feed.parse(bare, content_type='application/rss+xml;charset="GB2312"').title == "雷达"
    with pytest.raises(feed.FeedError):
        feed.parse(bare, content_type="text/xml")
    # A declaration wins over the header, and valid UTF-8 over a header charset.
    utf8 = '<rss version="2.0"><channel><title>雷达</title></channel></rss>'.encode()
    assert feed.parse(utf8, content_type="text/xml; charset=gbk").title == "雷达"
    latin = '<?xml version="1.0" encoding="ISO-8859-1"?><rss version="2.0"><channel><title>Café</title></channel></rss>'
    assert feed.parse(latin.encode("latin-1"), content_type="text/xml; charset=utf-8").title == "Café"
    # windows-1252 as WHATWG decodes it: 0x81 is a C1 control, not an error.
    assert feed.parse(latin.replace("é", "\x81").encode("latin-1")).title == "Caf\x81"


def test_leap_seconds_keep_their_printed_form() -> None:
    when = timestamps.lenient("Fri, 09 Oct 2026 23:59:60 +0000")
    assert when is not None and timestamps.format_rfc3339(when) == "2026-10-09T23:59:60Z"
    shifted = timestamps.lenient("2026-10-10T00:59:60+01:00")
    assert shifted is not None and timestamps.format_rfc3339(shifted) == "2026-10-09T23:59:60Z"


def test_person_names() -> None:
    assert feed.person_name("jane@example.com (Jane Doe)") == "(Jane Doe)"  # as feed-rs
    assert feed.person_name("Bob <bob@example.com>") == "Bob"
    assert feed.person_name("<bob@example.com>") is None
    assert feed.person_name("  Ann  ") == "Ann"
