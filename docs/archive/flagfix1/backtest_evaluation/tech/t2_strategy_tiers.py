"""T2: what a user strategy costs per bar, by how it is expressed. Same logic everywhere:
close crosses above its 20-bar mean -> buy; below -> sell (decision at close t, fill at open t+1)."""
import json, sys, time
sys.path.insert(0, "/tmp/eval")
import numpy as np, numba as nb, hbt_core
import intraday_common as ic
d = ic.load_5m(1)["EQ00"]; o, c = np.ascontiguousarray(d.open.to_numpy()), np.ascontiguousarray(d.close.to_numpy())
N = 20; n = len(c)
# tier A: vectorised signal (numpy), engine in Rust
def vec_signal():
    m = np.convolve(c, np.ones(N) / N)[:n]; m[:N - 1] = np.nan
    above = c > m; prev = np.r_[False, above[:-1]]
    s = np.zeros(n, np.int8); s[above & ~prev] = 1; s[~above & prev] = -1; s[:N] = 0; return s
# tier B: numba cfunc called by the Rust loop through a C function pointer
@nb.cfunc(nb.int8(nb.types.CPointer(nb.float64), nb.int64), cache=True)
def rule(cp, t):
    if t < N: return 0
    a = 0.0; b = 0.0
    for k in range(N): a += cp[t - k]; b += cp[t - 1 - k]
    up, was = cp[t] > a / N, cp[t - 1] > b / N
    return 1 if up and not was else (-1 if was and not up else 0)
# tier C: plain Python callback per bar (what backtesting.py / backtrader / nautilus user code is)
csum = np.r_[0.0, np.cumsum(c)]
def py_cb(t):
    if t < N: return 0
    up = c[t] > (csum[t + 1] - csum[t + 1 - N]) / N; was = c[t - 1] > (csum[t] - csum[t - N]) / N
    return 1 if up and not was else (-1 if was and not up else 0)
res = {}
def T(name, fn, k=5):
    fn(); xs = []
    for _ in range(k):
        t0 = time.perf_counter(); r = fn(); xs.append(time.perf_counter() - t0)
    res[name] = {"ms": round(min(xs) * 1e3, 2), "ns_per_bar": round(min(xs) / n * 1e9, 1), "result": r}
    print(f"{name:58s} {min(xs)*1e3:9.2f} ms  {min(xs)/n*1e9:9.1f} ns/bar  -> {r}")
T("A vectorised numpy signal + Rust engine", lambda: hbt_core.drive_signals(o, c, vec_signal(), 1e6, 0.0003))
T("B numba @cfunc rule called from Rust via C pointer", lambda: hbt_core.drive_cfunc(o, c, rule.address, 1e6, 0.0003))
T("C Python callback per bar, Rust engine", lambda: hbt_core.drive_python(o, c, py_cb, 1e6, 0.0003), k=3)
assert res["A vectorised numpy signal + Rust engine"]["result"] == res["B numba @cfunc rule called from Rust via C pointer"]["result"] == res["C Python callback per bar, Rust engine"]["result"]
print(f"identical results across tiers; {n:,} bars. Python callback is {res['C Python callback per bar, Rust engine']['ns_per_bar']/res['B numba @cfunc rule called from Rust via C pointer']['ns_per_bar']:.0f}x the compiled callback")
json.dump(res, open("/workspace/backtest_evaluation/results/t2_strategy_tiers.json", "w"), indent=1, default=str)
