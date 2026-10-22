"""Publication-date strings in the formats pages use.

The strftime-style parsing here is lenient where `strptime` is not: numbers
take one or two digits and may be preceded by whitespace, a space in the format
matches any run of whitespace, and years may carry a sign. The RFC 3339 and
RFC 2822 parsers and the calendar live in `chrono`.
"""

from isbe.articles.chrono import (
    I32,
    Date,
    Day,
    civil_from_days,
    clock,
    day_at,
    format_day,
    format_rfc3339,
    number,
    rfc2822,
    rfc3339,
    skip_space,
    valid_day,
)
from isbe.core.validators import trim

__all__ = ["Date", "Day", "day_at", "hour_minute", "naive_date", "parse_date", "plausible", "rfc3339", "unix_rfc3339"]


# --- strftime subset ----------------------------------------------------------------

_SPACE = " "
_RANGES = {"m": (1, 12), "d": (1, 31), "H": (0, 23), "M": (0, 59), "S": (0, 60)}


def _items(fmt: str) -> tuple[str, ...]:
    """A format string as items: `%X` fields, literals and whitespace runs."""
    items: list[str] = []
    i = 0
    while i < len(fmt):
        if fmt[i] == "%":
            items.append(fmt[i : i + 2])
            i += 2
        elif fmt[i] == " ":
            items.append(_SPACE)
            i += 1
        else:
            j = i
            while j < len(fmt) and fmt[j] not in "% ":
                j += 1
            items.append(fmt[i:j])
            i = j
    return tuple(items)


def _fields(s: str, items: tuple[str, ...]) -> dict[str, int] | None:
    """Parse `s` by `items`; the whole input must be consumed."""
    fields: dict[str, int] = {}
    i = 0
    for item in items:
        if item == _SPACE:
            i = skip_space(s, i)
        elif item[0] != "%":
            if not s.startswith(item, i):
                return None
            i += len(item)
        else:
            i = skip_space(s, i)
            kind = item[1]
            if kind == "Y" and i < len(s) and s[i] in "+-":
                # A signed year may have any number of digits.
                negative = s[i] == "-"
                scanned = number(s, i + 1, 1, 1 << 62)
                if scanned is None:
                    return None
                i, value = scanned[0], -scanned[1] if negative else scanned[1]
            else:
                scanned = number(s, i, 1, 4 if kind == "Y" else 2)
                if scanned is None:
                    return None
                i, value = scanned
            low, high = _RANGES.get(kind, I32)
            if not low <= value <= high:
                return None
            fields[kind] = value
    return fields if i == len(s) else None


_DATETIME_FORMATS = tuple(
    _items(f)
    for f in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%Y年%m月%d日 %H:%M:%S",
        "%Y年%m月%d日 %H:%M",
    )
)
_DATE_FORMATS = tuple(_items(f) for f in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"))
_ISO_DATE = _items("%Y-%m-%d")
_HOUR_MINUTE = _items("%H:%M")


def naive_date(s: str) -> Day | None:
    """`NaiveDate::parse_from_str(s, "%Y-%m-%d")`."""
    return _day(_fields(s, _ISO_DATE))


def _day(fields: dict[str, int] | None) -> Day | None:
    if fields is None:
        return None
    day = (fields["Y"], fields["m"], fields["d"])
    return day if valid_day(*day) else None


def hour_minute(s: str) -> bool:
    """Whether `NaiveTime::parse_from_str(s, "%H:%M")` succeeds."""
    return _fields(s, _HOUR_MINUTE) is not None


# A date's text must start with one of these and end with one of the next.
_FIRST = frozenset("0123456789+-") | frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_LAST = frozenset("0123456789)日") | frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


def plausible(value: str) -> bool:
    """A cheap necessary condition for `parse_date(value)` on trimmed text."""
    return bool(value) and value[0] in _FIRST and value[-1] in _LAST


def parse_date(value: str) -> Date | None:
    """`article_date::parse_date`: RFC 3339 or RFC 2822 with their offset, else
    one of the naive formats, whose missing zone must not become an invented
    UTC instant."""
    value = trim(value)
    if not plausible(value):
        return None
    stamp = rfc3339(value) or rfc2822(value)
    if stamp is not None:
        return stamp.date()
    for items in _DATETIME_FORMATS:
        fields = _fields(value, items)
        day = _day(fields)
        if fields is None or day is None:
            continue
        second = fields.get("S", 0)
        return Date(f"{format_day(day)}T{clock(fields['H'], fields['M'], second)}", day, None)
    for items in _DATE_FORMATS:
        day = _day(_fields(value, items))
        if day is not None:
            return Date(format_day(day), day, None)
    return None


def unix_rfc3339(seconds: int) -> str:
    """`DateTime::from_timestamp(seconds, 0).to_rfc3339()`."""
    day = civil_from_days(seconds // 86400)
    rest = seconds % 86400
    return format_rfc3339(day, rest // 3600, rest // 60 % 60, rest % 60, 0, 0)
