import sys, time, json, warnings; sys.path.insert(0, "/tmp/eval"); warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, vectorbt as vbt
from intraday_common import *
data = load_5m(); syms = list(data)
idx = pd.to_datetime(data[syms[0]]["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
M = lambda k: pd.DataFrame({s: data[s][k].to_numpy() for s in syms}, index=idx)
O, H, L, C = M("open"), M("high"), M("low"), M("close")
E, X, SQ = {}, {}, {}
for s in syms:
    e, x, q = signals(data[s]); E[s], X[s], SQ[s] = e, x, q
E, X, SQ = (pd.DataFrame(v, index=idx) for v in (E, X, SQ))
ent = E.shift(1, fill_value=False) & ~SQ                 # execute at the open of t+1, never on the square-off bar
ex = X.shift(1, fill_value=False) | SQ
def run(**kw):
    return vbt.Portfolio.from_signals(C, ent, ex, price=O, open=O, high=H, low=L, sl_stop=STOP, size=np.inf,
                                      size_granularity=1, fees=FEE, init_cash=CASH, freq="5min", **kw)
out = {}
for label, kw in (("defaults", {}), ("stop_entry_price=fillprice", {"stop_entry_price": "fillprice"})):
    run(**kw); t0 = time.perf_counter(); pf = run(**kw); dt = time.perf_counter() - t0
    fv = pf.final_value(); nt = pf.trades.count()
    out[label] = {"seconds": dt, "results": {s: [float(fv[s]), int(nt[s])] for s in syms}}
    print(f"vectorbt [{label}] 50 symbols x {len(idx):,} 5m bars: {dt:.2f}s")
    if label != "defaults":
        sr_default = pf.sharpe_ratio()

        r = pf.returns()
        bars_per_year_vbt = pd.Timedelta(days=365) / pd.Timedelta(minutes=5)
        out["sharpe_default_EQ00"] = float(sr_default["EQ00"])
        out["sharpe_manual_vbt_factor_EQ00"] = float(r["EQ00"].mean() / r["EQ00"].std() * np.sqrt(bars_per_year_vbt))
        out["sharpe_manual_nse_factor_EQ00"] = float(r["EQ00"].mean() / r["EQ00"].std() * np.sqrt(75 * 248))
        out["bars_per_year"] = {"vectorbt_default": bars_per_year_vbt, "nse_actual": 75 * 248}
json.dump(out, open("/tmp/eval/e5_vectorbt.json", "w"), default=float)
print({k: v for k, v in out.items() if k.startswith("sharpe") or k == "bars_per_year"})
