"""JSON exactly as existing data directories hold it.

Rows, files and hashes written by the earlier Rust build use compact
separators, object keys sorted, raw UTF-8, floats in shortest round-trip form
(`1e20`, `0.00001`, `1.5e-7`, `100.0`) and non-finite floats as `null`.
Anything stored or hashed goes through `dumps` so every version produces
identical bytes. `loads` applies the same input rules: no `NaN`/`Infinity`, no
out-of-range floats, and integers beyond 64 bits become floats.
"""

import json
import math
import re
from collections.abc import Mapping
from typing import Any

_ESCAPE = json.encoder.py_encode_basestring  # escapes only `"`, `\` and C0 controls
_REPR = re.compile(r"(-?)(\d+)(?:\.(\d+))?(?:e([+-]?\d+))?")
_I64_MIN = -(1 << 63)
_U64_MAX = (1 << 64) - 1


def _float(value: float) -> str:
    """Float text as stored data has it: shortest round-trip digits in this layout."""
    if not math.isfinite(value):
        return "null"
    if value == 0:
        return "-0.0" if math.copysign(1.0, value) < 0 else "0.0"
    sign, whole, frac, exp = _REPR.fullmatch(repr(value)).groups()  # type: ignore[union-attr]
    digits = (whole + (frac or "")).lstrip("0")
    exponent = int(exp or 0) - len(frac or "")
    stripped = digits.rstrip("0")
    exponent += len(digits) - len(stripped)
    digits = stripped
    length = len(digits)
    kk = length + exponent  # 10^(kk-1) <= |value| < 10^kk
    if exponent >= 0 and kk <= 16:
        text = digits + "0" * exponent + ".0"
    elif 0 < kk <= 16:
        text = digits[:kk] + "." + digits[kk:]
    elif -5 < kk <= 0:
        text = "0." + "0" * -kk + digits
    else:
        # Positive exponents are signed, as in stored data: 1e+20, 1e-7.
        power = f"e+{kk - 1}" if kk > 1 else f"e{kk - 1}"
        text = (digits if length == 1 else f"{digits[0]}.{digits[1:]}") + power
    return sign + text


def _encode(value: Any, out: list[str], sort_keys: bool, indent: str | None, level: int) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        out.append(str(int(value)))
    elif isinstance(value, float):
        out.append(_float(value))
    elif isinstance(value, str):
        out.append(_ESCAPE(value))
    elif isinstance(value, Mapping):
        items = list(value.items())
        if any(not isinstance(k, str) for k, _ in items):
            raise TypeError("JSON object keys must be strings")
        if sort_keys:
            items.sort(key=lambda kv: kv[0])
        if not items:
            out.append("{}")
            return
        out.append("{")
        for i, (key, item) in enumerate(items):
            if i:
                out.append(",")
            if indent is not None:
                out.append("\n" + indent * (level + 1))
            out.append(_ESCAPE(key))
            out.append(": " if indent is not None else ":")
            _encode(item, out, sort_keys, indent, level + 1)
        if indent is not None:
            out.append("\n" + indent * level)
        out.append("}")
    elif isinstance(value, (list, tuple)):
        if not value:
            out.append("[]")
            return
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            if indent is not None:
                out.append("\n" + indent * (level + 1))
            _encode(item, out, sort_keys, indent, level + 1)
        if indent is not None:
            out.append("\n" + indent * level)
        out.append("]")
    else:
        raise TypeError(f"{type(value).__name__} is not JSON serializable")


def dumps(value: Any, *, sort_keys: bool = True) -> str:
    """Compact JSON. Pass `sort_keys=False` for a model, whose fields serialize
    in declaration order."""
    out: list[str] = []
    _encode(value, out, sort_keys, None, 0)
    return "".join(out)


def dumps_pretty(value: Any, *, sort_keys: bool = True) -> str:
    """Pretty JSON: two-space indent, `": "` after keys."""
    out: list[str] = []
    _encode(value, out, sort_keys, "  ", 0)
    return "".join(out)


def _constant(name: str) -> Any:
    raise ValueError(f"invalid JSON value {name}")


def _number(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("number out of range")
    return value


def _integer(text: str) -> int | float:
    value = int(text)
    return value if _I64_MIN <= value <= _U64_MAX else _number(text)


def loads(text: str | bytes) -> Any:
    """Parse JSON; raises `ValueError` on anything the input rules reject."""
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    return json.loads(text, parse_constant=_constant, parse_float=_number, parse_int=_integer)
