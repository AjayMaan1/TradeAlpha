"""T4: signal-layer throughput. EMA(20), RSI(14), 100-bar z-score on 50 symbols x 195,750 1m bars (9.79 M rows)."""
import json, time, statistics
import numpy as np, polars as pl, numba as nb, talib
df = pl.read_ipc("/tmp/eval/t3/m1.arrow").select("instrument_id", "ts", "close").sort("instrument_id", "ts")
C = df["close"].to_numpy().reshape(50, -1)            # contiguous per symbol
res = {}
def T(name, fn, k=3):
    fn(); xs = []
    for _ in range(k):
        t0 = time.perf_counter(); r = fn(); xs.append(time.perf_counter() - t0)
    res[name] = round(statistics.median(xs), 3); print(f"{name:52s} {res[name]:7.3f} s"); return r
def polars_expr():
    c = pl.col("close"); d = c.diff()
    up = d.clip(lower_bound=0).ewm_mean(alpha=1 / 14, adjust=False); dn = (-d).clip(lower_bound=0).ewm_mean(alpha=1 / 14, adjust=False)
    return df.lazy().with_columns(
        c.ewm_mean(span=20, adjust=False).over("instrument_id").alias("ema"),
        (100 - 100 / (1 + up / dn)).over("instrument_id").alias("rsi"),
        ((c - c.rolling_mean(100)) / c.rolling_std(100)).over("instrument_id").alias("z")).collect()
def ta():
    return [(talib.EMA(x, 20), talib.RSI(x, 14), (x - talib.SMA(x, 100)) / talib.STDDEV(x, 100)) for x in C]
@nb.njit(parallel=True, cache=True, fastmath=False)
def nb_all(C):
    S, n = C.shape; ema = np.empty_like(C); rsi = np.full_like(C, np.nan); z = np.full_like(C, np.nan)
    for s in nb.prange(S):
        x = C[s]; a = 2 / 21; e = x[0]; ema[s, 0] = e; up = dn = 0.0; sm = sq = 0.0
        for t in range(n):
            if t: e = a * x[t] + (1 - a) * e; ema[s, t] = e
            if t:
                d = x[t] - x[t - 1]; g, l = max(d, 0.0), max(-d, 0.0)
                up = up + (g - up) / 14; dn = dn + (l - dn) / 14
                if dn > 0: rsi[s, t] = 100 - 100 / (1 + up / dn)
            sm += x[t]; sq += x[t] * x[t]
            if t >= 100: sm -= x[t - 100]; sq -= x[t - 100] ** 2
            if t >= 99:
                m = sm / 100; v = (sq - 100 * m * m) / 99
                if v > 0: z[s, t] = (x[t] - m) / np.sqrt(v)
    return ema, rsi, z
def numpy_pandas_style():
    import pandas as pd
    p = pd.DataFrame(C.T)
    d = p.diff(); up = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean(); dn = (-d).clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    return p.ewm(span=20, adjust=False).mean(), 100 - 100 / (1 + up / dn), (p - p.rolling(100).mean()) / p.rolling(100).std()
T("Polars expressions (lazy, multi-threaded, .over)", polars_expr)
T("TA-Lib (C), per symbol", ta)
T("numba, one fused parallel pass", lambda: nb_all(C))
T("pandas (wide frame)", numpy_pandas_style)
e1 = polars_expr()["ema"].to_numpy().reshape(50, -1); e2 = nb_all(C)[0]
print("EMA agreement polars vs numba: max abs diff", float(np.nanmax(np.abs(e1 - e2))))
json.dump(res, open("/workspace/backtest_evaluation/results/t4_indicators.json", "w"), indent=1)
