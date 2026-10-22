"""The `/api/sources*` request bodies, decoded with serde's strict rules and messages.

Every body is decoded into a struct that refuses unknown members, and a
refusal answers 400 `"Invalid source request: <message>"`. The messages are
serde's, as in the earlier Rust build, because the console shows them as
they are: object members are visited in sorted key order (a `Value` map), an
unknown member or a wrong type fails at the first such member, missing fields
are reported in declaration order (an optional field may be missing), and a
struct may also be given as an array of its fields.
"""

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from isbe.core import jsonfmt
from isbe.search import SearchPlan
from isbe.sources.registry import Source, SourceRevision

from .wire import Request, Respond, error, request_json


class Refused(ValueError):
    """A value the strict decoding refuses; the message follows serde's wording."""


STR, BOOL, USIZE = "a string", "a boolean", "usize"


@dataclass(frozen=True)
class Opt:
    item: Spec


@dataclass(frozen=True)
class Seq:
    item: Spec


@dataclass(frozen=True)
class Enum:
    """One of a fixed set of lowercase names."""

    variants: tuple[str, ...]


@dataclass(frozen=True)
class Field:
    name: str
    spec: Spec
    # Used when the member is absent.
    default: Any = None
    has_default: bool = False


@dataclass(frozen=True)
class Struct:
    name: str
    fields: tuple[Field, ...]


type Spec = str | Opt | Seq | Enum | Struct


# Unicode's Other_Grapheme_Extend: combining characters outside categories Mn and Me.
_EXTEND = frozenset(
    [0x09BE, 0x09D7, 0x0B3E, 0x0B57, 0x0BBE, 0x0BD7, 0x0CC2, 0x0CD5, 0x0CD6, 0x0D3E, 0x0D57, 0x0DCF,
     0x0DDF, 0x1B35, 0x200C, 0x302E, 0x302F, 0xFF9E, 0xFF9F, 0x1133E, 0x11357, 0x114B0, 0x114BD,
     0x115AF, 0x11930, 0x1D165, *range(0x1D16E, 0x1D173), *range(0x1F3FB, 0x1F400), *range(0xE0020, 0xE0080)]
)


def _escaped(c: str) -> bool:
    """Whether a quoted string shows `c` as a `\\u{...}` escape: grapheme extenders and unprintable characters."""
    return ord(c) in _EXTEND or unicodedata.category(c) in ("Mn", "Me") or not c.isprintable()


def _debug(text: str) -> str:
    """A string quoted for a message: backslash escapes, `\\u{...}` for invisible characters."""
    out = []
    for c in text:
        if c in '"\\':
            out.append("\\" + c)
        elif c in "\n\r\t\0":
            out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t", "\0": "\\0"}[c])
        elif _escaped(c):
            out.append(f"\\u{{{ord(c):x}}}")
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def unexpected(value: Any) -> str:
    """How a message names the unexpected value."""
    match value:
        case None:
            return "null"
        case bool():
            return f"boolean `{'true' if value else 'false'}`"
        case int():
            return f"integer `{value}`"
        case float():
            # The float as JSON text.
            return f"floating point `{jsonfmt.dumps(value)}`"
        case str():
            return f"string {_debug(value)}"
        case list():
            return "sequence"
    return "map"


def _invalid_type(value: Any, expected: str) -> Refused:
    return Refused(f"invalid type: {unexpected(value)}, expected {expected}")


def _one_of(names: Sequence[str]) -> str:
    quoted = [f"`{name}`" for name in names]
    if len(quoted) == 1:
        return quoted[0]
    if len(quoted) == 2:
        return f"{quoted[0]} or {quoted[1]}"
    return "one of " + ", ".join(quoted)


def decode(spec: Spec, value: Any) -> Any:
    """`value` decoded into the type `spec` describes, with serde's rules and messages."""
    match spec:
        case Opt(item):
            return None if value is None else decode(item, value)
        case Seq(item):
            if not isinstance(value, list):
                raise _invalid_type(value, "a sequence")
            return [decode(item, v) for v in value]
        case Enum():
            return _enum(spec, value)
        case Struct():
            return _struct(spec, value)
        case _ if spec == STR:
            if not isinstance(value, str):
                raise _invalid_type(value, STR)
            return value
        case _ if spec == BOOL:
            if not isinstance(value, bool):
                raise _invalid_type(value, BOOL)
            return value
    # A non-negative count, named `usize` in messages: a JSON integer; floats and booleans are never converted.
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 0:
            raise Refused(f"invalid value: integer `{value}`, expected usize")
        return value
    raise _invalid_type(value, USIZE)


