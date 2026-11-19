import sys, json, itertools; sys.path.insert(0, "/tmp/eval")
from intraday_common import *
def ref_variant(df, check_entry_bar, gap_fill_at_open, stop_first):
    o, h, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close")); entry, exit_, sq = signals(df)
    cash, sh, stop, pend = CASH, 0, 0.0, 0
    for t in range(len(c)):
        stop_px = (min(o[t], stop) if gap_fill_at_open else stop)
        if stop_first and sh > 0 and lo[t] <= stop:
            cash += sh * stop_px * (1 - FEE); sh = 0
        elif sh > 0 and (sq[t] or pend == -1):
            cash += sh * o[t] * (1 - FEE); sh = 0
        elif not stop_first and sh > 0 and lo[t] <= stop:
            cash += sh * stop_px * (1 - FEE); sh = 0
        if pend == 1 and sh == 0 and not sq[t]:
            q = int(cash // (o[t] * (1 + FEE)))
            if q > 0:
                cash -= q * o[t] * (1 + FEE); sh = q; stop = o[t] * (1 - STOP)
                if check_entry_bar and lo[t] <= stop: cash += sh * stop * (1 - FEE); sh = 0
        pend = 1 if entry[t] else -1 if exit_[t] else 0
    return cash + sh * c[-1]
data = load_5m(); v = json.load(open("/tmp/eval/e5_vectorbt.json"))["stop_entry_price=fillprice"]["results"]
for ceb, gfo, sf in itertools.product([True, False], [True, False], [True, False]):
    err = np.array([abs(v[s][0] - ref_variant(d, ceb, gfo, sf)) / v[s][0] for s, d in data.items()])
    print(f"stop on entry bar={ceb!s:5s} gap fills at open={gfo!s:5s} stop before open-signals={sf!s:5s} -> exact {int((err<1e-9).sum()):2d}/50 max err {err.max():.2e}")
