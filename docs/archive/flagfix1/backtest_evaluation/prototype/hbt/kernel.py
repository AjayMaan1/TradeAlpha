"""
Historify execution kernel (prototype): one bar-by-bar state machine with EXPLICIT, documented semantics.

Order of events inside bar t (chronological, like a real session):
  1. at the OPEN   : MIS square-off (if squareoff[t])  ->  pending signal exit  ->  pending signal entry
  2. inside the bar: protective stop / target of the open position, ACTIVE ON THE ENTRY BAR (bracket/cover order).
                     Gap through the stop -> filled at the open. Stop and target both touched -> stop first
                     (pessimistic: OHLC bars carry no intrabar sequence).
  3. at the CLOSE  : read entry/exit signals of bar t -> pending for the open of t+1 (fill_mode 0)
                     or filled immediately at the close (fill_mode 1).
Sizing: all-in, whole lots: floor(cash / (px * (1 + cost%)) / lot) * lot, reduced by one lot while unaffordable
(brokerage cap makes cost non-linear). Costs: side-aware per-bar rates from hbt.costs.rate_vectors.
Stats are accumulated on the fly (no equity-curve arrays) so very large sweeps stay in cache.
"""
from __future__ import annotations

import numba as nb
import numpy as np

# result vector layout
R_EQUITY, R_TRADES, R_STOPS, R_TARGETS, R_SQOFF, R_RET_N, R_RET_MEAN, R_RET_M2, R_MAXDD, R_FEES = range(10)
N_RESULT = 10


@nb.njit(cache=True, inline="always")
def _fee(value, pct, brok_pct, brok_cap):
    b = brok_pct * value
    if brok_cap > 0.0 and b > brok_cap:
        b = brok_cap
    return value * pct + b


@nb.njit(cache=True)
def simulate(o, h, l, c, entry, exit_, squareoff, rates, stop_pct, target_pct, cash0, lot, fill_mode, out):
    n = c.shape[0]
    cash, qty, stop, target, pending = cash0, 0.0, 0.0, 0.0, 0
    ntr = nst = ntg = nsq = 0
    fees = 0.0
    prev_eq, peak, maxdd = cash0, cash0, 0.0
    rn, rmean, rm2 = 0, 0.0, 0.0
    for t in range(n):
        # ---- 1. events at the open
        if qty > 0.0 and squareoff[t]:
            f = _fee(qty * o[t], rates[t, 1], rates[t, 2], rates[t, 3])
            cash += qty * o[t] - f; fees += f; qty = 0.0; nsq += 1; pending = 0
        if pending == -1 and qty > 0.0:
            f = _fee(qty * o[t], rates[t, 1], rates[t, 2], rates[t, 3])
            cash += qty * o[t] - f; fees += f; qty = 0.0
        entered_now = False
        if pending == 1 and qty == 0.0 and not squareoff[t]:
            px = o[t]
            q = np.floor(cash / (px * (1.0 + rates[t, 0] + rates[t, 2])) / lot) * lot
            while q > 0.0 and q * px + _fee(q * px, rates[t, 0], rates[t, 2], rates[t, 3]) > cash:
                q -= lot
            if q > 0.0:
                f = _fee(q * px, rates[t, 0], rates[t, 2], rates[t, 3])
                cash -= q * px + f; fees += f; qty = q; ntr += 1; entered_now = True
                stop = px * (1.0 - stop_pct) if stop_pct > 0.0 else 0.0
                target = px * (1.0 + target_pct) if target_pct > 0.0 else 0.0
        pending = 0
        # ---- 2. protective orders inside the bar
        if qty > 0.0:
            xpx = -1.0
            if stop > 0.0 and not entered_now and o[t] <= stop:
                xpx = o[t]; nst += 1                       # gapped through the stop
            elif target > 0.0 and not entered_now and o[t] >= target:
                xpx = o[t]; ntg += 1
            elif stop > 0.0 and l[t] <= stop:
                xpx = stop; nst += 1                       # pessimistic: stop before target
            elif target > 0.0 and h[t] >= target:
                xpx = target; ntg += 1
            if xpx > 0.0:
                f = _fee(qty * xpx, rates[t, 1], rates[t, 2], rates[t, 3])
                cash += qty * xpx - f; fees += f; qty = 0.0
        # ---- 3. signals at the close
        if fill_mode == 0:
            if t + 1 < n:
                if entry[t]:
                    pending = 1
                elif exit_[t]:
                    pending = -1
        else:
            if exit_[t] and qty > 0.0:
                f = _fee(qty * c[t], rates[t, 1], rates[t, 2], rates[t, 3])
                cash += qty * c[t] - f; fees += f; qty = 0.0
            elif entry[t] and qty == 0.0:
                px = c[t]
                q = np.floor(cash / (px * (1.0 + rates[t, 0] + rates[t, 2])) / lot) * lot
                while q > 0.0 and q * px + _fee(q * px, rates[t, 0], rates[t, 2], rates[t, 3]) > cash:
                    q -= lot
                if q > 0.0:
                    f = _fee(q * px, rates[t, 0], rates[t, 2], rates[t, 3])
                    cash -= q * px + f; fees += f; qty = q; ntr += 1
                    stop = px * (1.0 - stop_pct) if stop_pct > 0.0 else 0.0
                    target = px * (1.0 + target_pct) if target_pct > 0.0 else 0.0
        # ---- per-bar stats (Welford), drawdown
        eq = cash + qty * c[t]
        r = eq / prev_eq - 1.0
        prev_eq = eq
        rn += 1
        d = r - rmean
        rmean += d / rn
        rm2 += d * (r - rmean)
        if eq > peak:
            peak = eq
        dd = 1.0 - eq / peak
        if dd > maxdd:
            maxdd = dd
    out[R_EQUITY] = cash + qty * c[n - 1]; out[R_TRADES] = ntr; out[R_STOPS] = nst; out[R_TARGETS] = ntg
    out[R_SQOFF] = nsq; out[R_RET_N] = rn; out[R_RET_MEAN] = rmean; out[R_RET_M2] = rm2; out[R_MAXDD] = maxdd
    out[R_FEES] = fees


