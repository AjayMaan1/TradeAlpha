import time, sys, warnings, json; sys.path.insert(0, "/tmp/eval"); warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from backtesting import Backtest, Strategy
import parity_common as pc
from parity_common import load_daily, sma, CASH, FEE
data = load_daily()
class SmaCross(Strategy):
    fast, slow = 20, 50
    def init(self):
        c = np.asarray(self.data.Close); f, s = sma(c, self.fast), sma(c, self.slow)
        above = f > s; prev = np.r_[False, above[:-1]]; valid = ~np.isnan(s) & ~np.isnan(np.r_[np.nan, s[:-1]])
        self.ent, self.ex = above & ~prev & valid, ~above & prev & valid
    def next(self):
        i = len(self.data) - 1
        if self.ent[i] and not self.position: self.buy(size=0.99999999)
        elif self.ex[i] and self.position: self.position.close()
N = 3
t0 = time.perf_counter()
for s in list(data)[:N]:
    x = data[s].rename(columns=str.capitalize); x.index = x.index.tz_localize(None)
    bt = Backtest(x, SmaCross, cash=CASH, commission=FEE)
    st, heat = bt.optimize(fast=list(range(5, 55, 5)), slow=list(range(20, 220, 20)), maximize="Return [%]", return_heatmap=True)
dt = time.perf_counter() - t0
print(f"backtesting.py optimize: {N*100} backtests in {dt:.2f}s ({N*100/dt:,.1f}/s) -> 5000 est {5000/(N*100/dt):.0f}s")
json.dump({"engine": "backtesting", "backtests": N * 100, "seconds": dt}, open("/tmp/eval/sweep_backtesting.json", "w"))
