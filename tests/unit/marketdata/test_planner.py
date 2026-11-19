"""Planner (pure): interval-aware incremental windows, finality, repair, chunking."""
from datetime import date, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tradealpha.marketdata.ingestion.planner import (
    SeriesRef,
    SyncSnapshot,
    incremental_window,
    is_final,
    latest_final_possible,
    plan_series,
)
from tradealpha.platform.time.calendar import MarketCalendar
from tradealpha.platform.time.intervals import resolve
from tradealpha.platform.time.timeutil import IST, ist_midnight_epoch

CAL = MarketCalendar.default()
D, M5, M1 = resolve("D"), resolve("5m"), resolve("1m")


def ist(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def bar_ts(y, m, d, hh, mm):
    return int(ist(y, m, d, hh, mm).timestamp())


def daily(y, m, d):
    return ist_midnight_epoch(date(y, m, d))


def test_post_close_fetches_todays_final_daily_bar():                      # CP-04
    s = SeriesRef(1, "NSE", D)
    sync = SyncSnapshot(daily(2025, 1, 1), daily(2025, 3, 17), daily(2025, 3, 17))
    # 15:44 -> today's bar is not final yet, yesterday's is stored: nothing to request
    assert incremental_window(s, sync, CAL, ist(2025, 3, 18, 15, 44)) is None
    # 15:45 (close + 15 min) -> today's bar is final; yesterday's stored bar is final too -> only today is requested
    w = incremental_window(s, sync, CAL, ist(2025, 3, 18, 15, 45))
    assert w == (date(2025, 3, 18), date(2025, 3, 18))
    # a PROVISIONAL daily bar is stored (fetched before the close) -> re-fetch from the trading day before it
    prov = SyncSnapshot(daily(2025, 1, 1), daily(2025, 3, 17), daily(2025, 3, 13))
    w = incremental_window(s, prov, CAL, ist(2025, 3, 18, 15, 45))
    assert w == (date(2025, 3, 12), date(2025, 3, 18))                     # anchor 13 Mar (14 Mar = holiday)


def test_no_lookback_cap_after_an_outage():                                # CP-03
    s = SeriesRef(1, "NSE", M5)
    last = bar_ts(2025, 3, 7, 15, 25)
    tasks = plan_series(s, "incremental", CAL, ist(2025, 3, 28, 16, 0), 100, sync=SyncSnapshot(last - 86400, last, last))
    # 7 Mar is complete and final (15:25 is the last 5m bar) -> start on the next trading day, no look-back cap
    assert [(t.range_start, t.range_end) for t in tasks] == [(date(2025, 3, 10), date(2025, 3, 28))]
    # stored day NOT complete (last bar 14:00) -> that day is re-fetched
    mid = bar_ts(2025, 3, 7, 14, 0)
    tasks = plan_series(s, "incremental", CAL, ist(2025, 3, 28, 16, 0), 100, sync=SyncSnapshot(mid - 86400, mid, mid))
    assert tasks[0].range_start == date(2025, 3, 7)


def test_in_session_follow_requests_only_when_a_new_bar_is_final():
    s = SeriesRef(1, "NSE", M5)
    last = bar_ts(2025, 3, 18, 10, 55)                                    # bar 10:55-11:00, final at 11:01
    sync = SyncSnapshot(last - 86400 * 5, last, last)
    assert incremental_window(s, sync, CAL, ist(2025, 3, 18, 11, 3)) is None
    assert incremental_window(s, sync, CAL, ist(2025, 3, 18, 11, 6)) == (date(2025, 3, 18), date(2025, 3, 18))


def test_session_over_last_bar_final_means_nothing_to_do():
    s = SeriesRef(1, "NSE", M5)
    last = bar_ts(2025, 3, 18, 15, 25)
    assert latest_final_possible(CAL, "NSE", M5, ist(2025, 3, 18, 16, 0)) == last
    assert incremental_window(s, SyncSnapshot(last - 86400, last, last), CAL, ist(2025, 3, 18, 16, 0)) is None
    # ...and still nothing on the following holiday morning
    assert incremental_window(s, SyncSnapshot(last - 86400, last, last), CAL, ist(2025, 3, 19, 8, 0)) is None


def test_provisional_bars_are_refetched():
    s = SeriesRef(1, "NSE", M5)
    final, prov = bar_ts(2025, 3, 17, 15, 25), bar_ts(2025, 3, 18, 11, 0)   # 18 Mar bars stored mid-session
    w = incremental_window(s, SyncSnapshot(final - 86400, prov, final), CAL, ist(2025, 3, 18, 16, 0))
    assert w == (date(2025, 3, 17), date(2025, 3, 18))


def chrono(tasks):
    return sorted(tasks, key=lambda t: t.range_start)


def test_empty_series_uses_default_backfill_and_chunks():
    tasks = chrono(plan_series(SeriesRef(1, "NSE", M1), "incremental", CAL, ist(2025, 3, 28, 16, 0), 30))
    assert tasks[0].range_start == date(2025, 2, 26) and tasks[-1].range_end == date(2025, 3, 28)
    assert all((t.range_end - t.range_start).days < 30 for t in tasks)


def test_fixed_mode_chunks_to_broker_cap_and_clamps_future():
    raw = plan_series(SeriesRef(1, "NSE", M1), "fixed", CAL, ist(2025, 3, 28, 16, 0), 30,
                      start=date(2025, 1, 1), end=date(2025, 12, 31))
    tasks = chrono(raw)
    assert len(tasks) == 3 and tasks[-1].range_end == date(2025, 3, 28)
    # newest chunk first: a paused / interrupted backfill extends the series backwards without leaving holes
    assert raw == tasks[::-1]


def test_repair_targets_only_short_sessions():
    s = SeriesRef(1, "NSE", M5)
    days = CAL.trading_days("NSE", date(2025, 3, 3), date(2025, 3, 28))
    counts = {d: 75 for d in days}
    counts[date(2025, 3, 12)] = 40                                           # truncated
    counts.pop(date(2025, 3, 20))                                            # missing
    counts[date(2025, 3, 21)] = 10                                           # consecutive with 20 Mar -> one window
    tasks = chrono(plan_series(s, "repair", CAL, ist(2025, 3, 28, 16, 0), 100, counts=counts,
                               start=date(2025, 3, 3), end=date(2025, 3, 28)))
    assert [(t.range_start, t.range_end) for t in tasks] == [(date(2025, 3, 12), date(2025, 3, 12)),
                                                             (date(2025, 3, 20), date(2025, 3, 21))]


def test_finality_rules():
    assert is_final(CAL, "NSE", D, daily(2025, 3, 18), ist(2025, 3, 18, 15, 45))
    assert not is_final(CAL, "NSE", D, daily(2025, 3, 18), ist(2025, 3, 18, 15, 44))
    assert is_final(CAL, "MCX", D, daily(2025, 3, 18), ist(2025, 3, 19, 0, 11))     # MCX closes 23:30 in US DST (+15)
    assert not is_final(CAL, "MCX", D, daily(2025, 1, 15), ist(2025, 1, 16, 0, 5))  # ... 23:55 in US winter (+15)
    assert is_final(CAL, "NSE", M5, bar_ts(2025, 3, 18, 11, 0), ist(2025, 3, 18, 11, 6))
    assert not is_final(CAL, "NSE", M5, bar_ts(2025, 3, 18, 11, 0), ist(2025, 3, 18, 11, 5, ))


def test_derived_interval_rejected():
    with pytest.raises(ValueError):
        plan_series(SeriesRef(1, "NSE", resolve("W")), "incremental", CAL, ist(2025, 3, 28, 16), 100)


@settings(max_examples=200, deadline=None)
@given(st.sampled_from(["1m", "5m", "1h", "D"]),
       st.integers(min_value=0, max_value=400),              # days since last bar
       st.integers(min_value=0, max_value=24 * 60 - 1),      # minute of day "now"
       st.integers(min_value=0, max_value=200))              # day offset of "now"
def test_property_incremental_never_leaves_a_gap(code, age_days, minute, day_off):
    spec = resolve(code)
    now = ist(2025, 1, 1) + timedelta(days=day_off, minutes=minute)
    last = int((now - timedelta(days=age_days)).timestamp()) // 60 * 60
    sync = SyncSnapshot(last - 86400, last, last)
    s = SeriesRef(1, "NSE", spec)
    tasks = chrono(plan_series(s, "incremental", CAL, now, 30, sync=sync))
    possible = latest_final_possible(CAL, "NSE", spec, now)
    if not tasks:
        # nothing planned => the newest final bar (if any) is not newer than what is stored
        assert possible is None or possible <= last
        return
    from tradealpha.platform.time.timeutil import ist_date_of_epoch
    last_day = ist_date_of_epoch(last)
    if tasks[0].range_start > last_day:                                      # skipping ahead is only allowed when...
        skipped = CAL.trading_days("NSE", last_day + timedelta(days=1), tasks[0].range_start - timedelta(days=1))
        assert skipped == []                                                 # ...no trading day is skipped, and
        if spec.is_intraday:                                                 # ...the stored session was complete
            session = CAL.session_on("NSE", last_day)
            assert session is not None
            n = CAL.expected_bars("NSE", last_day, spec)
            assert last >= int(datetime.combine(last_day, session.open, IST).timestamp()) + (n - 1) * spec.seconds
    assert tasks[-1].range_end == now.date()
    for a, b in zip(tasks, tasks[1:], strict=False):
        assert (b.range_start - a.range_end).days == 1                       # contiguous chunks
    assert all((t.range_end - t.range_start).days < 30 for t in tasks)
