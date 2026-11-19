import time, sys, warnings; sys.path.insert(0, "/tmp/eval"); warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, bt
from parity_common import *
data = load_daily()
class Toggle(bt.Algo):
    """Set weight 1.0 on an entry signal, 0.0 on an exit signal; otherwise do nothing (no drift rebalancing)."""
    def __init__(self, sym, ent, ex, index):
        super().__init__(); self.sym = sym
        self.ent = pd.Series(ent, index=index); self.ex = pd.Series(ex, index=index)
    def __call__(self, target):
        if self.ent.get(target.now, False): target.temp["weights"] = {self.sym: 1.0}; return True
        if self.ex.get(target.now, False):  target.temp["weights"] = {self.sym: 0.0}; return True
        return False
res = {}
mode = "close"
t0 = time.perf_counter()
for s, d in data.items():
    px = d[["close"]].rename(columns={"close": s}); px.index = px.index.tz_localize(None)
    ent, ex = signals(d["close"].to_numpy())
    strat = bt.Strategy(s, [Toggle(s, ent, ex, px.index), bt.algos.Rebalance()])
    test = bt.Backtest(strat, px, initial_capital=CASH, integer_positions=True,
                       commissions=lambda q, p: abs(q) * p * FEE, progress_bar=False)
    r = bt.run(test)
    tr = r.get_transactions()
    n = int((tr["quantity"] > 0).sum()) if len(tr) else 0
    res[s] = (float(r.backtests[s].strategy.values.iloc[-1]), n)
report("bt", res, time.perf_counter() - t0, mode)
