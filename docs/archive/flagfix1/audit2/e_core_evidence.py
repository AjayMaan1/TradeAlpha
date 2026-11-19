"""Audit 2 evidence (Lab core, Phase 1.2). Every block reproduces one finding and records what happened.
Run: source ~/venvs/h314/bin/activate && python /workspace/audit2/e_core_evidence.py"""
import json, sys, warnings
from datetime import date
from types import SimpleNamespace as NS
H = "/workspace/historify-master/historify"
sys.path[:0] = [H, H + "/lab"]
warnings.simplefilter("ignore")
import duckdb, hbt_core, numpy as np, pandas as pd
from app.core.corporate_actions import adjust_frame, price_factor
from app.core.timeutil import ist_midnight_epoch
from hbt import backtest, bars_from_frame, exec_spec, instrument, order_plan, periods_per_year
from hbt.costs import cost_model
from tests.lab.conftest import mk_bars, sig

Z, out = cost_model(zero=True), {}
F = lambda r: r.fills[["bar", "buy", "qty", "price", "reason"]].values.tolist()
NOS = dict(squareoff_at=None, no_new_entries_after=None)
DAYS = [date(2025, 3, d) for d in (3, 4, 5, 6, 7, 10, 11, 12)]
c = np.arange(100, 108.0)
daily = bars_from_frame(pd.DataFrame({"ts": [ist_midnight_epoch(d) for d in DAYS], "open": c, "high": c + 1,
                                      "low": c - 1, "close": c, "volume": 10**6}))
le = np.zeros(8, bool); le[1] = True

# X-01 default ExecSpec on daily bars: every entry rejected (cutoff 14:55 vs daily "close minute" 24:00)
r = backtest(daily, 86400, le, spec=exec_spec(product="CNC"), costs=cost_model("EQ_DELIVERY"))
out["X01_daily_defaults"] = {"fills": len(r.fills), "rejects": {k: v for k, v in r.rejects.items() if v},
                             "warnings": r.warnings}
# X-02 limit / stop entries never fill on daily bars (DAY order of the signal day cancelled at the next open)
r = backtest(daily, 86400, le, spec=exec_spec(product="CNC", **NOS), plan=order_plan(entry_type="limit"),
             costs=cost_model("EQ_DELIVERY"))
out["X02_daily_limit_entry"] = {"limit_price": 101.0, "next_bar_low": 101.0, "fills": len(r.fills),
                                "rejects": {k: v for k, v in r.rejects.items() if v}}
# X-03 carried entry remainder re-opens the position after an exit signal (and reverses into the wrong side)
rows = [(100, 101, 99, 100), (100, 101, 99, 100, 300), (100, 101, 99, 100, 100000), (100, 101, 99, 100), (100, 101, 99, 100)]
kw = dict(spec=exec_spec(participation_cap=0.1, **NOS), plan=order_plan(sizing="fixed_qty", sizing_value=50),
          inst=instrument(tick=1), costs=Z, cash=10000.0)
r = backtest(mk_bars(rows), 300, sig(5, [0]), sig(5, [1]), **kw)
out["X03_exit_then_reentry"] = {"fills": F(r), "final_position": r.position, "expected_final_position": 0}
r = backtest(mk_bars(rows), 300, sig(5, [0]), short_entry=sig(5, [1]), **kw)
out["X03b_reversal"] = {"fills": F(r), "final_position": r.position, "expected": "short"}
# X-04 adjusted (corporate-action) prices are refused by the core -> D-06 "adjusted" research is impossible
df = pd.DataFrame({"ts": [ist_midnight_epoch(d) for d in DAYS[:5]], "open": [1234.55, 1240.05, 1250.1, 620, 622.5],
                   "high": [1240, 1250, 1260, 625, 630.0], "low": [1230, 1235, 1245, 615, 620.0],
                   "close": [1238.35, 1247.45, 1255.55, 621.3, 627.15], "volume": 1000})
adj = adjust_frame(df, [NS(ex_date=DAYS[3], kind="bonus", price_factor=price_factor("bonus", 1, 1), cash_amount=None)])
try:
    bars_from_frame(adj); out["X04_adjusted_bars"] = "accepted"
except ValueError as e:
    out["X04_adjusted_bars"] = f"refused: {e}"
# X-04b raw prices through the same bonus: the core has no corporate-action position adjustment -> fake 50 % loss
rawb = bars_from_frame(df)
r = backtest(rawb, 86400, np.array([1, 0, 0, 0, 0]), spec=exec_spec(product="CNC", **NOS),
             costs=cost_model(zero=True), cash=100000.0)
out["X04b_raw_through_bonus"] = {"position": r.position, "equity_first": float(r.equity.iloc[1]),
                                 "equity_after_ex_date": float(r.equity.iloc[3]),
                                 "true_change": "about +0.6 % (1 bonus share per share held)"}
# X-05 zero-volume bar fills when no participation cap is set (daily default = no cap)
rows = [(100, 101, 99, 100.5), (102, 103, 101, 102.5, 0), (103, 104, 102, 103), (103, 104, 102, 103)]
r = backtest(mk_bars(rows), 300, sig(4, [0]), spec=exec_spec(participation_cap=None, **NOS), inst=instrument(tick=1),
             costs=Z, cash=10000.0)
