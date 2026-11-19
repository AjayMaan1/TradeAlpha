import time, sys, warnings, math; sys.path.insert(0, "/tmp/eval"); warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, backtrader as bt
from parity_common import *
data = load_daily()
class SmaCross(bt.Strategy):
    params = dict(mode="next_open", ent=None, ex=None)
    def __init__(self): self.pending = None
    def _act(self, side, px):
        if side == "buy" and not self.position:
            q = int(self.broker.getcash() // (px * (1 + FEE)))
            if q > 0: self.buy(size=q)
        elif side == "sell" and self.position:
            self.close()
    def next_open(self):                          # cheat-on-open: runs before the bar's open is matched
        if self.p.mode == "next_open" and self.pending:
            self._act(self.pending, self.data.open[0]); self.pending = None
    def next(self):
        i = len(self.data) - 1
        side = "buy" if self.p.ent[i] else "sell" if self.p.ex[i] else None
        if side is None: return
        if self.p.mode == "next_open": self.pending = side
        else: self._act(side, self.data.close[0])
res = {}
for mode in ("next_open", "close"):
    t0 = time.perf_counter()
    for s, d in data.items():
        ent, ex = signals(d["close"].to_numpy())
        c = bt.Cerebro(stdstats=False, cheat_on_open=(mode == "next_open"))
        x = d.copy(); x.index = x.index.tz_localize(None)
        c.adddata(bt.feeds.PandasData(dataname=x))
        c.addstrategy(SmaCross, mode=mode, ent=ent, ex=ex)
        c.broker.setcash(CASH); c.broker.setcommission(commission=FEE)
        if mode == "close": c.broker.set_coc(True)   # cheat-on-close: fill at the signal bar's close
        c.addanalyzer(bt.analyzers.TradeAnalyzer, _name="ta")
        strat = c.run()[0]
        ta = strat.analyzers.ta.get_analysis()
        n = ta.get("total", {}).get("total", 0)
        res[s] = (c.broker.getvalue(), n)
    report("backtrader", res, time.perf_counter() - t0, mode)