@nb.njit(cache=True)
def ema_nb(x, n):
    a = 2.0 / (n + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, x.shape[0]):
        out[i] = a * x[i] + (1.0 - a) * out[i - 1]
    return out


@nb.njit(parallel=True, cache=True)
def sweep_ema_cross(O, H, L, C, tod, same_next, sq, rates, fasts, slows, stops, lo, hi, no_entry_after, warmup,
                    cash0, lot):
    """Grid of EMA(fast) x EMA(slow) x stop% for every symbol on bars [lo, hi). Signals are generated INSIDE the
    parallel loop (no n x combos signal matrices in memory). Returns float64[nf, ns, nstop, nsym, N_RESULT]."""
    nsym, nf, ns, nk = O.shape[1], fasts.shape[0], slows.shape[0], stops.shape[0]
    out = np.zeros((nf, ns, nk, nsym, N_RESULT))
    m = hi - lo
    for job in nb.prange(nsym * nf * ns):
        sym, rest = job // (nf * ns), job % (nf * ns)
        i, j = rest // ns, rest % ns
        if fasts[i] >= slows[j]:
            continue
        c = np.ascontiguousarray(C[:, sym])
        ef, es = ema_nb(c, fasts[i]), ema_nb(c, slows[j])
        entry = np.zeros(m, dtype=np.bool_)
        ex = np.zeros(m, dtype=np.bool_)
        for t in range(lo, hi):
            if t == 0 or t < warmup:
                continue
            a, p = ef[t] > es[t], ef[t - 1] > es[t - 1]
            if a and not p and tod[t] <= no_entry_after and same_next[t]:
                entry[t - lo] = True
            elif p and not a and same_next[t]:
                ex[t - lo] = True
        for k in range(nk):
            simulate(O[lo:hi, sym], H[lo:hi, sym], L[lo:hi, sym], C[lo:hi, sym], entry, ex, sq[lo:hi],
                     rates[lo:hi], stops[k], 0.0, cash0, lot, 0, out[i, j, k, sym])
    return out
