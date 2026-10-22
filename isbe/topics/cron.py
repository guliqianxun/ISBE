"""Cron validation with the grammar of croner 4.0.1, the parser the earlier Rust build used.

Schedules are only validated here; nothing in ISBE computes run times from
them (an external scheduler or the operator's crontab runs the commands), so
a scheduling library is not needed. The Python libraries also disagree with
croner on what a valid pattern is (`0/15`, seven fields with a year, `5#L`,
`15W`, names inside ranges), and a topic accepted before must stay valid, so
this implements croner's grammar and its error messages directly.
"""

import re

# The Unicode White_Space property.
_SPACE = "\t\n\x0b\x0c\r \x85\xa0" + "".join(map(chr, (0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000)))
_SPLIT = re.compile(f"[{_SPACE}]+")
_U16 = re.compile(r"\+?[0-9]+")

_ALL, _LAST, _CLOSEST = 1, 1 << 6, 1 << 7
_NTH = (1 << 1, 1 << 2, 1 << 3, 1 << 4, 1 << 5)
_NTH_ALL = sum(_NTH)

_NICKNAMES = {
    "@YEARLY": "0 0 1 1 *",
    "@ANNUALLY": "0 0 1 1 *",
    "@MONTHLY": "0 0 1 * *",
    "@WEEKLY": "0 0 * * SUN",
    "@DAILY": "0 0 * * *",
    "@HOURLY": "0 * * * *",
}
_WEEKDAYS = (("-SUN", "-7"), ("SUN", "0"), ("MON", "1"), ("TUE", "2"), ("WED", "3"), ("THU", "4"), ("FRI", "5"), ("SAT", "6"))
_MONTHS = tuple((m, str(i)) for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1))
_BASE = frozenset("*/0123456789,-")
_ALLOWED = {3: _BASE | {"L", "W", "?"}, 5: _BASE | {"#", "L", "?"}}


class CronError(ValueError):
    pass


def _component(message: str) -> CronError:
    return CronError(f"Component error: {message}")


def _u16(value: str) -> int | None:
    if not _U16.fullmatch(value):
        return None
    number = int(value)
    return number if number <= 0xFFFF else None