def _enum(spec: Enum, value: Any) -> str:
    variant: str
    content: Any
    if isinstance(value, dict):
        if len(value) != 1:
            raise Refused("invalid value: map, expected map with a single key")
        ((variant, content),) = value.items()
    elif isinstance(value, str):
        variant, content = value, None
    else:
        raise _invalid_type(value, "string or map")
    if variant not in spec.variants:
        raise Refused(f"unknown variant `{variant}`, expected {_one_of(spec.variants)}")
    if content is not None:
        raise _invalid_type(content, "unit")
    return variant


def _struct(spec: Struct, value: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, list):
        count = len(spec.fields)
        expected = f"struct {spec.name} with {count} element{'' if count == 1 else 's'}"
        for i, field in enumerate(spec.fields):
            if i < len(value):
                out[field.name] = decode(field.spec, value[i])
            elif field.has_default:
                out[field.name] = field.default
            else:
                raise Refused(f"invalid length {i}, expected {expected}")
        if len(value) > count:
            raise Refused(f"invalid length {len(value)}, expected fewer elements in array")
        return out
    if not isinstance(value, dict):
        raise _invalid_type(value, f"struct {spec.name}")
    fields = {field.name: field for field in spec.fields}
    for key, item in sorted(value.items()):
        known = fields.get(key)
        if known is None:
            names = [field.name for field in spec.fields]
            raise Refused(f"unknown field `{key}`, expected {_one_of(names)}")
        out[key] = decode(known.spec, item)
    for field in spec.fields:
        if field.name not in out:
            if field.has_default:
                out[field.name] = field.default
            elif isinstance(field.spec, Opt):
                out[field.name] = None
            else:
                raise Refused(f"missing field `{field.name}`")
    return out


def _required(*names: str, spec: Spec = STR) -> tuple[Field, ...]:
    return tuple(Field(name, spec) for name in names)


SOURCE = Struct(
    "Source",
    (
        *_required("id", "name", "topic"),
        Field("kind", Enum(("rss", "page"))),
        Field("url", STR),
        Field("enabled", BOOL, True, True),
        Field("link_pattern", STR, "", True),
        Field("max_items", USIZE, 100, True),
    ),
)
SOURCE_REVISION = Struct("SourceRevision", _required("id", "revision"))
SEARCH_PLAN = Struct(
    "SearchPlan",
    (
        *_required("id", "name", "topic"),
        Field("provider", Enum(("baidu", "searxng"))),
        Field("queries", Seq(STR)),
        *_required("start_date", "end_date", "language"),
        Field("max_results", USIZE),
        Field("region", STR, "", True),
        Field("institution", STR, "", True),
    ),
)

# The request structs, in declaration order (the order missing fields are reported in).
SAVE = Struct("Save", (Field("config", SOURCE), Field("revision", Opt(STR))))
IMPORT = Struct("Import", (Field("sources", Seq(SOURCE)),))
BATCH = Struct("Batch", (Field("sources", Seq(SOURCE_REVISION)), Field("enabled", BOOL)))
COLLECT = Struct("Collect", _required("request_id"))
DISCOVER = Struct(
    "Discover",
    (Field("url", STR), Field("topic", STR), Field("auto_enable", BOOL), Field("request_id", STR)),
)
SAVE_SEARCH = Struct("SaveSearch", (Field("config", SEARCH_PLAN), Field("revision", Opt(STR))))
RUN_SEARCH = Struct("RunSearch", (Field("config", SEARCH_PLAN), Field("request_id", STR)))
RECHECK = Struct("Recheck", (Field("request_id", STR), Field("after", Opt(STR))))
WEEKLY = Struct("Weekly", _required("topic_id", "week_end"))
ARTICLE = Struct("Article", _required("topic", "period", "id"))


def invalid(message: str) -> Respond:
    return Respond(error(400, f"Invalid source request: {message}"))


def body(req: Request, spec: Struct) -> dict[str, Any]:
    """The request body decoded into `spec`'s fields, or `Respond` with its 400/413 answer."""
    try:
        return _struct(spec, request_json(req))
    except Refused as refused:
        raise invalid(str(refused)) from None


def source(value: dict[str, Any]) -> Source:
    return _model(Source, value)


def source_revision(value: dict[str, Any]) -> SourceRevision:
    return _model(SourceRevision, value)


def search_plan(value: dict[str, Any]) -> SearchPlan:
    return _model(SearchPlan, value)


def _model[M: Source | SourceRevision | SearchPlan](model: type[M], value: dict[str, Any]) -> M:
    try:
        return model(**value)
    except ValidationError as failure:
        # Unreachable for decoded fields; the models' own checks still answer with the same 400.
        first = failure.errors()[0]
        raise invalid(str(first.get("msg", "invalid value"))) from None
