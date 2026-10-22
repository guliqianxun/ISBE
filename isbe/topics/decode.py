"""Typed decoding of YAML into pydantic models, accepting what topic files
written for the earlier Rust build use.

Each field is read from the scalar's source text, so `label: 2026-10-16` stays
the string it was written as and `window_days: '7'` is still the number 7.
Booleans accept the YAML 1.1 spellings (`yes`/`no`/`on`/`off`/`y`/`n`,
any case), so existing topic files keep loading; the untyped view in
`yamlio` stays YAML 1.2. Unknown keys fail unless the model ignores extras.
"""

import re
import types
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from . import yamlio
from .yamlio import Budget, YamlError, at


@dataclass(frozen=True)
class UInt:
    """Marks an int field with the bit width of the unsigned integer it holds."""

    bits: int


U32 = UInt(32)
U64 = UInt(64)

_DIGITS = re.compile(r"[0-9a-fA-F]+(?:_[0-9a-fA-F]+)*")
_TRUE = frozenset({"true", "yes", "y", "on"})
_FALSE = frozenset({"false", "no", "n", "off"})
_TAGGED = frozenset({yamlio.INT, yamlio.FLOAT, yamlio.BOOL, "tag:yaml.org,2002:timestamp", "tag:yaml.org,2002:binary"})
_CORE_TAGS = _TAGGED | {yamlio.STR, yamlio.NULL}


def parse_bool(value: str) -> bool | None:
    """Typed boolean: YAML 1.1 spellings, case-insensitive."""
    word = value.strip().lower()
    return True if word in _TRUE else False if word in _FALSE else None


def parse_uint(value: str) -> int | None:
    """Typed unsigned integer: optional `+`, `0x`/`0o`/`0b`
    prefixes, single `_` between digits, no leading zeros in decimal."""
    t = value.strip()
    if t.startswith("-"):
        return None
    t = t.removeprefix("+")
    radix, digits = 10, t
    for prefix, base in (("0x", 16), ("0o", 8), ("0b", 2)):
        if t[:2].lower() == prefix:
            radix, digits = base, t[2:]
            break
    if radix == 10 and digits.startswith("0") and digits != "0":
        return None
    if not _DIGITS.fullmatch(digits):
        return None
    try:
        return int(digits.replace("_", ""), radix)
    except ValueError:
        return None


def decode[M: BaseModel](source: str, model: type[M]) -> M:
    node = yamlio.compose(source)
    if node is None:
        raise YamlError("line 1 column 1: unexpected end of input")
    return _model(node, model, Budget(), 0)


def _scalar(node: Node) -> ScalarNode:
    if not isinstance(node, ScalarNode):
        raise at(node, "expected string scalar")
    return node


def _str(node: Node) -> str:
    node = _scalar(node)
    if yamlio.nullish(node):
        raise at(node, "cannot deserialize null into string; use Option<String>")
    if yamlio.explicit(node) and node.tag in _TAGGED:
        raise at(node, "cannot deserialize tagged scalar into string")
    return yamlio.text(node)


def _typed_text(node: Node, tag: str) -> str | None:
    """The text of a scalar that may hold a `tag` value: not null, and not
    explicitly tagged as another core type (`!!str 7` is no number)."""
    node = _scalar(node)
    if yamlio.nullish(node) or (yamlio.explicit(node) and node.tag != tag and node.tag in _CORE_TAGS):
        return None
    return node.value


def _bool(node: Node) -> bool:
    text = _typed_text(node, yamlio.BOOL)
    value = parse_bool(text) if text is not None else None
    if value is None:
        raise at(node, "invalid boolean")
    return value


def _uint(node: Node, width: UInt) -> int:
    text = _typed_text(node, yamlio.INT)
    value = parse_uint(text) if text is not None else None
    if value is None or value >= 2**width.bits:
        raise at(node, f"invalid u{width.bits}")
    return value


def _shown(value: str) -> str:
    """A variant name for error messages: control characters are escaped,
    everything else verbatim."""
    names = {"\t": "\\t", "\r": "\\r", "\n": "\\n"}
    return "".join(names.get(c, f"\\u{{{ord(c):x}}}") if unicodedata.category(c) == "Cc" else c for c in value)


def _enum[E: Enum](node: Node, kind: type[E]) -> E:
    variants = {str(m.value): m for m in kind}
    unknown = "unknown variant `{}`, expected one of " + ", ".join(variants)
    if isinstance(node, SequenceNode):
        raise at(node, "externally tagged enum expected scalar or mapping")
    if isinstance(node, MappingNode):
        # An externally tagged enum also reads `{variant: value}`; a unit variant takes only null.
        if not node.value or not isinstance(node.value[0][0], ScalarNode):
            raise at(node, "expected string key for externally tagged enum")
        (knode, vnode), *rest = node.value
        name = yamlio.text(knode)
        if name not in variants:
            raise at(knode, unknown.format(_shown(name)))
        if not yamlio.nullish(vnode):
            raise at(vnode, "unexpected value for unit enum variant")
        if rest:
            raise at(rest[0][0], "expected end of mapping after enum variant value")
        return variants[name]
    assert isinstance(node, ScalarNode)
    value = yamlio.text(node)
    if value in variants:
        return variants[value]
    plain_empty = not value and node.style is None and not yamlio.explicit(node)
    raise at(node, unknown.format("~" if plain_empty else _shown(value)))


def _value(node: Node, kind: Any, info: FieldInfo | None, budget: Budget, depth: int) -> Any:
    budget.visit(node, depth)
    origin = get_origin(kind)
    if origin in (Union, types.UnionType):
        if yamlio.nullish(node):
            return None
        (kind,) = (a for a in get_args(kind) if a is not type(None))
        origin = get_origin(kind)
    if kind is str:
        return _str(node)
    if kind is bool:
        return _bool(node)
    if kind is int:
        width = next((m for m in (info.metadata if info else ()) if isinstance(m, UInt)), U64)
        return _uint(node, width)
    if origin is list:
        if yamlio.nullish(node):
            return []
        if not isinstance(node, SequenceNode):
            raise at(node, "expected sequence start")
        (item,) = get_args(kind)
        return [_value(n, item, None, budget, depth + 1) for n in node.value]
    if isinstance(kind, type) and issubclass(kind, Enum):
        return _enum(node, kind)
    if isinstance(kind, type) and issubclass(kind, BaseModel):
        return _model(node, kind, budget, depth)
    raise TypeError(f"no YAML decoding for {kind!r}")


def _model[M: BaseModel](node: Node, model: type[M], budget: Budget, depth: int) -> M:
    budget.visit(node, depth)
    fields = {info.alias or name: info for name, info in model.model_fields.items()}
    values: dict[str, Any] = {}
    if not yamlio.nullish(node):
        if not isinstance(node, MappingNode):
            raise at(node, "expected mapping start")
        for key, knode, vnode in yamlio.pairs(node, _str, budget, depth):
            if key not in fields:
                if model.model_config.get("extra") == "forbid":
                    raise at(knode, f"unknown field `{key}`, expected one of {', '.join(fields)}")
                continue
            info = fields[key]
            values[key] = _value(vnode, info.annotation, info, budget, depth + 1)
    for key, info in fields.items():
        if key not in values and info.is_required():
            raise at(node, f"missing field `{key}`")
    try:
        return model.model_validate(values)
    except ValidationError as e:
        raise at(node, str(e)) from None
