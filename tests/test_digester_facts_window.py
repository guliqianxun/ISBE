"""Tests for facts_window() — the date-range computed by every digester
to filter facts (papers / articles / news / filings).

This locks the bug fix where the old code only set a lower bound
(`submitted_at >= cutoff`), causing items published AFTER `today` to leak
into "past-week" reports.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from isbe.topics._shared.digester_utils import facts_window


def test_window_is_inclusive_of_today_end_of_day() -> None:
    today = date(2026, 5, 7)
    low, high = facts_window(today, lookback_days=7)
    # Upper bound is end-of-day on `today` so same-day-as-today items are included.
    assert high.year == 2026 and high.month == 5 and high.day == 7
    assert high.hour == 23 and high.minute == 59
    assert high.tzinfo == UTC


def test_window_lower_bound_is_today_minus_lookback() -> None:
    today = date(2026, 5, 7)
    low, _ = facts_window(today, lookback_days=7)
    assert low == datetime(2026, 4, 30, 0, 0, tzinfo=UTC)


def test_window_low_is_strictly_below_high() -> None:
    today = date(2026, 5, 7)
    low, high = facts_window(today, lookback_days=7)
    assert low < high


def test_window_handles_zero_lookback() -> None:
    """lookback=0 means: today only, midnight to midnight."""
    today = date(2026, 5, 7)
    low, high = facts_window(today, lookback_days=0)
    assert low == datetime(2026, 5, 7, 0, 0, tzinfo=UTC)
    assert high.day == 7 and high.hour == 23


def test_window_excludes_items_after_today() -> None:
    """The motivating regression: a paper submitted after `today` must
    fall outside the returned [low, high] window."""
    today = date(2026, 5, 7)
    low, high = facts_window(today, lookback_days=7)
    future_paper = datetime(2026, 5, 11, 6, 16, tzinfo=UTC)
    assert not (low <= future_paper <= high)


def test_window_includes_item_exactly_at_lookback_boundary() -> None:
    today = date(2026, 5, 7)
    low, high = facts_window(today, lookback_days=7)
    edge = datetime(2026, 4, 30, 0, 0, tzinfo=UTC)
    assert low <= edge <= high


def test_window_excludes_item_one_second_before_lower_bound() -> None:
    today = date(2026, 5, 7)
    low, _ = facts_window(today, lookback_days=7)
    edge_minus_1s = low - timedelta(seconds=1)
    assert edge_minus_1s < low
