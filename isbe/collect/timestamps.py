"""Feed timestamps exactly as the earlier Rust build accepted them.

`rfc3339` and `rfc2822` are the strict parsers (including two-digit years,
legacy zone names, comments and the weekday check); `lenient` first repairs
common feed mistakes with a few regex substitutions.
Results are aware UTC datetimes; a leap second (`:60`) reads as `:59`, and
dates Python cannot hold (year 0, beyond 9999) do not parse.
"""

import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone

from isbe.core import validators


class _Invalid(Exception):
    pass


type Scan[T] = tuple[str, T]


class Leap(datetime):
    """A leap second (`:60`), kept and printed as such; Python's datetime
    holds it as second 59 (so it compares equal to that second)."""


def format_rfc3339(when: datetime) -> str:
    """`to_rfc3339_opts(SecondsFormat::Secs, true)`: `2026-10-13T06:00:00Z`."""
    t = when.astimezone(UTC)
    second = 60 if isinstance(when, Leap) else t.second
    return f"{t.year:04}-{t.month:02}-{t.day:02}T{t.hour:02}:{t.minute:02}:{second:02}Z"


def _ltrim(s: str) -> str:
    i = 0
    while i < len(s) and validators.is_space(s[i]):
        i += 1
    return s[i:]


def _digit(c: str) -> bool:
    return "0" <= c <= "9"


def _number(s: str, low: int, high: int) -> Scan[int]:
    """`low`..=`high` ASCII digits."""
    if len(s) < low:
        raise _Invalid
    n = 0
    for i, c in enumerate(s[:high]):
        if not _digit(c):
            if i < low:
                raise _Invalid
            return s[i:], n
        n = n * 10 + ord(c) - 48
    return s[min(high, len(s)) :], n


_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _lower3(s: str) -> str:
    if len(s) < 3 or not s[:3].isascii():
        raise _Invalid
    return validators.ascii_lower(s[:3])


def _month0(s: str) -> Scan[int]:
    name = _lower3(s)
    if name not in _MONTHS:
        raise _Invalid
    return s[3:], _MONTHS.index(name)


def _weekday(s: str) -> Scan[int]:
    name = _lower3(s)
    if name not in _WEEKDAYS:
        raise _Invalid
    return s[3:], _WEEKDAYS.index(name)


def _char(s: str, c: str) -> str:
    if not s.startswith(c):
        raise _Invalid
    return s[1:]


def _space(s: str) -> str:
    rest = _ltrim(s)
    if len(rest) == len(s):
        raise _Invalid
    return rest


def _colon_or_space(s: str) -> str:
    i = 0
    while i < len(s) and (s[i] == ":" or validators.is_space(s[i])):
        i += 1
    return s[i:]


def _offset(
    s: str, colon: Callable[[str], str], zulu: bool, minus_sign: bool
) -> Scan[int]:
    """A timezone offset (minutes always required)."""
    if zulu and s[:1] in ("Z", "z"):
        return s[1:], 0
    sign = s[:1]
    if (sign == "−" and not minus_sign) or sign not in ("+", "-", "−"):
        raise _Invalid
    s = s[1:]
    if len(s) < 2 or not (_digit(s[0]) and _digit(s[1])):
        raise _Invalid
    hours = int(s[:2])
    s = colon(s[2:])
    if len(s) < 2 or not ("0" <= s[0] <= "5" and _digit(s[1])):
        raise _Invalid
    seconds = hours * 3600 + int(s[:2]) * 60
    return s[2:], -seconds if sign != "+" else seconds


_LEGACY = {"gmt": 0, "ut": 0, "z": 0, "edt": -4, "est": -5, "cdt": -5, "cst": -6}
_LEGACY |= {"mdt": -6, "mst": -7, "pdt": -7, "pst": -8}


def _offset_2822(s: str) -> Scan[int]:
    upto = 0
    while upto < len(s) and s[upto].isascii() and s[upto].isalpha():
        upto += 1
    if upto == 0:
        return _offset(s, lambda rest: rest, False, False)
    name, rest = validators.ascii_lower(s[:upto]), s[upto:]
    if name in _LEGACY:
        return rest, _LEGACY[name] * 3600
    if len(name) == 1 and name != "j":
        return rest, 0  # military zones: consumed, treated as -0000
    raise _Invalid


def _comment(s: str) -> str:
    s = _ltrim(s)
    if not s.startswith("("):
        raise _Invalid
    depth, escape = 0, False
    for i, c in enumerate(s):
        if escape:
            escape = False
        elif c == "\\" and depth:
            escape = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return s[i + 1 :]
    raise _Invalid


def _build(
    year: int, month: int, day: int, hour: int, minute: int, second: int,
    offset: int, micro: int = 0, weekday: int | None = None,
) -> datetime:
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour <= 23 and minute <= 59):
        raise _Invalid
    if second > 60 or abs(offset) >= 86400:
        raise _Invalid
    try:
        local = datetime(
            year, month, day, hour, minute, min(second, 59), micro,
            tzinfo=timezone(timedelta(seconds=offset)),
        )
        if weekday is not None and local.weekday() != weekday:
            raise _Invalid
        utc = local.astimezone(UTC)
        if second == 60:
            return Leap(*utc.timetuple()[:6], utc.microsecond, tzinfo=UTC)
        return utc
    except (ValueError, OverflowError):
        raise _Invalid from None


def _attempt(parse: Callable[[str], datetime], s: str) -> datetime | None:
    try:
        return parse(s)
    except _Invalid:
        return None


