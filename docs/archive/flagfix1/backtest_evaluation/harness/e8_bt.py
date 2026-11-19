import sys, warnings; sys.path.insert(0, "/tmp/eval"); warnings.filterwarnings("ignore")
import backtrader as bt
from parity_common import load_daily
calls = []
class NSECosts(bt.CommInfoBase):
    params = (("stocklike", True), ("commtype", bt.CommInfoBase.COMM_FIXED))
    def _getcommission(self, size, price, pseudoexec):
        v = abs(size) * price; stt = 0.00025 * v if size < 0 else 0.0; stamp = 0.00003 * v if size > 0 else 0.0
        c = min(20.0, 0.0003 * v) + stt + stamp
        if pseudoexec: calls.append(("BUY" if size > 0 else "SELL", round(c, 2)))
        return c
class Once(bt.Strategy):
    def next(self):
        if len(self) == 10: self.buy(size=100)
        if len(self) == 20: self.close()
d = load_daily()["EQ00"].iloc[:30].copy(); d.index = d.index.tz_localize(None)
c = bt.Cerebro(); c.adddata(bt.feeds.PandasData(dataname=d)); c.addstrategy(Once); c.broker.setcash(1e6); c.broker.addcommissioninfo(NSECosts())
c.run(); print("backtrader CommInfo._getcommission calls (side, INR):", calls)
