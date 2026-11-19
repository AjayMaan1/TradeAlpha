"""T9: intrabar ambiguity on 5m bars (stop AND target touched in the same bar) and resolving it with the stored 1m bars.
Bracket: stop 0.5 %, target 0.75 %; E5 signals; 20 symbols x 2 y. Modes: pessimistic (stop first), optimistic
(target first), magnifier (replay the five 1m bars; pessimistic only inside a single ambiguous 1m bar)."""
import json, sys
sys.path.insert(0, "/tmp/eval")
import numpy as np, numba as nb, polars as pl
import intraday_common as ic
m1 = pl.read_ipc("/tmp/eval/t3/m1.arrow").sort("instrument_id", "ts")
@nb.njit(cache=True)
def run(o, h, l, c, ent, ex, sq, h1, l1, mode, stop_pct, tgt_pct, fee):
    n = c.shape[0]; cash, qty, stop, tgt, pend = 1e6, 0.0, 0.0, 0.0, 0; amb = 0; exits = 0
    for t in range(n):
        if qty > 0 and sq[t]: cash += qty * o[t] * (1 - fee); qty = 0.0; pend = 0
        if pend == -1 and qty > 0: cash += qty * o[t] * (1 - fee); qty = 0.0
        new = False
        if pend == 1 and qty == 0 and not sq[t]:
            q = np.floor(cash / (o[t] * (1 + fee)))
            if q > 0: cash -= q * o[t] * (1 + fee); qty = q; stop = o[t] * (1 - stop_pct); tgt = o[t] * (1 + tgt_pct); new = True
        pend = 0
        if qty > 0:
            px = -1.0
            if not new and o[t] <= stop: px = o[t]
            elif not new and o[t] >= tgt: px = o[t]
            else:
                hs, ht = l[t] <= stop, h[t] >= tgt
                if hs and ht:
                    amb += 1
                    if mode == 0: px = stop
                    elif mode == 1: px = tgt
                    else:                                   # replay the 1m constituents in time order
                        for k in range(5):
                            a, b = l1[t * 5 + k] <= stop, h1[t * 5 + k] >= tgt
                            if a: px = stop; break           # (a and b inside one 1m bar -> stop, pessimistic)
                            if b: px = tgt; break
                elif hs: px = stop
                elif ht: px = tgt
            if px > 0: cash += qty * px * (1 - fee); qty = 0.0; exits += 1
        if t + 1 < n:
            if ent[t]: pend = 1
            elif ex[t]: pend = -1
    return cash + qty * c[n - 1], amb, exits
data = ic.load_5m(20); allres = []
for STOP_, TGT_ in ((0.005, 0.0075), (0.003, 0.0045), (0.002, 0.003)):
  out = {"pessimistic": [], "optimistic": [], "magnifier": []}; amb_total = exits_total = 0
  for k, (s, d) in enumerate(data.items()):
      sub = m1.filter(pl.col("instrument_id") == k + 1)
      assert sub.height == 5 * len(d) and (sub["ts"].to_numpy()[::5] == d.ts.to_numpy()).all()   # exact roll-up alignment
      h1, l1 = sub["high"].to_numpy(), sub["low"].to_numpy()
      e, x, sq = ic.signals(d); arr = [d[c].to_numpy() for c in ("open", "high", "low", "close")]
      for mode, name in enumerate(out):
          eq, amb, exits = run(*arr, e, x, sq, h1, l1, mode, STOP_, TGT_, 0.0003)
          out[name].append(eq)
          if mode == 0: amb_total += amb; exits_total += exits
  P, O_, M = (np.array(out[k]) for k in out)
  res = {"stop": STOP_, "target": TGT_, "symbols": len(data), "bracket_exits": exits_total, "ambiguous_bars": amb_total,
         "ambiguous_share_of_bracket_exits": amb_total / exits_total,
         "median_equity_gap_optimistic_vs_pessimistic": float(np.median(O_ / P - 1)),
         "magnifier_position_between_pess_and_opt_median": float(np.nanmedian(np.where(O_ != P, (M - P) / np.where(O_ != P, O_ - P, 1), np.nan))) if (O_ != P).any() else None}
  allres.append(res); print(json.dumps(res))
json.dump(allres, open('/workspace/backtest_evaluation/results/t9_bar_magnifier.json', 'w'), indent=1)