out["X05_zero_volume_fill"] = {"fills": F(r), "bar1_volume": 0}
# X-06 partial forced exit clears its carried remainder: MIS position stays open overnight
TWO = dict(days=[(date(2025, 3, 27), "09:15", 2), (date(2025, 3, 28), "09:15", 2)])
rows = [(100, 100, 100, 100), (100, 101, 99, 100), (104, 105, 103, 104, 300), (104, 104, 104, 104, 100000)]
r = backtest(mk_bars(rows, **TWO), 300, sig(4, [0]), spec=exec_spec(participation_cap=0.1, **NOS),
             inst=instrument(tick=1), costs=Z, cash=10000.0)
out["X06_forced_partial"] = {"fills": F(r), "final_mis_position": r.position}
# X-07 NaN prices become INT64_MIN (frame) / 0 (Arrow) instead of being refused
dfn = pd.DataFrame({"ts": mk_bars([(1, 1, 1, 1)] * 3)["ts"], "open": [100, 100, 100.0], "high": [101, 101, 101.0],
                    "low": [99, np.nan, 99.0], "close": [100, 100, 100.0], "volume": 1000})
r = backtest(bars_from_frame(dfn), 300, sig(3, [0]), spec=exec_spec(**NOS), plan=order_plan(stop_pct=0.01),
             inst=instrument(tick=1), costs=Z, cash=10000.0)
rel = duckdb.connect().sql("select 1::BIGINT ts, 100.0::DOUBLE open, 101.0::DOUBLE high, 'NaN'::DOUBLE low, 100.0::DOUBLE close, 5::BIGINT volume")
out["X07_nan_prices"] = {"frame_low": int(bars_from_frame(dfn)["low"][1]), "arrow_low": int(hbt_core.bars_from_arrow(rel)["low"][0]),
                         "fills_with_nan_bar": F(r)}
# X-08 datetime ts (ns / ms) accepted silently: every bar becomes its own "session"
ts = pd.date_range("2025-03-27 09:15", periods=3, freq="5min", tz="Asia/Kolkata")
b = bars_from_frame(pd.DataFrame({"ts": ts, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10}))
out["X08_ts_unit"] = {"ts": b["ts"][:2].tolist(), "day": b["day"][:3].tolist()}
# X-09 annualisation of W / M bars
out["X09_periods_per_year"] = {"D": periods_per_year(86400), "W": round(periods_per_year(7 * 86400), 2),
                               "M": round(periods_per_year(30 * 86400), 2), "expected_W": "~49.6 (248/5)", "expected_M": 12}
# X-10 NaN in a float signal array is treated as True
r = backtest(mk_bars([(100, 101, 99, 100)] * 3), 300, np.array([np.nan, 0, 0]), spec=exec_spec(**NOS),
             inst=instrument(tick=1), costs=Z, cash=10000.0)
out["X10_nan_signal"] = {"fills": len(r.fills)}
# X-11 manifest does not identify the data or the signals: different bars, different signals -> same hashes
b1 = mk_bars([(100, 101, 99, 100)] * 4); b2 = mk_bars([(200, 201, 199, 200)] * 4)
m1 = backtest(b1, 300, sig(4, [0]), spec=exec_spec(**NOS), costs=Z).manifest
m2 = backtest(b2, 300, sig(4, [1]), spec=exec_spec(**NOS), costs=Z).manifest
out["X11_manifest_identity"] = {"identical_manifests_for_different_runs": m1 == m2, "keys": sorted(m1)}
# X-12 exec_spec(product="CNC") keeps the MIS square-off time (setdefault on an existing key is a no-op)
out["X12_cnc_squareoff_in_spec"] = exec_spec(product="CNC")["squareoff_at"]
# X-13 no validation of OrderPlan values or session times: nonsense plans run and produce trades
B4 = [(100, 101, 99, 100.5), (102, 103, 101, 102.5), (103, 104, 102, 103), (103, 104, 102, 103)]
x13 = {}
for name, plan, spec in [("stop_pct=-0.05", dict(stop_pct=-0.05), {}), ("target_pct=-0.05", dict(target_pct=-0.05), {}),
                         ("squareoff_at=25:99", {}, dict(squareoff_at="25:99")),
                         ("slippage pct 3.0 (300 %)", {}, dict(slippage_model="pct", slippage_value=3.0))]:
    try:
        r = backtest(mk_bars(B4), 300, sig(4, [0]), spec=exec_spec(**{**NOS, **spec}), plan=order_plan(**plan),
                     inst=instrument(tick=1), costs=Z, cash=10000.0)
        x13[name] = F(r)
    except Exception as e:
        x13[name] = f"refused: {e}"
out["X13_unvalidated_inputs"] = x13
print(json.dumps(out, indent=1, default=str))
json.dump(out, open("/workspace/audit2/e_core_evidence.json", "w"), indent=1, default=str)
