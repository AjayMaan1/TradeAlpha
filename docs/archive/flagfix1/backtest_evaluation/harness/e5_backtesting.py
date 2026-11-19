import sys, time, json, warnings; sys.path.insert(0, "/tmp/eval"); warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from backtesting import Backtest, Strategy
from intraday_common import *
data = load_5m()
class EmaMIS(Strategy):
    def init(self):
        d = self.data.df
        self.ent, self.ex, sq = self.sig
        self.sq_next = np.r_[sq[1:], False]                    # close at bar t -> executes at the 15:15 open
        self.next_open = np.r_[d["Open"].to_numpy()[1:], np.nan]
    def next(self):
        i = len(self.data) - 1
        if self.position and self.sq_next[i]:
            self.position.close(); return
        if self.ent[i] and not self.position:
            # bracket order: stop 0.5 % below the fill (= next open). Equivalent to a broker cover/bracket order.
            self.buy(size=0.99999999, sl=self.next_open[i] * (1 - STOP))
        elif self.ex[i] and self.position:
            self.position.close()
res = {}
t0 = time.perf_counter()
for s, d in data.items():
    x = pd.DataFrame({"Open": d.open.to_numpy(), "High": d.high.to_numpy(), "Low": d.low.to_numpy(), "Close": d.close.to_numpy(),
                      "Volume": d.volume.to_numpy()}, index=pd.to_datetime(d.ts, unit="s") + pd.Timedelta(hours=5, minutes=30))
    EmaMIS.sig = signals(d)
    st = Backtest(x, EmaMIS, cash=CASH, commission=FEE).run()
    res[s] = [float(st["Equity Final [$]"]), int(st["# Trades"])]
dt = time.perf_counter() - t0
json.dump({"engine": "backtesting", "seconds": dt, "results": res}, open("/tmp/eval/e5_backtesting.json", "w"))
print(f"backtesting.py 50 symbols x 39,150 5m bars: {dt:.1f}s")
