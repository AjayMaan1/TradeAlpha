import time, sys, warnings; sys.path.insert(0, "/tmp/eval"); warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from backtesting import Backtest, Strategy
from parity_common import *
data = load_daily()
class SmaCross(Strategy):
    def init(self):
        c = np.asarray(self.data.Close)
        self.ent, self.ex = signals(c)          # identical signal definition to the reference
    def next(self):
        i = len(self.data) - 1
        if self.ent[i] and not self.position:
            self.buy(size=0.99999999)            # all-in whole shares: int(margin_available // (open*(1+fee)))
        elif self.ex[i] and self.position:
            self.position.close()
def bt_df(d):
    x = d.rename(columns=str.capitalize); x.index = x.index.tz_localize(None); return x
res = {}
for mode in ("next_open", "close"):
    t0 = time.perf_counter()
    for s, d in data.items():
        st = Backtest(bt_df(d), SmaCross, cash=CASH, commission=FEE, trade_on_close=(mode == "close")).run()
        res[s] = (st["Equity Final [$]"], st["# Trades"])
    report("backtesting", res, time.perf_counter() - t0, mode)
