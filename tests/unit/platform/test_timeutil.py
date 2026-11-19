"""Canonical time handling (DL-05): every daily convention maps to the same IST trading date."""
from datetime import UTC, date, datetime, timedelta

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from tradealpha.platform.time.timeutil import (
    IST,
    canonical_daily_ts,
    detect_daily_convention,
    ist_date_of_epoch,
    ist_midnight_epoch,
)

dates = st.dates(min_value=date(2000, 1, 1), max_value=date(2040, 12, 31))


@given(dates)
def test_all_daily_conventions_map_to_same_trading_date(d):
    ist_midnight = int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())
    utc_midnight = int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())
    ist_open = int(datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST).timestamp())
    ist_close = int(datetime(d.year, d.month, d.day, 15, 30, tzinfo=IST).timestamp())
    out = canonical_daily_ts([ist_midnight, utc_midnight, ist_open, ist_close])
    assert set(out.tolist()) == {ist_midnight_epoch(d)}
    assert ist_date_of_epoch(out[0]) == d


@given(dates)
def test_canonicalisation_is_idempotent(d):
    once = canonical_daily_ts([ist_midnight_epoch(d)])
    assert np.array_equal(canonical_daily_ts(once), once)


def test_v1_defect_reproduced_and_fixed():
    """v1 stored the 2025-01-01 bar as '2024-12-31 18:30' (naive UTC of an IST-midnight epoch)."""
    naive_utc = datetime(2024, 12, 31, 18, 30, tzinfo=UTC)
    assert ist_date_of_epoch(int(naive_utc.timestamp())) == date(2025, 1, 1)


def test_detect_daily_convention():
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(5)]
    assert detect_daily_convention([ist_midnight_epoch(d) for d in days]) == "ist_midnight"
    assert detect_daily_convention([int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())
                                    for d in days]) == "utc_midnight"
    assert detect_daily_convention([ist_midnight_epoch(d) + 33300 for d in days]) == "other"
    assert detect_daily_convention([]) == "unknown"
