"""What a purpose-built Historify kernel costs: numba, one loop over (combo, symbol), next-open fills, whole shares."""
import time, sys, json; sys.path.insert(0, "/tmp/eval")
import numpy as np, numba as nb
from parity_common import load_daily, CASH, FEE, reference
import parity_common as pc
@nb.njit(cache=True)
def sma(x, n):
    out = np.full(x.shape[0], np.nan); acc = 0.0
    for i in range(x.shape[0]):
        acc += x[i]
        if i >= n: acc -= x[i - n]
        if i >= n - 1: out[i] = acc / n
    return out
@nb.njit(cache=True)
def run_one(o, c, fast, slow, fee, cash0):
    f, s = sma(c, fast), sma(c, slow)
    cash, sh, pend, prev_above = cash0, 0.0, 0, False
    for t in range(c.shape[0]):
        if pend == 1 and sh == 0:
            q = np.floor(cash / (o[t] * (1 + fee)))
            if q > 0: cash -= q * o[t] * (1 + fee); sh = q
        elif pend == -1 and sh > 0:
            cash += sh * o[t] * (1 - fee); sh = 0.0
        pend = 0
        above = f[t] > s[t]
        valid = t >= 1 and not np.isnan(s[t]) and not np.isnan(s[t - 1])
        if valid and t + 1 < c.shape[0]:
            if above and not prev_above: pend = 1
            elif (not above) and prev_above: pend = -1
        prev_above = above
    return cash + sh * c[-1]
@nb.njit(parallel=True, cache=True)
def sweep(O, C, fasts, slows, fee, cash0):
    nf, ns, nsym = fasts.shape[0], slows.shape[0], O.shape[1]
    out = np.empty((nf, ns, nsym))
    for k in nb.prange(nf * ns * nsym):
        i, j, m = k // (ns * nsym), (k // nsym) % ns, k % nsym
        out[i, j, m] = run_one(O[:, m], C[:, m], fasts[i], slows[j], fee, cash0)
    return out
data = load_daily(); syms = list(data)
O = np.column_stack([data[s].open.to_numpy() for s in syms]); C = np.column_stack([data[s].close.to_numpy() for s in syms])
F, S = np.arange(5, 55, 5), np.arange(20, 220, 20)
t0 = time.perf_counter(); sweep(O, C, F, S, FEE, CASH); jit = time.perf_counter() - t0
t0 = time.perf_counter(); R = sweep(O, C, F, S, FEE, CASH); dt = time.perf_counter() - t0
pc.FAST, pc.SLOW = 15, 120; ref = reference(data["EQ03"], "next_open")[0]
print(f"numba kernel: 5000 backtests in {dt:.3f}s ({5000/dt:,.0f}/s), first call incl. JIT/cache {jit:.1f}s; spot check EQ03(15,120) {R[2,5,3]:.2f} vs ref {ref:.2f}")
json.dump({"engine": "numba_kernel", "backtests": 5000, "seconds": dt, "jit_s": jit}, open("/tmp/eval/sweep_numba.json", "w"))
