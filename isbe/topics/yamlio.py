"""YAML for topic files and memory frontmatter.

PyYAML's SafeLoader follows YAML 1.1, where `yes`, `off` or `2026-10-16` stop being strings, so keyword
lists, labels and ids would silently change. Plain scalars are resolved with
the YAML 1.2 core schema instead (only `true`/`false` are booleans, no
timestamps), duplicate keys are errors, and `<<` merge keys work as they did
before. Typed fields go through `decode`, which keeps the source text of
each scalar; this module covers the untyped view and writing.
"""

import math
import re
from collections.abc import Callable, Iterator
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

STR = "tag:yaml.org,2002:str"
INT = "tag:yaml.org,2002:int"
FLOAT = "tag:yaml.org,2002:float"
BOOL = "tag:yaml.org,2002:bool"
NULL = "tag:yaml.org,2002:null"
MERGE = "tag:yaml.org,2002:merge"
_AMBIGUOUS = "tag:isbe,2026:ambiguous"

# A 64 KiB file cannot hold more nodes than this without aliases; the cap
# stops alias bombs, the depth limit stops recursive aliases.
MAX_NODES = 200_000
MAX_DEPTH = 100

_INT12 = re.compile(r"[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+")
_FLOAT12 = re.compile(r"[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)")
# Strings any reader might type differently when written plain (YAML 1.1,
# case-insensitive booleans and nulls, `1_000`, `12:30`, ...).
_RISKY = re.compile(r"(?i:y|yes|n|no|true|false|on|off|null|~|<<|=)|[-+.]?[0-9].*|[-+]?\.[a-zA-Z].*", re.S)


class YamlError(ValueError):
    """Unreadable YAML; the message carries the line and column when known."""


def at(node: Node | None, message: str) -> YamlError:
    if node is None:
        return YamlError(message)
    mark = node.start_mark
    return YamlError(f"line {mark.line + 1} column {mark.column + 1}: {message}")


class _Loader(yaml.SafeLoader):
    """Composes nodes with YAML 1.2 core resolution and remembers explicit tags."""

    def compose_scalar_node(self, anchor: Any) -> ScalarNode:
        event = self.peek_event()
        node = super().compose_scalar_node(anchor)
        node.explicit = event.tag not in (None, "!")  # type: ignore[attr-defined]
        return node


_CORE = (
    (NULL, re.compile(r"(?:~|null|Null|NULL|)\Z"), ["~", "n", "N", ""]),
    (BOOL, re.compile(r"(?:true|True|TRUE|false|False|FALSE)\Z"), list("tTfF")),
    (INT, re.compile(rf"(?:{_INT12.pattern})\Z"), list("-+0123456789")),
    (FLOAT, re.compile(rf"(?:{_FLOAT12.pattern})\Z"), list("-+.0123456789")),
    (MERGE, re.compile(r"<<\Z"), ["<"]),
)
_Loader.yaml_implicit_resolvers = {}
for _tag, _regexp, _first in _CORE:
    _Loader.add_implicit_resolver(_tag, _regexp, _first)


def resolve(value: str) -> str:
    """The YAML 1.2 core tag of a plain scalar."""
    for tag, regexp, _ in _CORE:
        if regexp.match(value):
            return tag
    return STR


def compose(text: str) -> Node | None:
    """The single document of `text` as a node graph; `None` for an empty stream."""
    try:
        return yaml.compose(text, Loader=_Loader)
    except yaml.MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        problem = "; ".join(p for p in (e.context, e.problem) if p)
        if mark is None:
            raise YamlError(problem) from None
        raise YamlError(f"line {mark.line + 1} column {mark.column + 1}: {problem}") from None
    except yaml.YAMLError as e:
        raise YamlError(str(e)) from None
    except RecursionError:
        raise YamlError("document is nested too deeply") from None


def text(node: ScalarNode) -> str:
    """Scalar text, with JSON-style `\\uD83D\\uDE00` escape pairs joined."""
    value = node.value
    if any("\ud800" <= c <= "\udfff" for c in value):
        try:
            value = value.encode("utf-16", "surrogatepass").decode("utf-16")
        except UnicodeDecodeError:
            raise at(node, "invalid surrogate in escape sequence") from None
    return value


def explicit(node: ScalarNode) -> bool:
    return getattr(node, "explicit", False)


def nullish(node: Node) -> bool:
    """Null for typed fields: plain empty, `~` or any-case `null`."""
    if not isinstance(node, ScalarNode):
        return False
    if explicit(node):
        return node.tag == NULL
    return node.style is None and (node.value in ("", "~") or node.value.lower() == "null")


class Budget:
    """Counts visited nodes so aliases cannot expand without bound."""

    def __init__(self) -> None:
        self.nodes = 0

    def visit(self, node: Node, depth: int) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise at(node, "document expands to too many nodes")
        if depth > MAX_DEPTH:
            raise at(node, "document is nested too deeply")