def _rfc3339(s: str) -> datetime:
    if len(s) < 19 or not all(_digit(s[i]) for i in (0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18)):
        raise _Invalid
    if s[4] != "-" or s[7] != "-" or s[10] not in "tT " or s[13] != ":" or s[16] != ":":
        raise _Invalid
    micro = 0
    rest = s[19:]
    if rest.startswith("."):
        before = rest[1:]
        rest, nanos = _number(before, 1, 9)
        micro = nanos * 10 ** (9 - (len(before) - len(rest))) // 1000
        while rest and _digit(rest[0]):
            rest = rest[1:]
    rest, offset = _offset(rest, lambda r: _char(r, ":"), True, True)
    if rest:
        raise _Invalid
    year, month, day = int(s[0:4]), int(s[5:7]), int(s[8:10])
    hour, minute, second = int(s[11:13]), int(s[14:16]), int(s[17:19])
    return _build(year, month, day, hour, minute, second, offset, micro)


def rfc3339(s: str) -> datetime | None:
    """Strict RFC 3339; None when the text does not parse."""
    return _attempt(_rfc3339, s)


def _rfc2822(s: str) -> datetime:
    s = _ltrim(s)
    weekday = None
    try:
        after, weekday = _weekday(s)
    except _Invalid:
        pass
    else:
        s = _char(after, ",")
    s, day = _number(_ltrim(s), 1, 2)
    s, month0 = _month0(_space(s))
    s = _space(s)
    before = len(s)
    s, year = _number(s, 2, len(s) + 1)
    width = before - len(s)
    if width == 2:
        year += 2000 if year < 50 else 1900
    elif width == 3:
        year += 1900
    s, hour = _number(_space(s), 2, 2)
    s, minute = _number(_ltrim(_char(_ltrim(s), ":")), 2, 2)
    second = 0
    trimmed = _ltrim(s)
    if trimmed.startswith(":"):
        s, second = _number(trimmed[1:], 2, 2)
    s, offset = _offset_2822(_space(s))
    while True:
        try:
            s = _comment(s)
        except _Invalid:
            break
    if s:
        raise _Invalid
    return _build(year, month0 + 1, day, hour, minute, second, offset, weekday=weekday)


def rfc2822(s: str) -> datetime | None:
    """Strict RFC 2822; trailing text (even a space) fails."""
    return _attempt(_rfc2822, s)


def _rfc1123(s: str) -> datetime:
    """`DateTime::parse_from_str(s, "%d %b %Y %H:%M:%S %z")`."""
    s, day = _number(_ltrim(s), 1, 2)
    s, month0 = _month0(_ltrim(s))
    s = _ltrim(_ltrim(s))
    if s[:1] in ("+", "-"):
        sign = -1 if s[0] == "-" else 1
        s, year = _number(s[1:], 1, len(s))
        year *= sign
    else:
        s, year = _number(s, 1, 4)
    s, hour = _number(_ltrim(_ltrim(s)), 1, 2)
    s, minute = _number(_ltrim(_char(s, ":")), 1, 2)
    s, second = _number(_ltrim(_char(s, ":")), 1, 2)
    s, offset = _offset(_ltrim(_ltrim(s)), _colon_or_space, False, True)
    if s:
        raise _Invalid
    return _build(year, month0 + 1, day, hour, minute, second, offset)


# Repairs for common feed mistakes; each substitution replaces the first match only.
_FIX_3339 = ((re.compile(r"(\+|-)(\d{2})(\d{2})"), r"\1\2:\3"),
             (re.compile(r"-\d{2}\Z"), r"\g<0>T00:00:00+00:00"))
_DAYS = "(Sun|Mon|Tue|Wed|Thu|Fri|Sat)"
_MONTH_NAMES = "(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
_FIX_2822 = (
    (re.compile(r"(UTC|-0000\Z)"), "+0000"),
    (re.compile(_DAYS + "[a-z]*, "), ""),
    (re.compile(_MONTH_NAMES + "[a-z]*"), r"\1"),
    (re.compile(_MONTH_NAMES + r" (\d{1,2}) (\d{4})"), r"\2 \1 \3"),
    (re.compile(" 24:"), " 00:"),
    (re.compile(" ([0-9]):"), r" 0\1:"),
)
_FIX_1123 = ((re.compile(r" Z\Z"), " +0000"), (re.compile(r"\A[A-Za-z]{3}, "), ""))


def _fixed(s: str, fixes: tuple[tuple[re.Pattern[str], str], ...]) -> str:
    s = validators.trim(s)
    for pattern, replacement in fixes:
        s = pattern.sub(replacement, s, count=1)
    return s


def lenient(text: str) -> datetime | None:
    """RFC 3339, then RFC 2822, then RFC 1123, each with its common repairs."""
    for parse, fixes, trim in (
        (_rfc3339, _FIX_3339, True),
        (_rfc2822, _FIX_2822, False),
        (_rfc1123, _FIX_1123, False),
    ):
        cleaned = _fixed(text, fixes)
        found = _attempt(parse, validators.trim(cleaned) if trim else cleaned)
        if found is not None:
            return found
    return None


def _ymd(s: str) -> date:
    """`NaiveDate::parse_from_str(s, "%Y-%m-%d")`."""
    s = _ltrim(s)
    if s[:1] in ("+", "-"):
        sign = -1 if s[0] == "-" else 1
        s, year = _number(s[1:], 1, len(s))
        year *= sign
    else:
        s, year = _number(s, 1, 4)
    s, month = _number(_ltrim(_char(s, "-")), 1, 2)
    s, day = _number(_ltrim(_char(s, "-")), 1, 2)
    if s:
        raise _Invalid
    try:
        return date(year, month, day)
    except ValueError:
        raise _Invalid from None


def ymd(s: str) -> date | None:
    try:
        return _ymd(s)
    except _Invalid:
        return None
