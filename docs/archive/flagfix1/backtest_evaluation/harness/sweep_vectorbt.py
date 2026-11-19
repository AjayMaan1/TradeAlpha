import time, sys, warnings, json; sys.path.insert(0, "/tmp/eval"); warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, vectorbt as vbt
from parity_common import *
data = load_daily()
O = pd.DataFrame({s: d["open"] for s, d in data.items()}); C = pd.DataFrame({s: d["close"] for s, d in data.items()})
FASTS, SLOWS = list(range(5, 55, 5)), list(range(20, 220, 20))
def sweep():
    fast = vbt.MA.run(C, FASTS, short_name="fast"); slow = vbt.MA.run(C, SLOWS, short_name="slow")
    # cartesian product of (fast, slow) per symbol: 100 x 50 = 5000 columns
    f = fast.ma.reindex(columns=pd.MultiIndex.from_tuples([(a, b, s) for a in FASTS for b in SLOWS for s in C.columns], names=["fast", "slow", "sym"]))
    f = pd.DataFrame(np.hstack([fast.ma.xs(a, axis=1, level=0).to_numpy() for a in FASTS for b in SLOWS]), index=C.index, columns=f.columns)
    sl = pd.DataFrame(np.hstack([slow.ma.xs(b, axis=1, level=0).to_numpy() for a in FASTS for b in SLOWS]), index=C.index, columns=f.columns)
    above = f > sl; prev = above.shift(1, fill_value=False); valid = sl.notna() & sl.shift(1).notna()
    ent = (above & ~prev & valid).shift(1, fill_value=False); ex = (~above & prev & valid).shift(1, fill_value=False)
    tile = lambda X: pd.DataFrame(np.tile(X.to_numpy(), len(FASTS) * len(SLOWS)), index=C.index, columns=f.columns)
    pf = vbt.Portfolio.from_signals(tile(C), ent, ex, price=tile(O), size=np.inf, size_granularity=1, fees=FEE, init_cash=CASH, freq="1D")
    return pf
sweep()
t0 = time.perf_counter(); pf = sweep(); dt = time.perf_counter() - t0
tr = pf.total_return()
best = tr.groupby(level="sym").idxmax()
print(f"vectorbt sweep: {len(tr)} backtests in {dt:.2f}s  ({len(tr)/dt:,.0f} backtests/s)")
json.dump({"engine": "vectorbt", "backtests": len(tr), "seconds": dt, "best_EQ00": list(best["EQ00"])}, open("/tmp/eval/sweep_vectorbt.json", "w"))
# spot-check a random combo vs the reference implementation
import parity_common as pc
pc.FAST, pc.SLOW = 15, 120
r = pc.reference(data["EQ03"], "next_open")[0]
print("spot check (15,120,EQ03): vectorbt", round(pf.final_value()[(15, 120, "EQ03")], 2), "reference", round(r, 2))
