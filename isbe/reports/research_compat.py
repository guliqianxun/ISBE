"""Strict decoding shared by the research and briefing modules.

Manifests, bundles and model output are decoded into typed records, most of
them rejecting unknown members, and dates are parsed with a fixed grammar.
Python's `json` and `datetime` accept a different language (duplicate keys,
booleans as integers, `2024-1-1` refused, years past 9999 refused), so the rules
these inputs rely on are written out here: the same documents are accepted
and rejected as by the earlier Rust build that wrote the stored bundles, with
the same messages (without a line and column suffix).

Two decoding modes. `streamed=True` reads parsed JSON text: object members
are visited in document order and a repeated member is an error, so parse with
`loads`, which keeps them. `streamed=False` reads an already-decoded value
whose objects are sorted maps without duplicates.
"""

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from isbe.core import jsonfmt, validators

_I64_MIN, _I64_MAX, _U64_MAX = -(1 << 63), (1 << 63) - 1, (1 << 64) - 1


class DecodeError(ValueError):
    """A value the strict decoder rejects; the message says why."""


class Obj(dict[str, Any]):
    """A JSON object that also keeps its members in document order, repeats included."""

    pairs: list[tuple[str, Any]]

    @classmethod
    def of(cls, pairs: list[tuple[str, Any]]) -> Obj:
        obj = cls(pairs)
        obj.pairs = pairs
        return obj


def _constant(name: str) -> Any:
    raise ValueError(f"invalid JSON value {name}")


def _float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("number out of range")
    return value


def _int(text: str) -> int | float:
    value = int(text)
    return value if _I64_MIN <= value <= _U64_MAX else _float(text)


def loads(text: str) -> Any:
    """`jsonfmt.loads` with objects as `Obj`, for `decode(..., streamed=True)`."""
    return json.loads(
        text, object_pairs_hook=Obj.of, parse_constant=_constant, parse_float=_float,
        parse_int=_int,
    )


# --- typed decoding -------------------------------------------------------------------

STR = "a string"
USIZE = "usize"


@dataclass(frozen=True)
class Seq:
    item: Spec


@dataclass(frozen=True)
class Opt:
    item: Spec


@dataclass(frozen=True)
class Field:
    name: str
    spec: Spec
    # The value used when the member is absent.
    default: Callable[[], Any] | None = None


@dataclass(frozen=True)
class Struct:
    name: str
    # Builds the record from the decoded members, keyed by field name.
    make: Callable[[dict[str, Any]], Any]
    fields: Sequence[Field]
    deny_unknown: bool = True


type Spec = str | Seq | Opt | Struct


def _debug(value: str) -> str:
    """A string quoted and escaped as in decoder messages (`\\n`, `\\u{..}`)."""
    out = []
    for c in value:
        match c:
            case "\\" | '"':
                out.append("\\" + c)
            case "\n":
                out.append("\\n")
            case "\r":
                out.append("\\r")
            case "\t":
                out.append("\\t")
            case "\0":
                out.append("\\0")
            case _ if not c.isprintable():
                out.append(f"\\u{{{ord(c):x}}}")
            case _:
                out.append(c)
    return '"' + "".join(out) + '"'


def _unexpected(value: Any) -> str:
    """How a decoded value is described in a type-mismatch message."""
    match value:
        case None:
            return "null"
        case bool():
            return f"boolean `{'true' if value else 'false'}`"
        case int():
            return f"integer `{value}`"
        case float():
            return f"floating point `{jsonfmt.dumps(value)}`"
        case str():
            return f"string {_debug(value)}"
        case list():
            return "sequence"
    return "map"


def _invalid_type(value: Any, expected: str) -> DecodeError:
    return DecodeError(f"invalid type: {_unexpected(value)}, expected {expected}")


def _one_of(names: Sequence[str]) -> str:
    quoted = [f"`{name}`" for name in names]
    if len(quoted) == 1:
        return quoted[0]
    if len(quoted) == 2:
        return f"{quoted[0]} or {quoted[1]}"
    return "one of " + ", ".join(quoted)


def decode(spec: Spec, value: Any, *, streamed: bool) -> Any:
    """Decode `value` into the type `spec` describes."""
    match spec:
        case str():
            return _scalar(spec, value)
        case Opt(item):
            return None if value is None else decode(item, value, streamed=streamed)
        case Seq(item):
            if not isinstance(value, list):
                raise _invalid_type(value, "a sequence")
            return [decode(item, v, streamed=streamed) for v in value]
        case Struct():
            return _struct(spec, value, streamed)
    raise TypeError(f"unknown spec {spec!r}")


def _scalar(spec: str, value: Any) -> Any:
    if spec == STR:
        if not isinstance(value, str):
            raise _invalid_type(value, STR)
        if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise DecodeError("lone leading surrogate in hex escape")
        return value
    # usize: a JSON integer within 0..2**64-1; floats and booleans are never converted.
    if isinstance(value, int) and not isinstance(value, bool):
        if not 0 <= value <= _U64_MAX:
            raise DecodeError(f"invalid value: integer `{value}`, expected usize")
        return value
    raise _invalid_type(value, USIZE)


