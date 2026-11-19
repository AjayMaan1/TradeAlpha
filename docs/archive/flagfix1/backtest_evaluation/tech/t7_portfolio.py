"""T7: portfolio mode (time-major, shared cash). 50 symbols x 195,750 1m bars. EMA(9/21) entries per symbol, at most
10 concurrent positions, each sized equity/10 at the next open, 0.5 % stop active on the entry bar, MIS square-off 15:15."""
import json, resource, time
import numpy as np, numba as nb, polars as pl
df = pl.read_ipc("/tmp/eval/t3/m1.arrow").sort("instrument_id", "ts")
S = 50; n = df.height // S
M = {k: np.ascontiguousarray(df[k].to_numpy().reshape(S, n).T) for k in ("open", "high", "low", "close")}
ts = df["ts"].to_numpy()[:n]; mod = ((ts + 19800) % 86400) // 60; day = (ts + 19800) // 86400
same_next = np.r_[day[1:] == day[:-1], False]; sq = mod == 915
@nb.njit(parallel=True, cache=True)
def signals(C, same_next, mod):
    n, S = C.shape; ent = np.zeros((n, S), np.bool_); ex = np.zeros((n, S), np.bool_)
    for s in nb.prange(S):
        a9, a21 = 2 / 10, 2 / 22; f = sl = C[0, s]; prev = False
        for t in range(n):
            f = a9 * C[t, s] + (1 - a9) * f; sl = a21 * C[t, s] + (1 - a21) * sl; up = f > sl
            if t > 120 and same_next[t]:
                if up and not prev and mod[t] <= 895: ent[t, s] = True
                elif prev and not up: ex[t, s] = True
            prev = up
    return ent, ex
@nb.njit(cache=True)
def portfolio(O, H, L, C, ent, ex, sq, cash0, fee, stop_pct, max_pos):
    n, S = C.shape; cash = cash0; qty = np.zeros(S); stop = np.zeros(S); pend = np.zeros(S, np.int8)
    npos = 0; trades = 0; peak = cash0; maxdd = 0.0
    for t in range(n):
        for s in range(S):                                   # exits at the open first
            if qty[s] > 0 and (sq[t] or pend[s] == -1):
                cash += qty[s] * O[t, s] * (1 - fee); qty[s] = 0.0; npos -= 1
        eq = cash
        for s in range(S): eq += qty[s] * O[t, s]
        for s in range(S):                                   # entries at the open, deterministic symbol order
            if pend[s] == 1 and qty[s] == 0 and npos < max_pos and not sq[t]:
                q = np.floor(min(eq / max_pos, cash) / (O[t, s] * (1 + fee)))
                if q > 0:
                    cash -= q * O[t, s] * (1 + fee); qty[s] = q; stop[s] = O[t, s] * (1 - stop_pct); npos += 1; trades += 1
        for s in range(S):                                   # stops inside the bar
            if qty[s] > 0 and L[t, s] <= stop[s]:
                px = min(O[t, s], stop[s]); cash += qty[s] * px * (1 - fee); qty[s] = 0.0; npos -= 1
            pend[s] = 1 if ent[t, s] else (-1 if ex[t, s] else 0)
        eq = cash
        for s in range(S): eq += qty[s] * C[t, s]
        if eq > peak: peak = eq
        if 1 - eq / peak > maxdd: maxdd = 1 - eq / peak
    for s in range(S): cash += qty[s] * C[n - 1, s]
    return cash, trades, maxdd
ent, ex = signals(M["close"], same_next, mod)
portfolio(M["open"], M["high"], M["low"], M["close"], ent, ex, sq, 1e7, 0.0003, 0.005, 10)
t0 = time.perf_counter(); ent, ex = signals(M["close"], same_next, mod); ts_sig = time.perf_counter() - t0
t0 = time.perf_counter(); r = portfolio(M["open"], M["high"], M["low"], M["close"], ent, ex, sq, 1e7, 0.0003, 0.005, 10); tp = time.perf_counter() - t0
res = {"symbols": S, "bars": n, "cells": n * S, "signals_s": round(ts_sig, 3), "portfolio_s": round(tp, 3),
       "ns_per_cell": round(tp / (n * S) * 1e9, 2), "final_equity": r[0], "trades": r[1], "max_dd": r[2],
       "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)}
print(json.dumps(res, indent=1)); json.dump(res, open("/workspace/backtest_evaluation/results/t7_portfolio.json", "w"), indent=1)
