"""hbt-core capabilities added to close the unification gaps (ADR-003): portfolio mode (several instruments on ONE
account), the flagfix2 slippage refinements (>= N ticks, session-edge widening, clipped to the bar) and the
participation remainder rule "cancel_entries" (an entry never builds beyond a bar's share; an exit always completes)."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

import hbt  # noqa: E402
from tradealpha.lifecycle.gates.drills import DRILL_IR, synthetic_bars  # noqa: E402

pytestmark = pytest.mark.trading_mock_matching     # the mock OpenAlgo's matching calibration


def _costs():
    return hbt.cost_model(zero=True)


def _days(n=25):
    d = [date(2025, 1, 6) + timedelta(days=i) for i in range(n)]
    return [x for x in d if x.weekday() < 5]


def _ds(seed, start=500.0, volume=2_000_000):
    return hbt.Dataset.from_frame(synthetic_bars(_days(), seed=seed, start=start, volume=volume), "5m")


def test_portfolio_mode_one_leg_is_the_single_engine_and_legs_share_one_account():
    a, b = _ds(1), _ds(2, 900.0)
    kw = dict(spec=hbt.exec_spec(product="MIS"), costs=_costs())
    one = hbt.run(DRILL_IR, a, **kw)
    p1 = hbt.run_portfolio(DRILL_IR, {"A": a}, **kw)
    assert np.array_equal(one.equity.to_numpy(), p1.equity.to_numpy())
    assert one.manifest["decisions_sha256"] == p1.legs["A"].manifest["decisions_sha256"]
    plan = hbt.order_plan(sizing="pct_equity", sizing_value=0.5)
    p2 = hbt.run_portfolio(DRILL_IR, {"A": a, "B": b}, plan=plan, max_positions=2, **kw)
    assert p2.peak_open == 2 and len(p2.trades) == len(p2.legs["A"].trades) + len(p2.legs["B"].trades)
    # ONE account: the portfolio's final equity is the start + every leg's P&L (legs report start + contribution)
    contrib = sum(float(r.equity.iloc[-1]) - 1_000_000.0 for r in p2.legs.values())
    assert float(p2.equity.iloc[-1]) == pytest.approx(1_000_000.0 + contrib, abs=0.05)
    p3 = hbt.run_portfolio(DRILL_IR, {"A": a, "B": b}, plan=plan, max_positions=1, **kw)
    assert p3.peak_open == 1 and sum(r.rejects["max_positions"] for r in p3.legs.values()) > 0


def _one_trade(spec_over, volume=2_000_000, open_px=100.0):
    """A daily bar series with one entry signal: the fill of the entry."""
    days = pd.bdate_range("2025-03-03", periods=6)
    ts = ((days - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s")).to_numpy("int64") - 19_800
    c = np.full(6, open_px)
    df = pd.DataFrame({"ts": ts, "open": c, "high": c + 1.0, "low": c - 1.0, "close": c, "volume": volume})
    ds = hbt.Dataset.from_frame(df, "D")
    doc = {"ir_version": 1, "name": "one", "universe": {"interval": "D"},
           "rules": [{"when": {"fn": "eq", "args": [{"fn": "position_qty"}, 0]},
                      "do": {"enter_long": {"size": {"fixed_qty": 100}}}}]}
    r = hbt.run(doc, ds, spec=hbt.exec_spec(product="CNC", **spec_over), inst=hbt.instrument(tick=5),
                costs=_costs())
    return r.fills[r.fills["reason"] == "entry"]


def test_flagfix2_slippage_refinements_min_ticks_edge_widening_and_bar_clip():
    base = {"slippage_model": "pct", "slippage_value": 0.001}
    assert float(_one_trade(base)["price"].iloc[0]) == 100.10                       # 10 bps
    # at least 20 ticks (1.00): the minimum wins over 10 bps
    assert float(_one_trade({**base, "slippage_min_ticks": 20})["price"].iloc[0]) == 101.00
    # daily bars: a fill AT the open is an edge fill -> x1.5
    assert float(_one_trade({**base, "slippage_edge_minutes": 15, "slippage_edge_widen": 1.5})["price"].iloc[0]) \
        == 100.15
    # clipped to the bar's range: 20 ticks would be 102.00 > high 101.00
    assert float(_one_trade({**base, "slippage_min_ticks": 40, "slippage_clip": True})["price"].iloc[0]) == 101.00


def test_participation_remainder_cancel_entries_caps_the_entry_and_completes_the_exit():
    spec = {"participation_cap": 0.01}                 # 1 % of 5,000 shares = 50 per bar; the order wants 100
    carry = _one_trade({**spec, "participation_remainder": "carry"}, volume=5_000)
    cancel = _one_trade({**spec, "participation_remainder": "cancel_entries"}, volume=5_000)
    assert int(carry["qty"].sum()) == 100 and len(carry) == 2                     # carried into the next bar
    assert int(cancel["qty"].sum()) == 50 and len(cancel) == 1                    # the rest is cancelled


def _run_orders(ohlc, order, expiry=None, interval="D"):
    """Bars (list of (o, h, l, c); one decision at the FIRST bar) + one enter_long with the given order dict."""
    if interval == "D":
        days = pd.bdate_range("2025-03-03", periods=len(ohlc))
        ts = ((days - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s")).to_numpy("int64") - 19_800
    else:
        t0 = int(pd.Timestamp("2025-03-03 09:15", tz="Asia/Kolkata").timestamp())
        ts = np.array([t0 + i * 300 for i in range(len(ohlc))], dtype="int64")
    o, h, lo, c = (np.array([row[i] for row in ohlc], dtype=float) for i in range(4))
    ds = hbt.Dataset.from_frame(pd.DataFrame({"ts": ts, "open": o, "high": h, "low": lo, "close": c,
                                              "volume": 1_000_000}), interval)
    do = {"enter_long": {"order": order, "size": {"fixed_qty": 10}}}
    if expiry is not None:
        do["enter_long"]["expiry_bars"] = expiry
    doc = {"ir_version": 1, "name": "sl", "universe": {"interval": interval},
           "rules": [{"id": "e", "when": {"fn": "is_first_bar"}, "do": do}]}
    r = hbt.run(doc, ds, spec=hbt.exec_spec(product="CNC" if interval == "D" else "MIS"),
                inst=hbt.instrument(tick=5), costs=_costs())
    return r.fills, r.rejects, r.intents


def test_stop_limit_entries_trigger_like_a_stop_and_fill_like_a_limit():
    base = (100.0, 100.5, 99.5, 100.0)
    # marketable (limit 103 above the 102 trigger): triggered intrabar, filled AT the limit (pessimistic), no slippage
    fills, _, intents = _run_orders([base, (100, 104, 99, 101)],
                                    {"type": "stop_limit", "price": 102.0, "limit": 103.0})
    assert intents["order"].iloc[0] == "stop_limit" and float(intents["limit"].iloc[0]) == 103.0
    assert list(fills["price"]) == [103.0] and fills["reason"].iloc[0] == "entry"
    # NOT marketable (limit 101 below the trigger): becomes a resting limit; pessimistic = no same-bar fill even
    # though the bar's low touched it (the sequence inside the bar is unknown); fills when price comes back
    # (5m bars: a resting entry lives until the session ends)
    fills, _, _ = _run_orders([base, (100, 104, 99, 103), (103, 103.5, 100.5, 101)],
                              {"type": "stop_limit", "price": 102.0, "limit": 101.0}, interval="5m")
    assert list(fills["price"])[0] == 101.0 and int(fills["bar"].iloc[0]) == 2
    # gap through trigger AND limit: never chases - fills only when a later bar trades back inside the limit
    fills, _, _ = _run_orders([base, (105, 106, 104, 105), (105, 105.5, 102.5, 103)],
                              {"type": "stop_limit", "price": 102.0, "limit": 103.0}, interval="5m")
    assert list(fills["price"])[0] == 103.0 and int(fills["bar"].iloc[0]) == 2
    # gap through the trigger with the open INSIDE the limit: fills at the open (better than the limit)
    fills, _, _ = _run_orders([base, (102.5, 104, 102, 103)],
                              {"type": "stop_limit", "price": 102.0, "limit": 103.0})
    assert list(fills["price"]) == [102.5]
    # without "limit": marketable at the trigger (limit = trigger)
    fills, _, _ = _run_orders([base, (100, 104, 99, 101)], {"type": "stop_limit", "price": 102.0})
    assert list(fills["price"]) == [102.0]


def test_ioc_style_expiry_a_resting_entry_dies_with_its_bar():
    flat = (100.0, 100.5, 99.5, 100.0)
    # the limit at 95 is never reached inside the validity: expiry_bars=1 cancels it (IOC at bar granularity)
    fills, rejects, _ = _run_orders([flat, flat, flat, (96, 97, 94.5, 95.0)],
                                    {"type": "limit", "price": 95.0}, expiry=1)
    assert len(fills[fills["reason"] == "entry"]) == 1 and int(fills["bar"].iloc[0]) == 3   # re-signalled, then filled
    assert rejects["expired"] >= 1
