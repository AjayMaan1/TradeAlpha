"""Phase 1.3 design check: the streaming kernels (one step per bar, the evaluator's only implementation) reproduce
TA-Lib's batch definitions, including warm-up/seeding. Run with the TA-Lib evaluation venv."""
import json, math
import numpy as np, talib

rng = np.random.default_rng(7)
close = np.round(1000 * np.cumprod(1 + rng.normal(0, 0.002, 5000)) * 20) / 20
high = close + np.round(rng.uniform(0, 2, 5000) * 20) / 20
low = close - np.round(rng.uniform(0, 2, 5000) * 20) / 20


class Ema:
    def __init__(s, n): s.n, s.a, s.k, s.sum, s.v = n, 2 / (n + 1), 0, 0.0, math.nan
    def step(s, x):
        if s.k < s.n:
            s.k += 1; s.sum += x
            if s.k == s.n: s.v = s.sum / s.n
            return s.v
        s.v += s.a * (x - s.v); return s.v


class Sma:
    def __init__(s, n): s.n, s.buf, s.sum = n, [], 0.0
    def step(s, x):
        s.buf.append(x); s.sum += x
        if len(s.buf) > s.n: s.sum -= s.buf.pop(0)
        return s.sum / s.n if len(s.buf) == s.n else math.nan


class Rsi:                                    # Wilder; seed = mean of the first n changes
    def __init__(s, n): s.n, s.prev, s.k, s.up, s.dn = n, None, 0, 0.0, 0.0
    def step(s, x):
        if s.prev is None: s.prev = x; return math.nan
        d, s.prev = x - s.prev, x; s.k += 1; u, v = max(d, 0), max(-d, 0)
        if s.k < s.n: s.up += u; s.dn += v; return math.nan
        if s.k == s.n: s.up = (s.up + u) / s.n; s.dn = (s.dn + v) / s.n
        else: s.up = (s.up * (s.n - 1) + u) / s.n; s.dn = (s.dn * (s.n - 1) + v) / s.n
        return 100.0 if s.dn == 0 else 100 - 100 / (1 + s.up / s.dn)


class Atr:                                    # Wilder; TA-Lib: first ATR = SMA of TR[1..n] at index n
    def __init__(s, n): s.n, s.pc, s.k, s.sum, s.v = n, None, 0, 0.0, math.nan
    def step(s, h, l, c):
        if s.pc is None: s.pc = c; return math.nan
        tr = max(h - l, abs(h - s.pc), abs(l - s.pc)); s.pc = c; s.k += 1
        if s.k < s.n: s.sum += tr; return math.nan
        if s.k == s.n: s.v = (s.sum + tr) / s.n; return s.v
        s.v = (s.v * (s.n - 1) + tr) / s.n; return s.v


def stream(k, *cols):
    return np.array([k.step(*xs) for xs in zip(*cols)])


def cmp(a, b):
    both = ~np.isnan(a) & ~np.isnan(b)
    return {"nan_pattern_equal": bool((np.isnan(a) == np.isnan(b)).all()),
            "max_rel_diff": float(np.max(np.abs(a[both] - b[both]) / np.abs(b[both])))}


out = {}
for n in (9, 21, 50):
    out[f"EMA{n}"] = cmp(stream(Ema(n), close), talib.EMA(close, n))
    out[f"SMA{n}"] = cmp(stream(Sma(n), close), talib.SMA(close, n))
    out[f"RSI{n}"] = cmp(stream(Rsi(n), close), talib.RSI(close, n))
    out[f"ATR{n}"] = cmp(stream(Atr(n), high, low, close), talib.ATR(high, low, close, n))
print(json.dumps(out, indent=1))
json.dump(out, open("/workspace/audit2/p13_talib_conventions.json", "w"), indent=1)
