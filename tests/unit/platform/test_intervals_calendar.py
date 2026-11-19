from datetime import date

import pytest

from tradealpha.marketdata.providers.openalgo.client import chunk_ranges
from tradealpha.platform.time.calendar import MarketCalendar
from tradealpha.platform.time.intervals import INTERVALS, UnsupportedInterval, native_codes, resolve


def test_interval_catalogue_is_stable():
    assert [(s.id, s.code) for s in INTERVALS.values()] == [
        (1, "1m"), (2, "3m"), (3, "5m"), (4, "10m"), (5, "15m"), (6, "30m"), (7, "1h"), (8, "D"), (9, "W"), (10, "M")]
    assert native_codes() == ["1m", "3m", "5m", "10m", "15m", "30m", "1h", "D"]


@pytest.mark.parametrize("alias,code", [("1d", "D"), ("d", "D"), ("1w", "W"), ("60m", "1h"), ("M", "M"), ("1m", "1m")])
def test_aliases(alias, code):
    assert resolve(alias).code == code


def test_unknown_interval():
    with pytest.raises(UnsupportedInterval):
        resolve("2h")


@pytest.mark.parametrize("start,end,max_days,expected", [
    ("2025-01-01", "2025-01-01", 30, 1),          # single day (v1 chunked helper skipped this)
    ("2025-01-01", "2025-01-30", 30, 1),
    ("2025-01-01", "2025-01-31", 30, 2),
    ("2015-01-01", "2024-12-31", 2000, 2),        # Angel One D cap
    ("2024-01-01", "2024-12-31", 30, 13),         # 1 year of 1m
])
def test_chunk_ranges_cover_range_exactly(start, end, max_days, expected):
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    chunks = list(chunk_ranges(s, e, max_days))
    assert len(chunks) == expected
    assert chunks[0][0] == s and chunks[-1][1] == e
    for (_a1, b1), (a2, _b2) in zip(chunks, chunks[1:], strict=False):
        assert (a2 - b1).days == 1                # contiguous, no overlap, no gap
    assert all((b - a).days + 1 <= max_days for a, b in chunks)


def test_calendar_sessions_and_holidays():
    cal = MarketCalendar.default()
    assert not cal.is_trading_day("NSE", date(2025, 3, 14))       # sample holiday
    assert cal.is_trading_day("MCX", date(2025, 3, 14))           # commodity group has no such holiday
    assert not cal.is_trading_day("NSE", date(2025, 3, 15))       # Saturday
    assert cal.expected_bars("NSE", date(2025, 3, 17), resolve("5m")) == 75
    assert cal.expected_bars("NSE", date(2025, 3, 17), resolve("1m")) == 375
    assert cal.expected_bars("MCX", date(2025, 3, 17), resolve("5m")) == 174    # US DST: 09:00-23:30
    assert cal.expected_bars("MCX", date(2025, 1, 15), resolve("5m")) == 179    # US winter: 09:00-23:55
    assert cal.expected_bars("NSE", date(2025, 3, 17), resolve("D")) == 1
    assert len(cal.trading_days("NSE", date(2025, 3, 1), date(2025, 3, 31))) == 19


def test_calendar_overrides():
    cal = MarketCalendar.default().with_overrides([("NSE", "2025-11-01", True, "18:00", "19:00"),   # Muhurat-style
                                                   ("NSE", "2025-03-17", False, None, None)])
    assert cal.is_trading_day("NSE", date(2025, 11, 1))
    assert cal.expected_bars("NSE", date(2025, 11, 1), resolve("1m")) == 60
    assert not cal.is_trading_day("NSE", date(2025, 3, 17))