def _struct(spec: Struct, value: Any, streamed: bool) -> Any:
    fields = {field.name: field for field in spec.fields}
    out: dict[str, Any] = {}
    if isinstance(value, list):
        # A record also accepts its fields as an array, in order.
        for i, field in enumerate(spec.fields):
            if i < len(value):
                out[field.name] = decode(field.spec, value[i], streamed=streamed)
            elif field.default is not None:
                out[field.name] = field.default()
            else:
                raise DecodeError(
                    f"invalid length {i}, expected struct {spec.name} "
                    f"with {len(spec.fields)} elements"
                )
        if len(value) > len(spec.fields):
            raise DecodeError(
                "trailing characters"
                if streamed
                else f"invalid length {len(value)}, expected fewer elements in array"
            )
        return spec.make(out)
    if not isinstance(value, dict):
        raise _invalid_type(value, f"struct {spec.name}")
    members = value.pairs if streamed and isinstance(value, Obj) else sorted(value.items())
    for key, item in members:
        known = fields.get(key)
        if known is None:
            if spec.deny_unknown:
                raise DecodeError(f"unknown field `{key}`, expected {_one_of(list(fields))}")
            continue
        if key in out:
            raise DecodeError(f"duplicate field `{key}`")
        out[key] = decode(known.spec, item, streamed=streamed)
    for field in spec.fields:
        if field.name not in out:
            if field.default is not None:
                out[field.name] = field.default()
            elif isinstance(field.spec, Opt):
                out[field.name] = None
            else:
                raise DecodeError(f"missing field `{field.name}`")
    return spec.make(out)


def utf8(data: bytes) -> str:
    """Decode UTF-8; a failure names the offset and, if invalid, the length."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        if error.reason == "unexpected end of data":
            message = f"incomplete utf-8 byte sequence from index {error.start}"
        else:
            message = (
                f"invalid utf-8 sequence of {error.end - error.start} bytes "
                f"from index {error.start}"
            )
        raise DecodeError(message) from None


# --- dates ---------------------------------------------------------------------

# The supported years: one inside what a 32-bit packed date holds.
_MIN_YEAR, _MAX_YEAR = (-(1 << 31) >> 13) + 1, (((1 << 31) - 1) >> 13) - 1
_OUT_OF_RANGE = "input is out of range"
_INVALID = "input contains invalid characters"
_TOO_SHORT = "premature end of input"
_TOO_LONG = "trailing input"

type Date = tuple[int, int, int]


def _trim_start(s: str) -> str:
    i = 0
    while i < len(s) and validators.is_space(s[i]):
        i += 1
    return s[i:]


def _number(s: str, width: int | None) -> tuple[str, int]:
    """1..=width leading ASCII digits as an integer (within 64 bits), and the rest."""
    if not s:
        raise DecodeError(_TOO_SHORT)
    n, i = 0, 0
    while i < len(s) and (width is None or i < width) and s[i] in "0123456789":
        n = n * 10 + ord(s[i]) - 48
        if n > _I64_MAX:
            raise DecodeError(_OUT_OF_RANGE)
        i += 1
    if i == 0:
        raise DecodeError(_INVALID)
    return s[i:], n


def _numeric(s: str, width: int, signed: bool) -> tuple[str, int]:
    s = _trim_start(s)
    if signed and s[:1] in ("-", "+"):
        rest, value = _number(s[1:], None)
        return rest, -value if s[0] == "-" else value
    return _number(s, width)


def _dash(s: str, spaces: bool) -> str:
    if spaces:
        s = _trim_start(s)
    if not s:
        raise DecodeError(_TOO_SHORT)
    if not s.startswith("-"):
        raise DecodeError(_INVALID)
    return s[1:]


def _leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def parse_date(value: str, *, spaces: bool = True) -> Date:
    """A `%Y-%m-%d` date; `spaces=False` is the strict form that allows no
    extra whitespace around `-`. Returns (year, month, day), which orders like
    the dates; raises `DecodeError` with the parser's message.

    Both forms take any whitespace before a number, zero padding, 1-2 digit
    months and days, and a signed year of any length; the default form also
    takes whitespace before each `-` and at the end.
    """
    s, year = _numeric(value, 4, True)
    if not -(1 << 31) <= year < (1 << 31):
        raise DecodeError(_OUT_OF_RANGE)
    s, month = _numeric(_dash(s, spaces), 2, False)
    if not 1 <= month <= 12:
        raise DecodeError(_OUT_OF_RANGE)
    s, day = _numeric(_dash(s, spaces), 2, False)
    if not 1 <= day <= 31:
        raise DecodeError(_OUT_OF_RANGE)
    if spaces:
        s = _trim_start(s)
    if s:
        raise DecodeError(_TOO_LONG)
    days = (31, 29 if _leap(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    if not _MIN_YEAR <= year <= _MAX_YEAR or day > days[month - 1]:
        raise DecodeError(_OUT_OF_RANGE)
    return year, month, day


def format_date(date: Date) -> str:
    """ISO 8601 `YYYY-MM-DD`, with a sign outside years 0-9999."""
    year, month, day = date
    head = f"{year:04d}" if 0 <= year <= 9999 else f"{year:+05d}"
    return f"{head}-{month:02d}-{day:02d}"