def pairs(node: MappingNode, key: Callable[[Node], str], budget: Budget, depth: int) -> Iterator[tuple[str, Node, Node]]:
    """Mapping entries with `<<` merges applied (explicit keys win, earlier
    merged mappings win over later ones) and duplicate keys rejected."""
    own: list[tuple[str, Node, Node]] = []
    merged: list[tuple[str, Node, Node]] = []
    for knode, vnode in node.value:
        if isinstance(knode, ScalarNode) and knode.tag == MERGE:
            sources = vnode.value if isinstance(vnode, SequenceNode) else [vnode]
            for source in sources:
                if not isinstance(source, MappingNode):
                    raise at(source, "merge value must be a mapping or a list of mappings")
                budget.visit(source, depth + 1)
                merged.extend(pairs(source, key, budget, depth + 1))
            continue
        own.append((key(knode), knode, vnode))
    seen: set[str] = set()
    for name, knode, vnode in own:
        if name in seen:
            raise at(knode, f"duplicate mapping key: {name}")
        seen.add(name)
        yield name, knode, vnode
    for name, knode, vnode in merged:
        if name not in seen:
            seen.add(name)
            yield name, knode, vnode


def _key(node: Node) -> str:
    if not isinstance(node, ScalarNode):
        raise at(node, "mapping keys must be scalars")
    if node.tag == NULL:
        raise at(node, "mapping keys must not be null")
    return text(node)


def _scalar(node: ScalarNode) -> Any:
    tag, value = node.tag, node.value
    if tag == NULL:
        return None
    if tag == BOOL:
        if value in ("true", "True", "TRUE"):
            return True
        if value in ("false", "False", "FALSE"):
            return False
        raise at(node, f"invalid boolean `{value}`")
    if tag == INT:
        number = _int12(value)
        if number is None:
            raise at(node, f"invalid integer `{value}`")
        # Integers within 64 bits stay integers; larger ones are read as floats.
        return number if -(2**63) <= number < 2**64 else float(number)
    if tag == FLOAT:
        # JSON has no infinities or NaN, so they are refused here too.
        try:
            real = float(value) if value.lstrip("+-").lower() not in (".inf", ".nan") else math.inf
        except ValueError:
            raise at(node, f"invalid float `{value}`") from None
        if not math.isfinite(real):
            raise at(node, f"non-finite float `{value}` cannot be represented in JSON")
        return real
    if tag in (STR, MERGE) or not tag.startswith("tag:yaml.org,2002:"):
        # `<<` is only special as a key; local tags (`!x`) keep their text.
        return text(node)
    raise at(node, f"unsupported YAML tag {tag}")


def _int12(value: str) -> int | None:
    if not _INT12.fullmatch(value):
        return None
    if value.startswith("0o"):
        return int(value[2:], 8)
    if value.startswith("0x"):
        return int(value[2:], 16)
    return int(value, 10)


def _plain(node: Node, budget: Budget, depth: int) -> Any:
    budget.visit(node, depth)
    if isinstance(node, ScalarNode):
        return _scalar(node)
    if isinstance(node, SequenceNode):
        return [_plain(item, budget, depth + 1) for item in node.value]
    if isinstance(node, MappingNode):
        return {k: _plain(v, budget, depth + 1) for k, _, v in pairs(node, _key, budget, depth)}
    raise at(node, "unsupported node")


def load_value(source: str) -> Any:
    """The document as JSON-compatible values (`null` for an empty stream)."""
    node = compose(source)
    return None if node is None else _plain(node, Budget(), 0)


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True

    def resolve(self, kind: type[Node], value: Any, implicit: tuple[bool, bool]) -> str:
        if kind is ScalarNode and implicit[0]:
            # Real ints, floats, booleans and nulls are written in YAML 1.2
            # core form; a string that any reader might take for one of them
            # resolves to a tag no string has, which forces quotes.
            tag = resolve(value)
            if tag == STR and _RISKY.fullmatch(value):
                return _AMBIGUOUS
            return tag
        return super().resolve(kind, value, implicit)


def _represent_str(dumper: _Dumper, data: str) -> ScalarNode:
    # Double quotes are unambiguous for line breaks in every YAML version.
    breaks = any(c in data for c in "\n\r\x85\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}")
    return dumper.represent_scalar(STR, data, style='"' if breaks else None)


def _represent_float(dumper: _Dumper, data: float) -> ScalarNode:
    if not math.isfinite(data):
        raise YamlError("non-finite numbers cannot be written")
    return yaml.SafeDumper.represent_float(dumper, data)


_Dumper.add_representer(str, _represent_str)
_Dumper.add_representer(float, _represent_float)


def dump(value: Any) -> str:
    """Block-style YAML for a JSON value that `load_value` reads back unchanged.

    Keys are sorted at every level, so console-written topic files list keys in
    a stable order.
    """
    try:
        return yaml.dump(value, Dumper=_Dumper, allow_unicode=True, sort_keys=True, default_flow_style=False, width=2**31)
    except yaml.YAMLError as e:
        raise YamlError(str(e)) from None


_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def quote(value: str) -> str:
    """A double-quoted scalar that YAML 1.1 and 1.2 readers decode to `value`."""
    out = []
    for c in value:
        if c in _ESCAPES:
            out.append(_ESCAPES[c])
        elif not c.isprintable():
            out.append(f"\\x{ord(c):02x}" if ord(c) < 0x100 else f"\\u{ord(c):04x}" if ord(c) < 0x10000 else f"\\U{ord(c):08x}")
        else:
            out.append(c)
    return '"' + "".join(out) + '"'
