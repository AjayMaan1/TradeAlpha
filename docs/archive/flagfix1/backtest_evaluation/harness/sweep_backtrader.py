import time, sys, warnings, json; sys.path.insert(0, "/tmp/eval"); warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, backtrader as bt
from parity_common import load_daily, CASH, FEE
data = load_daily()
class SmaCross(bt.Strategy):
    params = dict(fast=20, slow=50)
    def __init__(self):
        self.x = bt.ind.CrossOver(bt.ind.SMA(period=self.p.fast), bt.ind.SMA(period=self.p.slow)); self.pending = None
    def next_open(self):
        if self.pending == "buy" and not self.position:
            q = int(self.broker.getcash() // (self.data.open[0] * (1 + FEE)))
            if q: self.buy(size=q)
        elif self.pending == "sell" and self.position: self.close()
        self.pending = None
    def next(self):
        self.pending = "buy" if self.x[0] > 0 else "sell" if self.x[0] < 0 else None
x = data["EQ00"].copy(); x.index = x.index.tz_localize(None)
t0 = time.perf_counter()
c = bt.Cerebro(stdstats=False, cheat_on_open=True, maxcpus=4)
c.adddata(bt.feeds.PandasData(dataname=x)); c.broker.setcash(CASH); c.broker.setcommission(commission=FEE)
c.optstrategy(SmaCross, fast=list(range(5, 55, 5)), slow=list(range(20, 220, 20)))
r = c.run(); dt = time.perf_counter() - t0
print(f"backtrader optstrategy: 100 backtests in {dt:.2f}s ({100/dt:.1f}/s) -> 5000 est {5000/(100/dt):.0f}s")
json.dump({"engine": "backtrader", "backtests": 100, "seconds": dt}, open("/tmp/eval/sweep_backtrader.json", "w"))
