import sys, warnings; sys.path.insert(0, "/tmp/eval"); warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, vectorbt as vbt
from intraday_common import *
d = load_5m(1)["EQ00"]
o, h, lo, c = (d[k].to_numpy() for k in ("open", "high", "low", "close")); e, x, sq = signals(d)
ent = np.r_[False, e[:-1]] & ~sq; ex = np.r_[False, x[:-1]] | sq
# reference trade list
cash, sh, stop, pend, ref = CASH, 0, 0.0, 0, []
for t in range(len(c)):
    if sh > 0 and sq[t]: ref.append((ent_t, t, o[t], "sq")); cash += sh*o[t]*(1-FEE); sh = 0; pend = 0
    if pend == 1 and sh == 0 and not sq[t]:
        q = int(cash // (o[t]*(1+FEE)))
        if q > 0: cash -= q*o[t]*(1+FEE); sh = q; stop = o[t]*(1-STOP); ent_t = t
    elif pend == -1 and sh > 0: ref.append((ent_t, t, o[t], "sig")); cash += sh*o[t]*(1-FEE); sh = 0
    pend = 0
    if sh > 0 and lo[t] <= stop: px = min(o[t], stop); ref.append((ent_t, t, px, "stop")); cash += sh*px*(1-FEE); sh = 0
    pend = 1 if e[t] else -1 if x[t] else 0
pf = vbt.Portfolio.from_signals(c, ent, ex, price=o, open=o, high=h, low=lo, sl_stop=STOP, size=np.inf, size_granularity=1,
                                fees=FEE, init_cash=CASH, stop_entry_price="fillprice")
tr = pf.trades.records_readable
vb = list(zip(tr["Entry Timestamp"], tr["Exit Timestamp"], tr["Avg Exit Price"]))
print("reference trades", len(ref), " vectorbt trades", len(vb))
for k, (a, b) in enumerate(zip(ref, vb)):
    if a[0] != b[0] or a[1] != b[1] or abs(a[2] - b[2]) > 1e-6:
        print(f"first divergence at trade {k}:\n  reference entry bar {a[0]} exit bar {a[1]} px {a[2]:.4f} ({a[3]})\n  vectorbt  entry bar {b[0]} exit bar {b[1]} px {b[2]:.4f}")
        t = a[0]; print(f"  entry bar: open {o[t]:.2f} high {h[t]:.2f} low {lo[t]:.2f} close {c[t]:.2f}; stop {o[t]*(1-STOP):.4f} -> low<=stop on entry bar: {lo[t] <= o[t]*(1-STOP)}")
        break
