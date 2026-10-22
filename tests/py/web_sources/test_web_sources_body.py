"""Request bodies are refused with the exact messages the console has always answered.

The expected texts are what the earlier Rust build answered (checked against its binary)."""

import pytest
from web_sources_helpers import config, request, send

from isbe.web.console import Console

PLAN = {"id": "p", "name": "N", "topic": "T", "provider": "searxng", "queries": ["q"],
        "start_date": "2026-10-01", "end_date": "2026-10-02", "language": "en", "max_results": 1}

CASES = [
    ("/api/sources", {"config": config("city"), "unexpected": True},
     "unknown field `unexpected`, expected `config` or `revision`"),
    ("/api/sources", {}, "missing field `config`"),
    ("/api/sources", {"config": {}}, "missing field `id`"),
    ("/api/sources", {"config": {"id": "a", "name": "b"}}, "missing field `topic`"),
    ("/api/sources", {"config": {**config("a"), "kind": "atom"}}, "unknown variant `atom`, expected `rss` or `page`"),
    ("/api/sources", {"config": {**config("a"), "kind": 1}}, "invalid type: integer `1`, expected string or map"),
    ("/api/sources", {"config": {**config("a"), "kind": {"rss": 1}}}, "invalid type: integer `1`, expected unit"),
    ("/api/sources", {"config": {**config("a"), "kind": {}}}, "invalid value: map, expected map with a single key"),
    ("/api/sources", {"config": {**config("a"), "enabled": "yes"}},
     'invalid type: string "yes", expected a boolean'),
    ("/api/sources", {"config": {**config("a"), "enabled": 'x\n"y\t\\\u0001é\u00a0\u200b\u0301z\u2028'}},
     r'invalid type: string "x\n\"y\t\\\u{1}é\u{a0}\u{200b}\u{301}z\u{2028}", expected a boolean'),
    ("/api/sources", {"config": {**config("a"), "max_items": 1 << 64}},
     "invalid type: floating point `1.8446744073709552e+19`, expected usize"),
    ("/api/sources", {"config": {**config("a"), "max_items": -1}}, "invalid value: integer `-1`, expected usize"),
    ("/api/sources", {"config": {**config("a"), "max_items": 1.0}},
     "invalid type: floating point `1.0`, expected usize"),
    ("/api/sources", {"config": {**config("a"), "max_items": 1e21}},
     "invalid type: floating point `1e+21`, expected usize"),
    ("/api/sources", {"config": {**config("a"), "url": None}}, "invalid type: null, expected a string"),
    ("/api/sources", {"config": {**config("a"), "zzz": 1, "name": 2}}, "invalid type: integer `2`, expected a string"),
    ("/api/sources", {"config": {**config("a"), "id": "x\n\"y"}, "revision": 3},
     "invalid type: integer `3`, expected a string"),
    ("/api/sources", [config("a")], "invalid length 1, expected struct Save with 2 elements"),
    ("/api/sources", [config("a"), None, 1], "invalid length 3, expected fewer elements in array"),
    ("/api/sources", "text", 'invalid type: string "text", expected struct Save'),
    ("/api/sources", {"config": ["a", "b", "c"]}, "invalid length 3, expected struct Source with 8 elements"),
    ("/api/sources/city/collect", [], "invalid length 0, expected struct Collect with 1 element"),
    ("/api/sources/import", {"sources": {}}, "invalid type: map, expected a sequence"),
    ("/api/sources/import", {"sources": [1]}, "invalid type: integer `1`, expected struct Source"),
    ("/api/sources/batch", {"sources": [{"id": "a", "revision": 1}], "enabled": True},
     "invalid type: integer `1`, expected a string"),
    ("/api/sources/batch", {"sources": [], "enabled": 1}, "invalid type: integer `1`, expected a boolean"),
    ("/api/sources/discover", {"url": "https://example.org/", "topic": "ESG", "request_id": "req"},
     "missing field `auto_enable`"),
    ("/api/sources/city/collect", {"request_id": "req", "unexpected": True},
     "unknown field `unexpected`, expected `request_id`"),
    ("/api/sources/search", {"config": {}, "unexpected": True}, "missing field `id`"),
    ("/api/sources/search", {"config": {**PLAN, "provider": "google"}},
     "unknown variant `google`, expected `baidu` or `searxng`"),
    ("/api/sources/search", {"config": {**PLAN, "queries": "q"}}, 'invalid type: string "q", expected a sequence'),
    ("/api/sources/search/run", {"config": PLAN, "request_id": "req", "unexpected": True},
     "unknown field `unexpected`, expected `config` or `request_id`"),
    ("/api/sources/search/plan/recheck", {"request_id": "bad", "after": 3}, "invalid type: integer `3`, expected a string"),
    ("/api/sources/search/plan/weekly", {"topic_id": "news", "week_end": "2026-10-18", "unexpected": True},
     "unknown field `unexpected`, expected `topic_id` or `week_end`"),
    ("/api/sources/article", {"topic": "news", "period": "2020-W42-media", "id": "one", "url": "x"},
     "unknown field `url`, expected one of `topic`, `period`, `id`"),
    ("/api/sources/article", {"topic": "news", "period": "2020-W42-media"}, "missing field `id`"),
]


@pytest.mark.parametrize(("path", "body", "message"), CASES)
def test_bodies_are_refused_with_strict_decoder_messages(app: Console, path: str, body: object, message: str) -> None:
    assert request(app, "POST", path, body) == (400, {"error": f"Invalid source request: {message}"})


def test_array_bodies_and_defaults_decode_strictly(app: Console) -> None:
    positional = [["city", "City news", "上海 ESG", "rss", "https://example.org/feed"], None]
    code, saved = request(app, "POST", "/api/sources", positional)
    assert code == 200
    assert saved["config"] == {"id": "city", "name": "City news", "topic": "上海 ESG", "kind": "rss",
                               "url": "https://example.org/feed", "enabled": True, "link_pattern": "",
                               "max_items": 100}
    code, other = request(app, "POST", "/api/sources", {"config": {**config("other"), "kind": {"page": None}}})
    assert (code, other["config"]["kind"]) == (200, "page")


def test_unreadable_bodies_answer_like_request_json(app: Console) -> None:
    assert send(app, "POST", "/api/sources", raw=b"{")[:2] == (400, {"error": "Expected a JSON object"})
    assert send(app, "POST", "/api/sources", raw=b" " * 70000)[:2] == (413, {"error": "Settings request is too large"})