class _Component:
    def __init__(self, low: int, high: int, features: int) -> None:
        self.low, self.high = low, high
        self.features = features | _ALL | _LAST

    def _allowed(self, feature: int) -> bool:
        return self.features & feature == feature

    def _enable(self, feature: int) -> None:
        if not self._allowed(feature):
            raise _component(f"Feature 0b{feature:08b} is not supported by the current features 0b{self.features:08b}.")

    def _set(self, position: int, bit: int) -> None:
        if position < self.low or position > self.high:
            raise _component(f"Position {position} is out of bounds for the current range ({self.low}-{self.high}).")
        if not self._allowed(bit):
            raise _component(f"Bit 0b{bit:08b} is not supported by the current features 0b{self.features:08b}.")

    def parse(self, field: str) -> None:
        if field == "*":
            return
        for part in field.split(","):
            part = part.strip(_SPACE)
            if not part:
                continue
            if "/" in part:
                self._stepping(part)
            elif "-" in part:
                self._range(part)
            elif "W" in part:
                self._closest_weekday(part)
            elif part == "L":
                self._enable(_LAST)
            else:
                if part.endswith("L") and "#" not in part:
                    part = part.replace("L", "#L")
                if "#" in part and not self._allowed(_NTH_ALL):
                    raise _component("Nth specifier # not allowed in the current field.")
                if "L" in part and not self._allowed(_NTH_ALL):
                    raise _component("L not allowed in the current field.")
                self._number(part)

    @staticmethod
    def _nth_bit(value: str) -> int:
        if value.endswith("L"):
            return _LAST
        if "#" not in value:
            return _ALL
        nth = _u16(value[value.index("#") + 1 :])
        if nth is None or nth > 0xFF:
            raise _component("Invalid nth specifier.")
        if not 1 <= nth <= 5:
            raise _component("Nth specifier out of bounds.")
        return _NTH[nth - 1]

    def _closest_weekday(self, value: str) -> None:
        day_text = value[: value.index("W")]
        if day_text == "L":
            self._enable(_LAST)
            self._enable(_CLOSEST)
            return
        day = _u16(day_text)
        if day is None:
            raise _component("Invalid day for closest weekday.")
        if day < self.low or day > self.high:
            raise _component("Day for closest weekday out of bounds.")
        self._set(day, _CLOSEST)

    def _range(self, value: str) -> None:
        bit = self._nth_bit(value)
        parts = [p.strip(_SPACE) for p in value.split("#")[0].split("-")]
        if len(parts) != 2:
            raise _component("Invalid range syntax.")
        start, end = _u16(parts[0]), _u16(parts[1])
        if start is None:
            raise _component("Invalid start of range.")
        if end is None:
            raise _component("Invalid end of range.")
        if start > end or start < self.low or end > self.high:
            raise _component("Range out of bounds.")
        self._set(start, bit)

    def _number(self, value: str) -> None:
        bit = self._nth_bit(value)
        number = _u16(value.split("#")[0])
        if number is None:
            raise _component("Invalid number.")
        if number < self.low or number > self.high:
            raise _component("Number out of bounds.")
        self._set(number, bit)

    def _stepping(self, value: str) -> None:
        bit = self._nth_bit(value)
        parts = value.split("#")[0].split("/")
        if len(parts) != 2:
            raise _component("Invalid stepped range syntax.")
        range_part, step_text = parts
        step = _u16(step_text)
        if step is None:
            raise _component("Invalid step.")
        if step == 0:
            raise _component("Step cannot be zero.")
        if range_part == "*":
            start, end = self.low, self.high
        elif "-" in range_part:
            bounds = range_part.split("-")
            if len(bounds) != 2:
                raise _component("Invalid range syntax in stepping.")
            first, last = _u16(bounds[0]), _u16(bounds[1])
            if first is None:
                raise _component("Invalid range start.")
            if last is None:
                raise _component("Invalid range end.")
            start, end = first, last
        elif not range_part:
            raise _component(
                f'Invalid step syntax: omitting the starting point like "/{step_text}" is not allowed. '
                f'Use "*/{step_text}" for wildcard steps or "X-Y/{step_text}" for range steps.'
            )
        else:
            raise _component(
                f'Invalid step syntax: single number steps like "{range_part}/{step_text}" are not allowed. '
                f'Use "*/{step_text}" for wildcard steps or "X-Y/{step_text}" for range steps.'
            )
        if start < self.low or end > self.high or start > end:
            raise _component("Range is out of bounds in stepping.")
        self._set(start, bit)
        # croner walks the values in u16 arithmetic and fails on overflow,
        # even after the last value it sets.
        value_at = start
        while value_at <= end:
            if value_at + step > 0xFFFF:
                raise _component("Value exceeded max after stepping.")
            value_at += step


def check(pattern: str) -> None:
    """Raise `CronError` unless croner 4 would parse `pattern` (5–7 fields:
    optional seconds first, optional year last)."""
    text = pattern.upper().strip(_SPACE)
    if not text:
        raise CronError("CronPattern cannot be an empty string.")
    if "@" in text:
        text = _NICKNAMES.get(text.strip(_SPACE), text.strip(_SPACE))
    for old, new in _WEEKDAYS + _MONTHS:
        text = text.replace(old, new)
    parts = [p for p in _SPLIT.split(text) if p]
    count = len(parts)
    if count == 5:
        parts.insert(0, "0")
    if len(parts) == 6:
        parts.append("*")
    if not 5 <= count <= 7:
        raise CronError("Invalid pattern: Pattern must have between 5 and 7 fields.")
    parts[3] = parts[3].replace("?", "*")
    parts[5] = parts[5].replace("?", "*").removeprefix("+")
    for index, part in enumerate(parts):
        allowed = _ALLOWED.get(index, _BASE)
        for ch in part:
            if ch not in allowed:
                raise CronError(f"Pattern contains illegal characters: CronPattern contains illegal character '{ch}' in part '{part}'")
    limits = ((0, 59, 0), (0, 59, 0), (0, 23, 0), (1, 31, _LAST | _CLOSEST), (1, 12, 0), (0, 7, _LAST | _NTH_ALL), (1, 5000, 0))
    for part, (low, high, features) in zip(parts, limits, strict=True):
        _Component(low, high, features).parse(part)
