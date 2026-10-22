import pytest

from isbe.core import jsonfmt


def test_compact_sorted_raw_utf8() -> None:
    value = {"user": "主题\n\"x\"", "max_tokens": 10, "system": "s", "nested": {"b": [1, True, None], "a": {}}}
    assert jsonfmt.dumps(value) == (
        '{"max_tokens":10,"nested":{"a":{},"b":[1,true,null]},"system":"s","user":"主题\\n\\"x\\""}'
    )
    assert jsonfmt.dumps({"z": 1, "a": 2}, sort_keys=False) == '{"z":1,"a":2}'
    assert jsonfmt.dumps("\x01\x7f/") == '"\\u0001\x7f/"'


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0.0, "0.0"),
        (-0.0, "-0.0"),
        (1.0, "1.0"),
        (100.0, "100.0"),
        (0.2, "0.2"),
        (1e15, "1000000000000000.0"),
        (1e16, "1e+16"),
        (1.5e20, "1.5e+20"),
        (123456789012345680.0, "1.2345678901234568e+17"),
        (0.001234, "0.001234"),
        (0.00001, "0.00001"),
        (0.000001, "1e-6"),
        (1.5e-7, "1.5e-7"),
        (-2.5, "-2.5"),
        (5e-324, "5e-324"),
        (1.7976931348623157e308, "1.7976931348623157e+308"),
        (float("nan"), "null"),
        (float("inf"), "null"),
    ],
)
def test_floats_use_the_json_layout(value: float, text: str) -> None:
    assert jsonfmt.dumps(value) == text


def test_pretty_matches_to_string_pretty() -> None:
    assert jsonfmt.dumps_pretty({}) == "{}"
    assert jsonfmt.dumps_pretty({"b": "2", "a": "1"}) == '{\n  "a": "1",\n  "b": "2"\n}'
    assert jsonfmt.dumps_pretty({"a": [1, {"x": []}]}) == (
        '{\n  "a": [\n    1,\n    {\n      "x": []\n    }\n  ]\n}'
    )


def test_loads_is_strict() -> None:
    for text in ["NaN", "[Infinity]", "1e400", '{"a": -Infinity}', "", "{", "\ufeff{}"]:
        with pytest.raises(ValueError):
            jsonfmt.loads(text)
    assert jsonfmt.loads("18446744073709551615") == 18446744073709551615
    assert jsonfmt.loads("18446744073709551616") == 1.8446744073709552e19
    assert jsonfmt.dumps(jsonfmt.loads('{"n": 100000000000000000000}')) == '{"n":1e+20}'
    assert jsonfmt.loads(b'{"a": 1, "a": 2}') == {"a": 2}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Written by the earlier Rust build into a media snapshot.
        (1e15, "1000000000000000.0"),
        (1.2345e16, "1.2345e+16"),
        (1e17, "1e+17"),
        (1e100, "1e+100"),
        (1.7976931348623157e308, "1.7976931348623157e+308"),
        (-1e20, "-1e+20"),
        (1.2345678901234568e29, "1.2345678901234568e+29"),
        (1e-5, "0.00001"),
        (1e-6, "1e-6"),
        (-1.5e-7, "-1.5e-7"),
        (5e-324, "5e-324"),
    ],
)
def test_floats_keep_the_stored_format(value: float, expected: str) -> None:
    assert jsonfmt.dumps(value) == expected
