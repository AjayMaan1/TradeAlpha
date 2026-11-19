import sys, time, json, resource, warnings, gc; sys.path.insert(0, "/tmp/eval"); warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, vectorbt as vbt
from intraday_common import load_5m, ema, minute_of_day, STOP
K = int(sys.argv[1])                       # number of (fast, slow, stop) parameter sets
data = load_5m(); syms = list(data)
ts = data[syms[0]].ts.to_numpy(); idx = pd.to_datetime(ts, unit="s", utc=True)
M = lambda k: np.column_stack([data[s][k].to_numpy() for s in syms])
O, H, L, C = M("open"), M("high"), M("low"), M("close")
mod = minute_of_day(ts); day = (ts + 19800) // 86400; same_next = np.r_[day[1:] == day[:-1], False]; sq = mod == 915
pairs = [(f, s) for f in (5, 8, 9, 12, 15, 20, 25, 30) for s in (21, 30, 40, 50, 60, 80, 100, 120) if f < s]
sets = [(f, s, st) for (f, s) in pairs for st in (0.003, 0.005, 0.0075, 0.01)][:K]
t0 = time.perf_counter()
ent = np.zeros((len(ts), K * len(syms)), bool); ex = np.zeros_like(ent); sl = np.zeros(K * len(syms))
for k, (f, s, st) in enumerate(sets):
    for j in range(len(syms)):
        ef, es = ema(C[:, j], f), ema(C[:, j], s); a = ef > es; p = np.r_[False, a[:-1]]
        e = a & ~p & (mod <= 895) & same_next; e[:120] = False; x = ~a & p & same_next
        col = k * len(syms) + j
        ent[:, col] = np.r_[False, e[:-1]] & ~sq; ex[:, col] = np.r_[False, x[:-1]] | sq; sl[col] = st
t_sig = time.perf_counter() - t0; t0 = time.perf_counter()
tile = lambda X: np.tile(X, (1, K))
pf = vbt.Portfolio.from_signals(tile(C), ent, ex, price=tile(O), open=tile(O), high=tile(H), low=tile(L), sl_stop=sl[None, :],
                                size=np.inf, size_granularity=1, fees=0.0003, init_cash=1e6, freq="5min", stop_entry_price="fillprice")
fv = pf.final_value(); sr = pf.sharpe_ratio()
dt = time.perf_counter() - t0
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(json.dumps({"K": K, "backtests": K * len(syms), "signal_build_s": round(t_sig, 2), "vectorbt_s": round(dt, 2), "peak_rss_mb": round(rss)}))
