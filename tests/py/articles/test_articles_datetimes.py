"""Date and time parsing: accepted formats, rejected input and the instants they give."""

import pytest

from isbe.articles import datetimes
from isbe.articles.datetimes import parse_date, unix_rfc3339


@pytest.mark.parametrize(
    ("text", "value", "day", "instant"),
    [
        ("2026-10-16T23:30:00-07:00", "2026-10-16T23:30:00-07:00", (2026, 10, 16), 1792218600),
        ("2026-10-11T20:00:00Z", "2026-10-11T20:00:00+00:00", (2026, 10, 11), 1791748800),
        ("2026-10-11T01:02:03.5z", "2026-10-11T01:02:03.500+00:00", (2026, 10, 11), 1791680523),
        ("2026-10-11 01:02:03.123456+08:00", "2026-10-11T01:02:03.123456+08:00", (2026, 10, 11), 1791651723),
        ("Mon, 12 Oct 2026 08:00:00 +0800", "2026-10-12T08:00:00+08:00", (2026, 10, 12), 1791763200),
        ("12 Oct 26 08:00 GMT", "2026-10-12T08:00:00+00:00", (2026, 10, 12), 1791792000),
        ("  2026-10-12 08:00 ", "2026-10-12T08:00:00", (2026, 10, 12), None),
        ("2026/10/12 08:00:05", "2026-10-12T08:00:05", (2026, 10, 12), None),
        ("2026年10月05日 9:05", "2026-10-05T09:05:00", (2026, 10, 5), None),
        ("2026年10月05日", "2026-10-05", (2026, 10, 5), None),
        ("2026-10-1", "2026-10-01", (2026, 10, 1), None),
        ("2026/ 1/ 2", "2026-01-02", (2026, 1, 2), None),
        ("+12026-10-01", "+12026-10-01", (12026, 10, 1), None),
    ],
)
def test_parse_date_matches_the_calendar_grammar(text: str, value: str, day: tuple[int, int, int], instant: int | None) -> None:
    date = parse_date(text)
    assert date is not None
    assert (date.value, date.day, date.instant) == (value, day, instant)


@pytest.mark.parametrize(
    "text",
    ["", "2026-13-01", "2026-02-30", "x2026-10-12", "2026-10-12abc", "2026-10-12T24:00:00Z",
     "Tue, 12 Oct 2026 08:00:00 +0800", "1791891212", "2026-10-12T08:00:00+24:00"],
)  # fmt: skip
def test_parse_date_rejects_invalid_dates(text: str) -> None:
    assert parse_date(text) is None


def test_naive_dates_times_and_unix_seconds() -> None:
    assert datetimes.naive_date("2026-10-01") == (2026, 10, 1)
    assert datetimes.naive_date("2026-10-32") is None
    assert datetimes.hour_minute("14:53") and not datetimes.hour_minute("24:53")
    assert unix_rfc3339(1791891212) == "2026-10-13T11:33:32+00:00"
    assert datetimes.day_at(1791748800, 8 * 3600) == (2026, 10, 12)
