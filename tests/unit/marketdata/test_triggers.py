"""Schedule triggers over the market calendar (holidays, MCX US-DST close, previews, catch-up window)."""
from datetime import datetime

import pytest

from tradealpha.marketdata.ingestion import triggers as T
from tradealpha.platform.time.calendar import MarketCalendar
from tradealpha.platform.time.timeutil import IST

CAL = MarketCalendar.default()
FRI = datetime(2025, 3, 28, 16, 0, tzinfo=IST)                     # Mon 31 Mar 2025 is an NSE holiday (sample list)


def test_post_close_preview_skips_holidays():
    t = T.validate({"type": "market_event", "event": "after_close", "exchange": "NSE", "offset_min": 15})
    pv = T.preview(t, CAL, FRI, n=5)
    assert [x[:16] for x in pv["next"]] == ["2025-04-01T15:45", "2025-04-02T15:45", "2025-04-03T15:45",
                                             "2025-04-04T15:45", "2025-04-07T15:45"]
    assert pv["skipped"][0] == "2025-03-31"
    assert T.describe(t, CAL) == "Trading days · NSE close + 15 min (15:45 IST)"


def test_wall_clock_occurrences_include_holidays_for_skip_records():
    t = T.validate({"type": "market_event", "event": "after_close", "exchange": "NSE"})
    nxt = T.next_fire(t, CAL, FRI)
    assert nxt[0].isoformat()[:16] == "2025-03-31T15:45" and nxt[1] is False         # fires, gets recorded as skip


def test_pre_open_default_offset():
    t = T.validate({"type": "market_event", "event": "before_open", "exchange": "NSE"})
    assert t["offset_min"] == -30 and T.preview(t, CAL, FRI, 1)["next"][0][:16] == "2025-04-01T08:45"


def test_mcx_close_follows_us_dst():
    t = T.validate({"type": "market_event", "event": "after_close", "exchange": "MCX", "offset_min": 10})
    summer = T.next_fire(t, CAL, datetime(2025, 3, 28, 12, tzinfo=IST))[0]
    winter = T.next_fire(t, CAL, datetime(2025, 3, 7, 12, tzinfo=IST))[0]
    assert summer.isoformat()[:16] == "2025-03-28T23:40"
    assert winter.isoformat()[:16] == "2025-03-08T00:05"                      # 23:55 + 10 -> after midnight
    assert T.session_day(t, winter).isoformat() == "2025-03-07"                # ...but belongs to Friday's session


def test_session_interval_slots_and_single_holiday_record():
    t = T.validate({"type": "session_interval", "exchange": "NSE", "every_min": 5})
    slots = [ft for ft, trading in T.occurrences(t, CAL, datetime(2025, 3, 27, 0, 0, tzinfo=IST), limit=200)
             if ft.date().isoformat() == "2025-03-27"]
    assert slots[0].strftime("%H:%M") == "09:20" and slots[-1].strftime("%H:%M") == "15:35" and len(slots) == 76
    hol = [x for x in T.occurrences(t, CAL, FRI, limit=300) if not x[1]]
    assert len([x for x in hol if x[0].date().isoformat() == "2025-03-31"]) == 1


def test_cron_and_once():
    c = T.validate({"type": "cron", "expr": "0 10 * * SUN"})
    assert T.validate({"type": "cron", "expr": "0 18 * * 1-5", "skip_non_trading": True})["skip_non_trading"]
    assert T.preview(c, CAL, FRI, 1)["next"][0][:16] == "2025-03-30T10:00"
    o = T.validate({"type": "once", "at": "2025-03-29T22:00:00"})
    assert o["at"].endswith("+05:30") and T.occurrences(o, CAL, FRI) and not T.occurrences(o, CAL, datetime(2025, 3, 30, tzinfo=IST))


def test_previous_fire_for_catch_up():
    t = T.validate({"type": "market_event", "event": "after_close", "exchange": "NSE"})
    assert T.previous_fire(t, CAL, datetime(2025, 3, 17, 17, 0, tzinfo=IST), 6).strftime("%d %H:%M") == "17 15:45"
    assert T.previous_fire(t, CAL, datetime(2025, 3, 17, 23, 0, tzinfo=IST), 6) is None     # outside the window


@pytest.mark.parametrize("bad", [{"type": "x"}, {"type": "market_event", "event": "noon"},
                                 {"type": "session_interval", "every_min": 0}, {"type": "cron", "expr": "nope"},
                                 {"type": "market_event", "event": "after_close", "exchange": "LSE"}])
def test_validation(bad):
    with pytest.raises(T.TriggerError):
        T.validate(bad)
