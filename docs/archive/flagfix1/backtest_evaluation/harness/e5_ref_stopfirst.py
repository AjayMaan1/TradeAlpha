import sys, json; sys.path.insert(0, "/tmp/eval")
from intraday_common import *
def reference_stop_first(df):
    o, h, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close")); entry, exit_, sq = signals(df)
    cash, sh, stop, pend = CASH, 0, 0.0, 0
    for t in range(len(c)):
        if sh > 0 and lo[t] <= stop:                                 # vectorbt rule: stop first, whole bar
            px = min(o[t], stop); cash += sh * px * (1 - FEE); sh = 0
        elif sh > 0 and (sq[t] or pend == -1):
            cash += sh * o[t] * (1 - FEE); sh = 0
        if pend == 1 and sh == 0 and not sq[t]:
            q = int(cash // (o[t] * (1 + FEE)))
            if q > 0:
                cash -= q * o[t] * (1 + FEE); sh = q; stop = o[t] * (1 - STOP)
                if lo[t] <= stop: cash += sh * stop * (1 - FEE); sh = 0
        pend = 1 if entry[t] else -1 if exit_[t] else 0
    return cash + sh * c[-1]
data = load_5m(); v = json.load(open("/tmp/eval/e5_vectorbt.json"))["stop_entry_price=fillprice"]["results"]
err = np.array([abs(v[s][0] - reference_stop_first(d)) / v[s][0] for s, d in data.items()])
print(f"vectorbt vs reference-with-stop-priority: max rel err {err.max():.2e}, exact {int((err<1e-9).sum())}/50")
