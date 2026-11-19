"""T6: target scale = 200 symbols x 10 y of 1m (937,500 bars/symbol, 187.5 M bars). Stream symbol by symbol through
the Rust kernel: EMA(9/21) x 4 stops, MIS 15:15 square-off, real intraday cost schedule. Measure time + peak RSS."""
import json, resource, sys, time
sys.path.insert(0, "/workspace/backtest_evaluation/prototype")
import numpy as np, hbt_core
from hbt import costs
S, DAYS, BPD = 200, 2500, 375
n = DAYS * BPD
day0 = 16436                                                       # 2015-01-01 as IST day number
tday = np.repeat(day0 + (np.arange(DAYS) * 7) // 5, BPD).astype(np.int64)   # weekday-ish sequence
mod = np.tile(555 + np.arange(BPD), DAYS).astype(np.int64)          # 09:15 .. 15:29
same_next = np.r_[tday[1:] == tday[:-1], False]; sq = mod == 915
rates = np.ascontiguousarray(costs.rate_vectors("EQ_INTRADAY", tday))
rng = np.random.default_rng(7)
t_gen = t_run = 0.0; bars = 0; total_trades = 0
for s in range(S):
    t0 = time.perf_counter()
    r = rng.normal(0, 0.018 / np.sqrt(BPD), n); c = 500 * np.exp(np.cumsum(r))
    o = np.r_[c[0], c[:-1]]; w = np.abs(rng.normal(0, 0.0006, (2, n)))
    h = np.maximum(o, c) * (1 + w[0]); l = np.minimum(o, c) * (1 - w[1])
    O, H, L, C = (np.ascontiguousarray(x.reshape(-1, 1)) for x in (o, h, l, c))
    t_gen += time.perf_counter() - t0
    t0 = time.perf_counter()
    out = hbt_core.sweep_ema_cross(O, H, L, C, mod, same_next, sq, rates, [9], [21], [0.003, 0.005, 0.0075, 0.01], 0, n, 895, 120, 1e6, 1.0)
    t_run += time.perf_counter() - t0
    bars += n; total_trades += int(out[:, 1].sum())
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
res = {"symbols": S, "bars_per_symbol": n, "total_bars": bars, "backtests": S * 4, "bar_steps": bars * 4,
       "kernel_seconds": round(t_run, 1), "data_gen_seconds": round(t_gen, 1), "peak_rss_mb": round(rss),
       "bar_steps_per_second_M": round(bars * 4 / t_run / 1e6, 1), "trades": total_trades}
print(json.dumps(res, indent=1))
json.dump(res, open("/workspace/backtest_evaluation/results/t6_scale.json", "w"), indent=1)
