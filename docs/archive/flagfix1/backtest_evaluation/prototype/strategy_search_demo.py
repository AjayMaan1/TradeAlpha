"""
Strategy generation done safely: big parallel grid search -> walk-forward -> deflated Sharpe.
Data: 50 symbols x 2y of 5m (Historify roll-up from 1m). Prices are GBM with regime drift: there is NO intraday edge
to find, so a correct pipeline must report the in-sample "winners" as not significant.
"""
import json, resource, sys, time
sys.path.insert(0, ".")
import numpy as np
from hbt import data as hd, costs, kernel as K, metrics as M

u = hd.from_parquet_dir("/tmp/eval/m5", "historify 5m roll-up of 1m (session-anchored)")
n, S = u.close.shape
tod, same_next, sq = u.minute_of_day, u.same_session_next, u.mask_at(15 * 60 + 15)
rates = costs.rate_vectors("EQ_INTRADAY", u.trade_day)
F = np.array([5, 8, 9, 12, 15, 20, 25, 30]); SL = np.array([21, 30, 40, 50, 60, 80, 100, 120])
ST = np.array([0.003, 0.005, 0.0075, 0.01])
valid_pairs = int(sum(f < s for f in F for s in SL)); combos = valid_pairs * len(ST)
BPY = M.bars_per_year(248, 75)
args = lambda lo, hi: (u.open, u.high, u.low, u.close, tod, same_next, sq, rates, F, SL, ST, lo, hi, 14 * 60 + 55, 120, 1e6, 1.0)

t0 = time.perf_counter(); K.sweep_ema_cross(*args(0, 2000)); jit = time.perf_counter() - t0
t0 = time.perf_counter(); full = K.sweep_ema_cross(*args(0, n)); dt = time.perf_counter() - t0
n_bt = combos * S
print(f"grid: {combos} parameter sets x {S} symbols = {n_bt:,} backtests x {n:,} bars = {n_bt*n/1e9:.2f} G bar-steps "
      f"in {dt:.1f}s ({n_bt/dt:,.0f} backtests/s; compile/cache {jit:.1f}s; peak RSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024:.0f} MB)")

# ---- walk-forward: 4 half-year windows, choose best in-sample set per symbol, evaluate on the next window
days = u.trade_day; ud = np.unique(days); w = np.array_split(ud, 4)
bounds = [(int(np.searchsorted(days, x[0])), int(np.searchsorted(days, x[-1], side="right"))) for x in w]
res = [K.sweep_ema_cross(*args(lo, hi)) for lo, hi in bounds]
is_sr, oos_sr = [], []
mask = np.array([[f < s for s in SL] for f in F])[:, :, None]            # invalid (fast>=slow) cells are empty
for k in range(3):
    sr_is = np.where(mask[..., None], M.sharpe(res[k], BPY), -np.inf)      # (nf, ns, nstop, nsym)
    sr_oos = M.sharpe(res[k + 1], BPY)
    flat = sr_is.reshape(-1, S); best = flat.argmax(axis=0)
    is_sr.append(flat[best, np.arange(S)]); oos_sr.append(sr_oos.reshape(-1, S)[best, np.arange(S)])
is_sr, oos_sr = np.concatenate(is_sr), np.concatenate(oos_sr)
print(f"walk-forward (3 folds x {S} symbols): in-sample Sharpe of the chosen set  mean {is_sr.mean():+.2f}  |  "
      f"out-of-sample Sharpe of the same set  mean {oos_sr.mean():+.2f}  (OOS > 0 in {np.mean(oos_sr > 0):.0%} of cases)")

# ---- deflated Sharpe for the single best (symbol, set) over the whole sample
sr_pp = np.where(mask[..., None], M.sharpe(full, BPY, annualize=False), np.nan)
vals = sr_pp[~np.isnan(sr_pp)]
best = float(np.nanmax(sr_pp)); trials = vals.size
dsr = M.deflated_sharpe(best, n_obs=n, n_trials=trials, sr_variance=float(vals.var()))
print(f"best of {trials:,} tries: annualised Sharpe {best*np.sqrt(BPY):+.2f}; expected max under no skill "
      f"{M.expected_max_sharpe(trials, float(vals.var()))*np.sqrt(BPY):+.2f}; deflated Sharpe probability {dsr:.2f} "
      f"-> {'significant' if dsr > 0.95 else 'NOT significant (as it should be on data with no edge)'}")
fees, ntr = full[..., K.R_FEES], full[..., K.R_TRADES]
print(f"realistic NSE intraday costs (brokerage cap, STT sell, stamp buy, versioned Oct-2024): "
      f"median fees per trade INR {np.median(fees[ntr > 0] / ntr[ntr > 0]):.0f}")
json.dump({"combos": combos, "symbols": S, "backtests": n_bt, "bars": n, "seconds": dt, "backtests_per_s": n_bt / dt,
           "wf_is_mean_sharpe": float(is_sr.mean()), "wf_oos_mean_sharpe": float(oos_sr.mean()),
           "best_sharpe_ann": best * np.sqrt(BPY), "dsr": dsr, "trials": trials},
          open("/workspace/backtest_evaluation/results/strategy_search_demo.json", "w"), indent=1)
