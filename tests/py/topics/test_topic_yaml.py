import pytest

from isbe.topics import yamlio
from isbe.topics.yamlio import YamlError, dump, load_value, quote

NBSP, LS, BOM, NEL = "\N{NO-BREAK SPACE}", "\N{LINE SEPARATOR}", "\N{ZERO WIDTH NO-BREAK SPACE}", "\x85"


def test_plain_scalars_follow_the_yaml_1_2_core_schema() -> None:
    doc = load_value(
        "words: [yes, no, on, off, y, n, Yes, NO, tRue, nULL]\n"
        "dates: [2026-10-16, 2026-10-16T10:00:00Z, '12:30', 12:30]\n"
        "bools: [true, True, TRUE, false, False, FALSE]\n"
        "nulls: [~, null, Null, NULL]\n"
        "empty:\n"
        "ints: [0, -3, +4, 017, 0o17, 0x1F]\n"
        "floats: [1.5, .5, 1., 1e3, -0.0]\n"
        "strings: [1_000, 0b101, 0xGG, 0o, '7', \"true\"]\n"
    )
    assert doc["words"] == ["yes", "no", "on", "off", "y", "n", "Yes", "NO", "tRue", "nULL"]
    assert doc["dates"] == ["2026-10-16", "2026-10-16T10:00:00Z", "12:30", "12:30"]
    assert doc["bools"] == [True, True, True, False, False, False]
    assert doc["nulls"] == [None] * 4 and doc["empty"] is None
    assert doc["ints"] == [0, -3, 4, 17, 15, 31]
    assert doc["floats"] == [1.5, 0.5, 1.0, 1000.0, -0.0]
    assert doc["strings"] == ["1_000", "0b101", "0xGG", "0o", "7", "true"]
    assert all(type(v) is int for v in doc["ints"])


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("a: 1\na: 2\n", "line 2 column 1: duplicate mapping key: a"),
        ("a: .inf\n", "non-finite float"),
        ("a: .NaN\n", "non-finite float"),
        ("a: 1e400\n", "non-finite float"),
        ("? [a]\n: x\n", "mapping keys must be scalars"),
        ("~: x\n", "mapping keys must not be null"),
        ("a: !!timestamp 2026-10-16\n", "unsupported YAML tag"),
        ("a: *missing\n", "found undefined alias"),
        ("id: [broken", "line 1 column 12:"),
        ("a: 1\n---\nb: 2\n", "expected a single document"),
        ("a: [" + "[" * 150 + "]" * 150 + "]\n", "nested too deeply"),
    ],
)
def test_unreadable_documents_raise_located_errors(text: str, message: str) -> None:
    with pytest.raises(YamlError, match=message.replace("[", r"\[").replace("*", r"\*").replace(".", r"\.")):
        load_value(text)


def test_merge_keys_aliases_and_empty_documents() -> None:
    assert load_value("b: &x {c: 1}\na:\n  <<: *x\n  d: 2\n") == {"b": {"c": 1}, "a": {"c": 1, "d": 2}}
    assert load_value("a: {<<: [{x: 1}, {x: 2, y: 2}], y: 3}\n") == {"a": {"x": 1, "y": 3}}
    assert load_value("a: &a 1\nb: *a\n") == {"a": 1, "b": 1}
    assert load_value("") is None and load_value("---\n") is None
    assert load_value("1: x\ntrue: y\n") == {"1": "x", "true": "y"}
    assert load_value("a: <<\n'<<': {b: 1}\nc: !local text\n") == {"a": "<<", "<<": {"b": 1}, "c": "text"}
    assert load_value('a: "\\ud83d\\ude00"\n') == {"a": "\N{GRINNING FACE}"}
    assert load_value(BOM + "a: 1\r\n") == {"a": 1}


def test_alias_bombs_are_refused() -> None:
    lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    lines += [f"a{i}: &a{i} [{', '.join([f'*a{i - 1}'] * 10)}]" for i in range(1, 7)]
    with pytest.raises(YamlError, match="too many nodes"):
        load_value("\n".join(lines) + "\n")
    with pytest.raises(YamlError, match="nested too deeply"):
        load_value("a: &a [*a]\n")


RISKY = [
    "yes", "No", "ON", "off", "y", "N", "tRue", "FALSE", "null", "nULL", "~", "", "<<", "=",
    "1", "+1", "-1", "007", "017", "1_000", "0x1F", "0o17", "0b101", "1e3", "1.", ".5", "-.5", ".inf", ".NaN",
    "12:30", "2026-10-16", "2026-10-16T10:00:00Z", "- x", "? x", ": x", "x: y", "a #b", "#x", "[x]", "{x}", "&a", "*a",
    "!a", "|", ">", "%x", "@x", "`x", "'x'", '"x"', " lead", "trail ", "\t", "a\tb", "a\nb", "a\n", "\r\n", "x" + NEL + "y",
    "x" + LS + "y", BOM + "bom", "\x07bell", "\x00nul", "\x7fdel", NBSP + "nbsp", "---", "...", "a, b", "3D vision",
    "${VAR:-https://example.test}/feed", "C:\\path", "中文 笔记", "\N{GRINNING FACE}", "x" * 300, "multi\nline\n",
]


@pytest.mark.parametrize("value", RISKY)
def test_dump_round_trips_strings_any_reader_could_retype(value: str) -> None:
    for doc in ({"s": value}, {value: 1} if value else {"k": [value]}, [value]):
        text = dump(doc)
        assert load_value(text) == doc, text


def test_dump_writes_readable_block_yaml() -> None:
    config = {
        "id": "t",
        "label": "临近降水 yes",
        "active": False,
        "window_days": 7,
        "arxiv": {"categories": ["cs.LG"], "include_keywords": ["yes", "2026", "radar echo"], "max_results": 50},
        "rss": {"feeds": [{"name": "Feed", "url": "${HOST:-https://example.test}/feed"}], "exclude_keywords": []},
        "digest": {},
        "score": 1.5,
        "none": None,
    }
    text = dump(config)
    assert load_value(text) == config
    assert "label: 临近降水 yes\n" in text
    assert "url: ${HOST:-https://example.test}/feed\n" in text
    assert "- 'yes'\n" in text and "- '2026'\n" in text and "- radar echo\n" in text
    assert "active: false\n" in text and "window_days: 7\n" in text and "digest: {}\n" in text and "none: null\n" in text
    assert list(load_value(text)) == sorted(config), "keys sorted, as the Rust console wrote them"
    long = dump({"label": "word " * 100})
    assert long.count("\n") == 1, "long strings are not folded"


def test_dump_refuses_values_json_cannot_hold() -> None:
    for bad in (float("nan"), float("inf"), object()):
        with pytest.raises((YamlError, TypeError, ValueError)):
            dump({"x": bad})


@pytest.mark.parametrize("value", [*RISKY, "\\", '"', "a\\nb"])
def test_quote_is_a_double_quoted_scalar_for_every_string(value: str) -> None:
    quoted = quote(value)
    assert quoted.startswith('"') and quoted.endswith('"') and "\n" not in quoted
    assert yamlio.load_value(f"k: {quoted}\n") == {"k": value}


def test_dump_sorts_keys_like_the_console() -> None:
    # The text the earlier build wrote for POST /api/topics with this body.
    config = {
        "id": "second",
        "label": "Second",
        "cadence": "daily",
        "arxiv": {"include_keywords": ["language"], "categories": ["cs.CL"]},
    }
    assert dump(config) == (
        "arxiv:\n  categories:\n  - cs.CL\n  include_keywords:\n  - language\ncadence: daily\nid: second\nlabel: Second\n"
    )
