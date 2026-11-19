"""Can the engine express NSE equity-intraday costs? (rates illustrative - must be versioned by date in production)"""
import sys, warnings; sys.path.insert(0, "/tmp/eval"); warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from backtesting import Backtest, Strategy
from parity_common import load_daily
calls = []
def nse_intraday_costs(size, price):
    v = abs(size) * price
    brokerage = min(20.0, 0.0003 * v)
    stt = 0.00025 * v if size < 0 else 0.0          # sell side only
    stamp = 0.00003 * v if size > 0 else 0.0        # buy side only
    txn = 0.0000297 * v; sebi = 0.000001 * v
    gst = 0.18 * (brokerage + txn + sebi)
    calls.append(("BUY" if size > 0 else "SELL", round(brokerage + stt + stamp + txn + sebi + gst, 2)))
    return brokerage + stt + stamp + txn + sebi + gst
class Once(Strategy):
    def init(self): pass
    def next(self):
        if len(self.data) == 10: self.buy(size=100)
        if len(self.data) == 20: self.position.close()
d = load_daily()["EQ00"].rename(columns=str.capitalize); d.index = d.index.tz_localize(None)
st = Backtest(d.iloc[:30], Once, cash=1_000_000, commission=nse_intraday_costs).run()
print("backtesting.py callable commission invocations (side, INR):", calls[-2:], "| commissions in stats:", round(st["Commissions [$]"], 2))
