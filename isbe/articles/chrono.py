"""Date-time parsing and printing with the grammar of the chrono library, which
the earlier Rust build used.

Stored date evidence, `published_at` and `updated_at` were written by those
parsers, so they are reproduced here instead of using `datetime`: RFC 3339,
RFC 2822 (with its two-digit years, zone names and comments) and the scanning
primitives the strftime-style parser in `datetimes` shares. Dates use a
proleptic Gregorian range (years -262143..=262142, year 0 included), so they
are kept as tuples rather than `datetime.date`.
"""

from dataclasses import dataclass

from isbe.core.validators import is_space

# One spare year is kept at each end of the internal range.
MIN_YEAR, MAX_YEAR = -262143, 262142
_I64_MAX = (1 << 63) - 1
I32 = (-(1 << 31), (1 << 31) - 1)
_NANO = 1_000_000_000

type Day = tuple[int, int, int]


@dataclass(frozen=True)
class Date:
    """`article_date::Date`: the normalized text, its calendar day, and the instant
    (Unix seconds) when the source stated an offset. `offset` (seconds east of UTC)
    is the stated offset; naive values have neither."""

    value: str
    day: Day
    instant: int | None
    offset: int | None = None


# --- calendar arithmetic ------------------------------------------------------------


def _leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def valid_day(year: int, month: int, day: int) -> bool:
    if not (MIN_YEAR <= year <= MAX_YEAR and 1 <= month <= 12 and day >= 1):
        return False
    days = (31, 29 if _leap(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    return day <= days[month - 1]


def days_from_civil(year: int, month: int, day: int) -> int:
    """Days since 1970-01-01 in the proleptic Gregorian calendar."""
    year -= month <= 2
    era = year // 400
    yoe = year - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    return era * 146097 + yoe * 365 + yoe // 4 - yoe // 100 + doy - 719468


def civil_from_days(days: int) -> Day:
    days += 719468
    era = days // 146097
    doe = days - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    day = doy - (153 * mp + 2) // 5 + 1
    month = mp + 3 if mp < 10 else mp - 9
    return yoe + era * 400 + (month <= 2), month, day


def day_at(instant: int, offset: int) -> Day:
    """The calendar day of `instant` as seen at `offset` seconds east of UTC."""
    return civil_from_days((instant + offset) // 86400)


# --- formatting ---------------------------------------------------------------------


def _year(year: int) -> str:
    # Four digits inside 0..=9999 and an explicit sign outside it.
    return f"{year:04d}" if 0 <= year <= 9999 else f"{year:+05d}"


def format_day(day: Day) -> str:
    """`NaiveDate`'s Display: `YYYY-MM-DD`."""
    return f"{_year(day[0])}-{day[1]:02d}-{day[2]:02d}"


def clock(hour: int, minute: int, second: int) -> str:
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def format_rfc3339(day: Day, hour: int, minute: int, second: int, nano: int, offset: int) -> str:
    """`DateTime::to_rfc3339`: AutoSi fraction, `+HH:MM` offset (never `Z`)."""
    text = f"{format_day(day)}T{clock(hour, minute, second)}"
    if nano % 1_000_000 == 0 and nano:
        text += f".{nano // 1_000_000:03d}"
    elif nano % 1000 == 0 and nano:
        text += f".{nano // 1000:06d}"
    elif nano:
        text += f".{nano:09d}"
    sign = "-" if offset < 0 else "+"
    minutes = abs(offset) // 60
    return f"{text}{sign}{minutes // 60:02d}:{minutes % 60:02d}"


# --- scanning ------------------------------------------------


def skip_space(s: str, i: int) -> int:
    while i < len(s) and is_space(s[i]):
        i += 1
    return i


def number(s: str, i: int, low: int, high: int) -> tuple[int, int] | None:
    """`scan::number`: `low..=high` ASCII digits at `i`; None on any error."""
    if len(s) - i < low:
        return None
    value = 0
    j = i
    while j < len(s) and j - i < high:
        c = s[j]
        if not "0" <= c <= "9":
            return None if j - i < low else (j, value)
        value = value * 10 + ord(c) - 48
        if value > _I64_MAX:
            return None
        j += 1
    return j, value


def _two(s: str, i: int) -> int | None:
    if i + 2 > len(s) or not ("0" <= s[i] <= "9" and "0" <= s[i + 1] <= "9"):
        return None
    return int(s[i : i + 2])


def _offset(s: str, i: int, *, colon: bool, zulu: bool, unicode_minus: bool) -> tuple[int, int] | None:
    """`scan::timezone_offset` with minutes required: `Z`, or `±HH[:]MM`."""
    if zulu and i < len(s) and s[i] in "Zz":
        return i + 1, 0
    if i >= len(s):
        return None
    if s[i] == "+":
        negative = False
    elif s[i] == "-" or (s[i] == "\u2212" and unicode_minus):
        negative = True
    else:
        return None
    i += 1
    hours = _two(s, i)
    if hours is None:
        return None
    i += 2
    if colon:
        if i >= len(s) or s[i] != ":":
            return None
        i += 1
    if i + 2 > len(s) or not ("0" <= s[i] <= "5" and "0" <= s[i + 1] <= "9"):
        return None
    seconds = hours * 3600 + int(s[i : i + 2]) * 60
    return i + 2, -seconds if negative else seconds


@dataclass(frozen=True)
class Stamp:
    """A parsed `DateTime<FixedOffset>`: local fields plus the offset."""

    day: Day
    hour: int
    minute: int
    second: int  # 0..=59; a leap second is second 59 with nano >= 1e9
    nano: int
    offset: int

    @property
    def instant(self) -> int:
        return (
            days_from_civil(*self.day) * 86400
            + self.hour * 3600
            + self.minute * 60
            + self.second
            - self.offset
        )

    def date(self) -> Date:
        leap = self.nano >= _NANO
        text = format_rfc3339(
            self.day, self.hour, self.minute, self.second + leap, self.nano - leap * _NANO, self.offset
        )
        return Date(text, self.day, self.instant, self.offset)


def _stamp(day: Day, hour: int, minute: int, second: int, nano: int, offset: int) -> Stamp | None:
    """`from_hms_nano_opt` + `FixedOffset::east_opt` + the UTC range check."""
    if second == 60:
        second, nano = 59, nano + _NANO
    if not (
        valid_day(*day)
        and hour < 24
        and minute < 60
        and second < 60
        and nano < 2 * _NANO
        and (nano < _NANO or second == 59)
        and -86400 < offset < 86400
    ):
        return None
    stamp = Stamp(day, hour, minute, second, nano, offset)
    low = days_from_civil(MIN_YEAR, 1, 1) * 86400
    high = days_from_civil(MAX_YEAR, 12, 31) * 86400 + 86399
    # The local datetime must still be representable once shifted to UTC.
    return stamp if low <= stamp.instant <= high else None


def rfc3339(s: str) -> Stamp | None:
    """`DateTime::parse_from_rfc3339` (strict: fixed widths, `T`/`t`/space separator)."""
    if len(s) < 19:
        return None
    for i in (0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18):
        if not "0" <= s[i] <= "9":
            return None
    if s[4] != "-" or s[7] != "-" or s[10] not in "tT " or s[13] != ":" or s[16] != ":":
        return None
    day = (int(s[0:4]), int(s[5:7]), int(s[8:10]))
    if not valid_day(*day):
        return None
    i, nano = 19, 0
    if i < len(s) and s[i] == ".":
        scanned = number(s, 20, 1, 9)
        if scanned is None:
            return None
        j, value = scanned
        nano = value * 10 ** (9 - (j - 20))
        while j < len(s) and "0" <= s[j] <= "9":
            j += 1
        i = j
    zone = _offset(s, i, colon=True, zulu=True, unicode_minus=True)
    if zone is None or zone[0] != len(s):
        return None
    return _stamp(day, int(s[11:13]), int(s[14:16]), int(s[17:19]), nano, zone[1])


_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_ZONES = {"gmt": 0, "ut": 0, "z": 0, "edt": -4, "est": -5, "cdt": -5, "cst": -6, "mdt": -6,
          "mst": -7, "pdt": -7, "pst": -8}  # fmt: skip


def _ascii_word(s: str, i: int, words: tuple[str, ...]) -> int | None:
    word = s[i : i + 3]
    if len(word) < 3 or not word.isascii():
        return None
    word = word.lower()
    return words.index(word) if word in words else None


def _space(s: str, i: int) -> int | None:
    """`scan::space`: at least one whitespace character."""
    j = skip_space(s, i)
    return j if j > i else None


def _comment(s: str, i: int) -> int | None:
    """`scan::comment_2822`: one parenthesized comment with nesting and escapes."""
    i = skip_space(s, i)
    if i >= len(s) or s[i] != "(":
        return None
    depth, escape = 1, False
    for j in range(i + 1, len(s)):
        c = s[j]
        if escape:
            escape = False
        elif c == "\\":
            escape = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j + 1
    return None


def _zone_2822(s: str, i: int) -> tuple[int, int] | None:
    j = i
    while j < len(s) and s[j].isascii() and s[j].isalpha():
        j += 1
    if j == i:
        return _offset(s, i, colon=False, zulu=False, unicode_minus=False)
    name = s[i:j].lower()
    if name in _ZONES:
        return j, _ZONES[name] * 3600
    if len(name) == 1 and name != "j":  # military zones are read as -0000
        return j, 0
    return None


def rfc2822(s: str) -> Stamp | None:
    """RFC 2822 in its relaxed grammar."""
    i = skip_space(s, 0)
    weekday = _ascii_word(s, i, _WEEKDAYS)
    if weekday is not None:
        if s[i + 3 : i + 4] != ",":
            return None
        i += 4
    scanned = number(s, skip_space(s, i), 1, 2)
    if scanned is None or not 1 <= scanned[1] <= 31:
        return None
    after = _space(s, scanned[0])
    day = scanned[1]
    month = None if after is None else _ascii_word(s, after, _MONTHS)
    if after is None or month is None:
        return None
    after = _space(s, after + 3)
    scanned = None if after is None else number(s, after, 2, 1 << 62)
    if after is None or scanned is None:
        return None
    i, year = scanned
    if i - after == 2:
        year += 2000 if year < 50 else 1900
    elif i - after == 3:
        year += 1900
    after = _space(s, i)
    hour = None if after is None else _two(s, after)
    if after is None or hour is None or hour > 23 or not I32[0] <= year <= I32[1]:
        return None
    i = skip_space(s, after + 2)
    if i >= len(s) or s[i] != ":":
        return None
    i = skip_space(s, i + 1)
    minute = _two(s, i)
    if minute is None or minute > 59:
        return None
    i += 2
    second = 0
    colon = skip_space(s, i)
    if colon < len(s) and s[colon] == ":":
        seconds = _two(s, colon + 1)
        if seconds is None or seconds > 60:
            return None
        second, i = seconds, colon + 3
    after = _space(s, i)
    zone = None if after is None else _zone_2822(s, after)
    if zone is None:
        return None
    i, offset = zone
    while (end := _comment(s, i)) is not None:
        i = end
    if i != len(s):
        return None
    date = (year, month + 1, day)
    # A stated weekday must match the date.
    if weekday is not None and valid_day(*date) and (days_from_civil(*date) + 3) % 7 != weekday:
        return None
    return _stamp(date, hour, minute, second, 0, offset)
