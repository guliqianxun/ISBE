"""Calendar days for media weeks, in the proleptic Gregorian calendar.

A day is a number of days since 1970-01-01 over a range that includes year 0
and years past 9999, so the arithmetic, the ISO week and the ordering hold for
every date the earlier Rust build accepted, which `datetime.date` cannot
represent. Parsing uses the date helpers in `isbe.articles.chrono` and
`isbe.reports.research_compat`.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from isbe.articles import chrono
from isbe.reports import research_compat as compat

SHANGHAI = ZoneInfo("Asia/Shanghai")

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# Python's datetime covers years 1-9999; Shanghai's offset is constant on both
# sides of that range (LMT before 1901, +08:00 since 1991).
_FIRST = int((datetime(1, 1, 2, tzinfo=UTC) - _EPOCH).total_seconds())
_LAST = int((datetime(9999, 12, 30, tzinfo=UTC) - _EPOCH).total_seconds())
_MIN_DAY = chrono.days_from_civil(chrono.MIN_YEAR, 1, 1)

_FIELDS = (("Y", "-"), ("m", "-"), ("d", "T"), ("H", ":"), ("M", ":"), ("S", ""))
_RANGES = {"m": (1, 12), "d": (1, 31), "H": (0, 23), "M": (0, 59), "S": (0, 60)}


class DateError(ValueError):
    """A date that cannot be used for a media week."""


def date(value: str) -> int:
    """A `YYYY-MM-DD` date, which must be written back unchanged.

    Raises `DecodeError` with the date parser's message, or `DateError` for a date that
    parses but is not in canonical form.
    """
    parsed = compat.parse_date(value, spaces=False)
    if compat.format_date(parsed) != value:
        raise DateError("Use a YYYY-MM-DD date")
    return chrono.days_from_civil(*parsed)


def show(day: int) -> str:
    """A day as `YYYY-MM-DD`, with a sign outside years 0-9999."""
    return chrono.format_day(chrono.civil_from_days(day))


def weekday(day: int) -> int:
    """Monday is 0, Sunday 6."""
    return (day + 3) % 7


def iso_week(day: int) -> tuple[int, int]:
    """(ISO year, ISO week number) of the day."""
    thursday = day - weekday(day) + 3
    year = chrono.civil_from_days(thursday)[0]
    return year, (thursday - chrono.days_from_civil(year, 1, 1)) // 7 + 1


def week_start(end: int) -> int:
    """The first day of the week ending on `end`, six days earlier."""
    if end - 6 < _MIN_DAY:
        raise DateError("Invalid report week")
    return end - 6


def today() -> int:
    """Today's calendar day in Asia/Shanghai."""
    now = datetime.now(SHANGHAI)
    return chrono.days_from_civil(now.year, now.month, now.day)


def _shanghai_day(instant: int) -> int:
    at = _EPOCH + timedelta(seconds=min(max(instant, _FIRST), _LAST))
    offset = at.astimezone(SHANGHAI).utcoffset()
    seconds = 0 if offset is None else int(offset.total_seconds())
    return (instant + seconds) // 86400


def _naive_day(s: str) -> chrono.Day | None:
    """The day of a `%Y-%m-%dT%H:%M:%S%.f` timestamp."""
    fields: dict[str, int] = {}
    i = 0
    for kind, literal in _FIELDS:
        # Whitespace is skipped before every number; a signed year may have any length.
        i = chrono.skip_space(s, i)
        if kind == "Y" and s[i : i + 1] in ("+", "-"):
            negative = s[i] == "-"
            scanned = chrono.number(s, i + 1, 1, 1 << 62)
            if scanned is None:
                return None
            i, value = scanned[0], -scanned[1] if negative else scanned[1]
        else:
            scanned = chrono.number(s, i, 1, 4 if kind == "Y" else 2)
            if scanned is None:
                return None
            i, value = scanned
        low, high = _RANGES.get(kind, chrono.I32)
        if not low <= value <= high or not s.startswith(literal, i):
            return None
        fields[kind] = value
        i += len(literal)
    # `%.f`: nothing, or a dot and 1-9 digits, any further digits ignored.
    if s.startswith(".", i):
        scanned = chrono.number(s, i + 1, 1, 9)
        if scanned is None:
            return None
        i = scanned[0]
        while i < len(s) and "0" <= s[i] <= "9":
            i += 1
    if i != len(s):
        return None
    day = (fields["Y"], fields["m"], fields["d"])
    return day if chrono.valid_day(*day) else None


def publication(item: Any) -> int | None:
    """The calendar day of `item.content.published_at`.

    A plain date as given; an RFC 3339 instant on the Shanghai calendar; a
    stored page timestamp without a zone keeps its explicit calendar date.
    """
    content = item.get("content") if isinstance(item, dict) else None
    value = content.get("published_at") if isinstance(content, dict) else None
    if not isinstance(value, str):
        return None
    try:
        return date(value)
    except ValueError:
        pass
    stamp = chrono.rfc3339(value)
    if stamp is not None:
        return _shanghai_day(stamp.instant)
    day = _naive_day(value)
    return None if day is None else chrono.days_from_civil(*day)
