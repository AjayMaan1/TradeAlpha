import time, sys, warnings; sys.path.insert(0, "/tmp/eval"); warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
t_imp = time.perf_counter(); import vectorbt as vbt; t_imp = time.perf_counter() - t_imp
from parity_common import *
data = load_daily()
O = pd.DataFrame({s: d["open"] for s, d in data.items()}); C = pd.DataFrame({s: d["close"] for s, d in data.items()})
def run(mode):
    fast, slow = vbt.MA.run(C, FAST).ma, vbt.MA.run(C, SLOW).ma
    fast.columns = slow.columns = C.columns
    ent = (fast > slow) & (fast.shift(1) <= slow.shift(1)); ex = (fast < slow) & (fast.shift(1) >= slow.shift(1))
    # match the spec's definition exactly (state change of 'above')
    above = fast > slow; prev = above.shift(1, fill_value=False); valid = slow.notna() & slow.shift(1).notna()
    ent, ex = above & ~prev & valid, ~above & prev & valid
    if mode == "next_open":
        ent, ex, price = ent.shift(1, fill_value=False), ex.shift(1, fill_value=False), O
    else:
        price = C
    return vbt.Portfolio.from_signals(C, ent, ex, price=price, size=np.inf, size_granularity=1, fees=FEE,
                                      init_cash=CASH, freq="1D")
for mode in ("next_open", "close"):
    run(mode)                                  # first call includes numba JIT compile
    t0 = time.perf_counter(); pf = run(mode); dt = time.perf_counter() - t0
    fv = pf.final_value(); nt = pf.trades.count()
    report("vectorbt", {s: (fv[s], nt[s]) for s in C.columns}, dt, mode, {"import_s": round(t_imp, 2)})
# JIT warm-up cost in a fresh process
