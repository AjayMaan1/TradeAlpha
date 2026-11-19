"""P2: cost of decision latency. E5 strategy (5m EMA 9/21 signals, 0.5 % stop, MIS 15:15), executed on the 1m timeline:
fill at the open of the 1m bar k minutes after the 5m signal bar closes (k=0 == next 5m open). Stops are checked on
1m bars (stop live from the fill bar). Same signals for every k; only execution timing moves."""
import json, sys
sys.path.insert(0, "/tmp/eval")
import numpy as np, numba as nb, polars as pl
import intraday_common as ic
m1 = pl.read_ipc("/tmp/eval/t3/m1.arrow").sort("instrument_id", "ts")

@nb.njit(cache=True)
def run(o, l, c, mod, ent5, ex5, k, stop_pct, fee):
    n = c.shape[0]; cash, qty, stop = 1e6, 0.0, 0.0; trades = 0; rets = 0.0
    act = np.zeros(n, np.int8)                       # 1m bar index -> action at its open
    for j in range(ent5.shape[0]):
        i = 5 * (j + 1) + k
        if i < n and (ent5[j] or ex5[j]) and (i // 375) == ((5 * j) // 375):   # same session
            act[i] = 1 if ent5[j] else -1
    entry_px = 0.0
    for t in range(n):
        if qty > 0 and mod[t] == 915:
            cash += qty * o[t] * (1 - fee); rets += o[t] / entry_px - 1; qty = 0.0
        if act[t] == -1 and qty > 0:
            cash += qty * o[t] * (1 - fee); rets += o[t] / entry_px - 1; qty = 0.0
        elif act[t] == 1 and qty == 0 and mod[t] < 915:
            q = np.floor(cash / (o[t] * (1 + fee)))
            if q > 0:
                cash -= q * o[t] * (1 + fee); qty = q; entry_px = o[t]; stop = o[t] * (1 - stop_pct); trades += 1
        if qty > 0 and l[t] <= stop:
            px = min(o[t], stop); cash += qty * px * (1 - fee); rets += px / entry_px - 1; qty = 0.0
    return cash + qty * c[n - 1], trades, rets

data = ic.load_5m(); out = {k: [] for k in (0, 1, 2, 3)}; per_trade = {k: [] for k in out}
for idx, (s, d) in enumerate(data.items()):
    sub = m1.filter(pl.col("instrument_id") == idx + 1)
    o, l, c, ts = (sub[x].to_numpy() for x in ("open", "low", "close", "ts"))
    mod = ((ts + 19800) % 86400) // 60
    e, x, _ = ic.signals(d)
    for k in out:
        eq, n, r = run(o, l, c, mod, e, x, k, ic.STOP, ic.FEE)
        out[k].append(eq); per_trade[k].append(r / max(n, 1))
res = {}
for k in out:
    d_eq = np.array(out[k]) / np.array(out[0]) - 1
    d_tr = (np.array(per_trade[k]) - np.array(per_trade[0])) * 1e4
    res[k] = {"median_equity_change_vs_k0": float(np.median(d_eq)), "median_per_trade_change_bp": float(np.median(d_tr)),
              "p10_p90_per_trade_bp": [float(np.percentile(d_tr, 10)), float(np.percentile(d_tr, 90))]}
    print(f"latency {k} min: median final-equity change vs next-open {np.median(d_eq):+.2%}; per-trade return change "
          f"median {np.median(d_tr):+.2f} bp (p10 {np.percentile(d_tr,10):+.2f}, p90 {np.percentile(d_tr,90):+.2f})")
json.dump(res, open("/workspace/backtest_evaluation/results/p2_decision_latency.json", "w"), indent=1)
