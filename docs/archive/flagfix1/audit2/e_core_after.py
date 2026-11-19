"""Audit 2 evidence re-run AFTER fix pack 1.2.1 (same scenarios as e_core_evidence.py, current API).
Writes e_core_after.json next to the 'before' file e_core_evidence.json."""
import json, sys, warnings
from datetime import date
H = "/workspace/historify-master/historify"
sys.path[:0] = [H, H + "/lab"]
warnings.simplefilter("ignore")
import duckdb, hbt_core, numpy as np, pandas as pd
from hbt import Dataset, backtest, bars_from_frame, core_events, exec_spec, instrument, order_plan, periods_per_year
from hbt.costs import cost_model
from tests.lab.conftest import mk_bars, sig

Z, out = cost_model(zero=True), {}
F = lambda r: r.fills[["bar", "buy", "qty", "price", "reason"]].values.tolist()
NOS = dict(squareoff_at=None, no_new_entries_after=None)
IST = 19_800
days = pd.bdate_range("2025-03-03", periods=8)
ts_d = ((days - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s")).to_numpy("int64") - IST
c = np.arange(100, 108.0)
daily = bars_from_frame(pd.DataFrame({"ts": ts_d, "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 10**6}))
le = np.zeros(8, bool); le[1] = True
def attempt(name, f):
    try:
        out[name] = f()
    except Exception as e:
        out[name] = f"refused: {type(e).__name__}: {str(e)[:120]}"
attempt("X01_daily_defaults", lambda: (lambda r: {"fills": len(r.fills), "rejects": {k: v for k, v in r.rejects.items() if v}})(
    backtest(daily, 86400, le, spec=exec_spec(product="CNC"), costs=cost_model("EQ_DELIVERY", brokerage="none"))))
attempt("X02_daily_limit_entry", lambda: (lambda r: {"fills": F(r)})(backtest(daily, 86400, le, spec=exec_spec(product="CNC"),
    plan=order_plan(entry_type="limit"), costs=Z)))
rows = [(100, 101, 99, 100), (100, 101, 99, 100, 300), (100, 101, 99, 100, 100000), (100, 101, 99, 100), (100, 101, 99, 100)]
kw = dict(spec=exec_spec(participation_cap=0.1, **NOS), plan=order_plan(sizing="fixed_qty", sizing_value=50),
          inst=instrument(tick=1), costs=Z, cash=10000.0)
attempt("X03_exit_then_reentry", lambda: (lambda r: {"fills": F(r), "final_position": r.position})(
    backtest(mk_bars(rows), 300, sig(5, [0]), sig(5, [1]), **kw)))
attempt("X03b_reversal", lambda: (lambda r: {"fills": F(r), "final_position": r.position})(
    backtest(mk_bars(rows), 300, sig(5, [0]), short_entry=sig(5, [1]), **kw)))
bdays = pd.bdate_range("2025-03-03", periods=5)
ts_b = ((bdays - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s")).to_numpy("int64") - IST
df = pd.DataFrame({"ts": ts_b, "open": [1234.55, 1240.05, 1250.1, 620, 622.5], "high": [1240, 1250, 1260, 625, 630.0],
                   "low": [1230, 1235, 1245, 615, 620.0], "close": [1238.35, 1247.45, 1255.55, 621.3, 627.15], "volume": 1000})
ev = [{"ex_date": str(bdays[3].date()), "kind": "bonus", "ratio_num": 1, "ratio_den": 1}]
attempt("X04_adjusted_bars", lambda: (lambda ds: {"signal_close": [round(x, 3) for x in ds.sig["close"][:3]],
                                                   "exec_close_paise": ds.exec["close"][:3].tolist()})(
    Dataset.from_frame(df, "D", adjustment="adjusted", events=ev)))
attempt("X04b_raw_through_bonus", lambda: (lambda r: {"position": r.position, "equity_before": float(r.equity.iloc[2]),
                                                       "equity_after_ex_date": float(r.equity.iloc[3])})(
    backtest(bars_from_frame(df), 86400, np.array([1, 0, 0, 0, 0]), spec=exec_spec(product="CNC"), costs=Z,
             cash=100000.0, events=core_events(ev))))
rows5 = [(100, 101, 99, 100.5), (102, 103, 101, 102.5, 0), (103, 104, 102, 103), (103, 104, 102, 103)]
attempt("X05_zero_volume_fill", lambda: {"fills": F(backtest(mk_bars(rows5), 300, sig(4, [0]),
    spec=exec_spec(participation_cap=None, **NOS), inst=instrument(tick=1), costs=Z, cash=10000.0))})
TWO = dict(days=[(date(2025, 3, 27), "09:15", 2), (date(2025, 3, 28), "09:15", 2)])
rows6 = [(100, 100, 100, 100), (100, 101, 99, 100), (104, 105, 103, 104, 300), (104, 104, 104, 104, 100000)]
attempt("X06_forced_partial", lambda: (lambda r: {"fills": F(r), "final_mis_position": r.position})(
    backtest(mk_bars(rows6, **TWO), 300, sig(4, [0]), spec=exec_spec(participation_cap=0.1, **NOS),
             inst=instrument(tick=1), costs=Z, cash=10000.0)))
dfn = pd.DataFrame({"ts": mk_bars([(1, 1, 1, 1)] * 3)["ts"], "open": 100.0, "high": 101.0, "low": [99, np.nan, 99.0],
                    "close": 100.0, "volume": 1000})
attempt("X07_nan_prices_frame", lambda: bars_from_frame(dfn))
attempt("X07_nan_prices_arrow", lambda: hbt_core.bars_from_arrow(duckdb.connect().sql(
    "select 1743047100::BIGINT ts, 100.0::DOUBLE open, 101.0::DOUBLE high, 'NaN'::DOUBLE low, 100.0::DOUBLE close, 5::BIGINT volume")))
dts = pd.date_range("2025-03-27 09:15", periods=3, freq="5min", tz="Asia/Kolkata")
attempt("X08_ts_unit", lambda: bars_from_frame(pd.DataFrame({"ts": dts, "open": 100.0, "high": 101.0, "low": 99.0,
                                                             "close": 100.0, "volume": 10})))
out["X09_periods_per_year"] = {"D": periods_per_year(86400), "W": periods_per_year(7 * 86400), "M": periods_per_year(30 * 86400)}
attempt("X10_nan_signal", lambda: backtest(mk_bars([(100, 101, 99, 100)] * 3), 300, np.array([np.nan, 0, 0]),
                                           spec=exec_spec(**NOS), inst=instrument(tick=1), costs=Z))
b1 = mk_bars([(100, 101, 99, 100)] * 4); b2 = mk_bars([(200, 201, 199, 200)] * 4)
m1 = backtest(b1, 300, sig(4, [0]), spec=exec_spec(**NOS), costs=Z).manifest
m2 = backtest(b2, 300, sig(4, [1]), spec=exec_spec(**NOS), costs=Z).manifest
out["X11_manifest_identity"] = {"identical_manifests_for_different_runs": m1 == m2}
out["X12_cnc_squareoff_in_spec"] = exec_spec(product="CNC")["squareoff_at"]
B4 = [(100, 101, 99, 100.5), (102, 103, 101, 102.5), (103, 104, 102, 103), (103, 104, 102, 103)]
x13 = {}
for name, plan, spec in [("stop_pct=-0.05", dict(stop_pct=-0.05), {}), ("target_pct=-0.05", dict(target_pct=-0.05), {}),
                         ("squareoff_at=25:99", {}, dict(squareoff_at="25:99")),
                         ("slippage pct 3.0 (300 %)", {}, dict(slippage_model="pct", slippage_value=3.0))]:
    try:
        backtest(mk_bars(B4), 300, sig(4, [0]), spec=exec_spec(**{**NOS, **spec}), plan=order_plan(**plan),
                 inst=instrument(tick=1), costs=Z, cash=10000.0)
        x13[name] = "accepted"
    except ValueError as e:
        x13[name] = f"refused: {str(e)[:80]}"
out["X13_unvalidated_inputs"] = x13
print(json.dumps(out, indent=1, default=str))
json.dump(out, open("/workspace/audit2/e_core_after.json", "w"), indent=1, default=str)
