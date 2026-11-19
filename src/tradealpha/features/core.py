"""
Causal indicators (P3 WP2). Contract for every function:
    * the value at row i uses rows <= i only (checked for every registered feature by the corrupt-the-future test)
    * a declared warm-up: rows before it are NaN (never a signal)
    * pure: Series / DataFrame in, Series / DataFrame out, same index (the frame's available_at index)
    * session features (VWAP, ORB, RVOL by time of day) reset on the calendar trading day (``day`` column)
Frames are DataFrames with open, high, low, close, volume (+ day for session features).
Exponential / Wilder smoothing is seeded with the SMA of the first n values (documented: the seed changes early
values); implemented with pandas' C ewm so 10 years of 1m bars stay fast.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------------------------- helpers
def _seeded_ewm(s: pd.Series, n: int, alpha: float) -> pd.Series:
    """EMA recursion y_t = a x_t + (1-a) y_{t-1}, seeded at the first full window with its SMA. NaN inputs before
    the first valid value are skipped (the window starts at the first valid input)."""
    x = s.astype(float)
    out = pd.Series(np.nan, index=s.index)
    valid = x.notna().to_numpy()
    if valid.sum() < n:
        return out
    first = int(np.argmax(valid))
    seed_pos = first + n - 1
    if not x.iloc[first:seed_pos + 1].notna().all():
        # gaps inside the seed window: fall back to a plain rolling seed at the first complete window
        r = x.rolling(n, min_periods=n).mean()
        if r.notna().sum() == 0:
            return out
        seed_pos = int(np.argmax(r.notna().to_numpy()))
        seed = r.iloc[seed_pos]
    else:
        seed = x.iloc[first:seed_pos + 1].mean()
    tail = x.iloc[seed_pos:].copy()
    tail.iloc[0] = seed
    out.iloc[seed_pos:] = tail.ewm(alpha=alpha, adjust=False, ignore_na=False).mean().to_numpy()
    return out


def cross_up(a: pd.Series, b) -> pd.Series:
    b = b if isinstance(b, pd.Series) else pd.Series(float(b), index=a.index)
    return ((a > b) & (a.shift(1) <= b.shift(1))).fillna(False).astype(bool)


def cross_down(a: pd.Series, b) -> pd.Series:
    b = b if isinstance(b, pd.Series) else pd.Series(float(b), index=a.index)
    return ((a < b) & (a.shift(1) >= b.shift(1))).fillna(False).astype(bool)


def zscore(s: pd.Series, n: int = 20) -> pd.Series:
    m = s.rolling(n, min_periods=n).mean()
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    return (s - m) / sd.replace(0, np.nan)


# ---------------------------------------------------------------------------------------------- moving averages
def sma(s: pd.Series, n: int = 20) -> pd.Series:
    return s.astype(float).rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int = 20) -> pd.Series:
    return _seeded_ewm(s, n, 2.0 / (n + 1))


def rma(s: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's moving average (alpha = 1/n)."""
    return _seeded_ewm(s, n, 1.0 / n)


def wma(s: pd.Series, n: int = 20) -> pd.Series:
    """Linearly weighted MA, weights 1..n (newest = n). Vectorised: sum(w_i x_i) via cumulative sums."""
    x = s.astype(float).to_numpy()
    m = len(x)
    out = np.full(m, np.nan)
    if m >= n:
        c = np.concatenate([[0.0], np.cumsum(x)])
        ci = np.concatenate([[0.0], np.cumsum(x * np.arange(1, m + 1))])
        i = np.arange(n, m + 1)                     # window (i-n, i]
        num = (ci[i] - ci[i - n]) - (i - n) * (c[i] - c[i - n])
        out[n - 1:] = num / (n * (n + 1) / 2)
        # NaN anywhere in the window -> NaN (cumsum propagates NaN forever, recompute those windows)
        bad = pd.Series(np.isnan(x)).rolling(n, min_periods=1).max().to_numpy() > 0
        out[bad] = np.nan
        if bad.any():
            w = np.arange(1, n + 1, dtype=float)
            r = pd.Series(x).rolling(n, min_periods=n).apply(lambda v: float(np.dot(v, w) / w.sum()), raw=True)
            out = np.where(np.isnan(out), r.to_numpy(), out)
    return pd.Series(out, index=s.index)


def hma(s: pd.Series, n: int = 20) -> pd.Series:
    """Hull MA = WMA(2*WMA(n/2) - WMA(n), sqrt(n))."""
    half, root = max(int(n / 2), 1), max(int(math.sqrt(n)), 1)
    return wma(2 * wma(s, half) - wma(s, n), root)


def vwma(df: pd.DataFrame, n: int = 20) -> pd.Series:
    pv = (df["close"] * df["volume"]).rolling(n, min_periods=n).sum()
    return pv / df["volume"].rolling(n, min_periods=n).sum().replace(0, np.nan)


# ---------------------------------------------------------------------------------------------- volatility
def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.fillna(df["high"] - df["low"])


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder ATR (RMA of true range), seeded with the SMA of the first n true ranges."""
    return rma(true_range(df), n)


def atr_pct(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return atr(df, n) / df["close"] * 100


def bollinger(s: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(s, n)
    sd = s.astype(float).rolling(n, min_periods=n).std(ddof=0)
    up, lo = mid + k * sd, mid - k * sd
    return pd.DataFrame({"mid": mid, "upper": up, "lower": lo, "bandwidth": (up - lo) / mid, "pct_b": (s - lo) / (up - lo)})


def keltner(df: pd.DataFrame, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = ema(df["close"], n)
    a = atr(df, n)
    return pd.DataFrame({"mid": mid, "upper": mid + k * a, "lower": mid - k * a})


def squeeze(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """Bollinger bands inside Keltner channels (volatility compression); NaN during warm-up."""
    b, k = bollinger(df["close"], n), keltner(df, n, 1.5)
    out = (b["upper"] < k["upper"]) & (b["lower"] > k["lower"])
    return out.where(b["mid"].notna() & k["mid"].notna())


def hv_close(s: pd.Series, n: int = 20, periods: int = 252) -> pd.Series:
    r = np.log(s / s.shift(1))
    return r.rolling(n, min_periods=n).std(ddof=1) * math.sqrt(periods) * 100


def parkinson(df: pd.DataFrame, n: int = 20, periods: int = 252) -> pd.Series:
    hl = np.log(df["high"] / df["low"]) ** 2
    return np.sqrt(hl.rolling(n, min_periods=n).mean() / (4 * math.log(2))) * math.sqrt(periods) * 100


# ---------------------------------------------------------------------------------------------- momentum
def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI: first average = SMA of the first n changes, then RMA. First value at row n."""
    d = s.astype(float).diff()
    g, loss = d.clip(lower=0), (-d).clip(lower=0)
    ag, al = rma(g, n), rma(loss, n)
    out = 100 - 100 / (1 + ag / al)
    out[(al == 0) & ag.notna()] = 100.0
    out[(al == 0) & (ag == 0)] = 50.0
    return out


def stochastic(df: pd.DataFrame, n: int = 14, d: int = 3) -> pd.DataFrame:
    hh = df["high"].rolling(n, min_periods=n).max()
    ll = df["low"].rolling(n, min_periods=n).min()
    k = (df["close"] - ll) / (hh - ll).replace(0, np.nan) * 100
    return pd.DataFrame({"k": k, "d": k.rolling(d, min_periods=d).mean()})


def roc(s: pd.Series, n: int = 10) -> pd.Series:
    return (s / s.shift(n) - 1) * 100


def cci(df: pd.DataFrame, n: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    m = tp.rolling(n, min_periods=n).mean()
    md = tp.rolling(n, min_periods=n).apply(lambda v: float(np.mean(np.abs(v - v.mean()))), raw=True)
    return (tp - m) / (0.015 * md.replace(0, np.nan))


def williams_r(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hh = df["high"].rolling(n, min_periods=n).max()
    ll = df["low"].rolling(n, min_periods=n).min()
    return (hh - df["close"]) / (hh - ll).replace(0, np.nan) * -100


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(s, fast) - ema(s, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


# ---------------------------------------------------------------------------------------------- trend
def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """Wilder DMI/ADX. +DI/-DI from RMA(+DM), RMA(-DM), ATR; ADX = RMA(DX)."""
    up = df["high"].diff()
    dn = -df["low"].diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    pdm[up.isna()] = np.nan
    ndm[up.isna()] = np.nan
    tr = true_range(df)
    tr[up.isna()] = np.nan
    a = rma(tr, n)
    pdi = 100 * rma(pdm, n) / a
    ndi = 100 * rma(ndm, n) / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return pd.DataFrame({"adx": rma(dx, n), "plus_di": pdi, "minus_di": ndi})


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> pd.DataFrame:
    """Supertrend on ATR(n) bands around HL2. direction +1 (up) / -1 (down); line = active band."""
    a = atr(df, n).to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy(float)
    close = df["close"].to_numpy(float)
    m = len(close)
    ub, lb = hl2 + mult * a, hl2 - mult * a
    fub, flb = np.full(m, np.nan), np.full(m, np.nan)
    direction, line = np.full(m, np.nan), np.full(m, np.nan)
    for i in range(m):
        if np.isnan(a[i]):
            continue
        if i == 0 or np.isnan(fub[i - 1]):
            fub[i], flb[i], direction[i] = ub[i], lb[i], 1.0
        else:
            fub[i] = ub[i] if (ub[i] < fub[i - 1] or close[i - 1] > fub[i - 1]) else fub[i - 1]
            flb[i] = lb[i] if (lb[i] > flb[i - 1] or close[i - 1] < flb[i - 1]) else flb[i - 1]
            if direction[i - 1] == 1:
                direction[i] = -1.0 if close[i] < flb[i] else 1.0
            else:
                direction[i] = 1.0 if close[i] > fub[i] else -1.0
        line[i] = flb[i] if direction[i] == 1 else fub[i]
    return pd.DataFrame({"line": line, "direction": direction}, index=df.index)


def donchian(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Channel of the PREVIOUS n bars (so a breakout above ``upper`` is a real breakout, not a tautology)."""
    up = df["high"].rolling(n, min_periods=n).max().shift(1)
    lo = df["low"].rolling(n, min_periods=n).min().shift(1)
    return pd.DataFrame({"upper": up, "lower": lo, "mid": (up + lo) / 2})


def psar(df: pd.DataFrame, step: float = 0.02, max_step: float = 0.2) -> pd.Series:
    """Parabolic SAR (Wilder). Path dependent: plain loop (daily / hourly data)."""
    h, lo = df["high"].to_numpy(float), df["low"].to_numpy(float)
    m = len(h)
    out = np.full(m, np.nan)
    if m < 2:
        return pd.Series(out, index=df.index)
    up = h[1] >= h[0]
    sar, ep, af = (lo[0], h[0], step) if up else (h[0], lo[0], step)
    for i in range(1, m):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, lo[i - 1], lo[i - 2] if i >= 2 else lo[i - 1])
            if lo[i] < sar:
                up, sar, ep, af = False, ep, lo[i], step
            elif h[i] > ep:
                ep, af = h[i], min(af + step, max_step)
        else:
            sar = max(sar, h[i - 1], h[i - 2] if i >= 2 else h[i - 1])
            if h[i] > sar:
                up, sar, ep, af = True, ep, h[i], step
            elif lo[i] < ep:
                ep, af = lo[i], min(af + step, max_step)
        out[i] = sar
    return pd.Series(out, index=df.index)


def linreg_slope(s: pd.Series, n: int = 20) -> pd.DataFrame:
    """OLS slope of the last n values per bar (in % of the mean per bar) and its t-statistic."""
    x = np.arange(n, dtype=float)
    xm = x - x.mean()
    sxx = float((xm ** 2).sum())

    def slope(v):
        return float(np.dot(xm, v - v.mean()) / sxx)

    def tstat(v):
        b = np.dot(xm, v - v.mean()) / sxx
        resid = v - (v.mean() + b * xm)
        se = math.sqrt(float((resid ** 2).sum()) / (n - 2) / sxx) if n > 2 else float("nan")
        return float(b / se) if se > 0 else float("nan")
    sl = s.astype(float).rolling(n, min_periods=n).apply(slope, raw=True)
    t = s.astype(float).rolling(n, min_periods=n).apply(tstat, raw=True)
    return pd.DataFrame({"slope_pct": sl / s.rolling(n, min_periods=n).mean() * 100, "t": t})


def ma_stack(s: pd.Series, fast: int = 20, mid: int = 50, slow: int = 200) -> pd.Series:
    """+1 if EMA fast > mid > slow, -1 if fast < mid < slow, else 0 (NaN during warm-up)."""
    a, b, c = ema(s, fast), ema(s, mid), ema(s, slow)
    out = pd.Series(np.where((a > b) & (b > c), 1.0, np.where((a < b) & (b < c), -1.0, 0.0)), index=s.index)
    return out.where(c.notna())


def trend_score(df: pd.DataFrame, n: int = 20, adx_n: int = 14, adx_min: float = 20.0) -> pd.Series:
    """+1 / 0 / -1 per timeframe: close vs EMA(n), EMA slope over 5 bars, ADX > adx_min. Sum across timeframes
    (after align) for the MTF alignment score -3..+3."""
    e = ema(df["close"], n)
    slope = e - e.shift(5)
    strong = adx(df, adx_n)["adx"] > adx_min
    up = (df["close"] > e) & (slope > 0) & strong
    dn = (df["close"] < e) & (slope < 0) & strong
    out = pd.Series(np.where(up, 1.0, np.where(dn, -1.0, 0.0)), index=df.index)
    return out.where(slope.notna() & adx(df, adx_n)["adx"].notna())


# ---------------------------------------------------------------------------------------------- volume
def obv(df: pd.DataFrame) -> pd.Series:
    sign = np.sign(df["close"].diff()).fillna(0)
    return (sign * df["volume"]).cumsum()


def volume_ratio(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """Volume vs the average of the PREVIOUS n bars."""
    return df["volume"] / df["volume"].rolling(n, min_periods=n).mean().shift(1).replace(0, np.nan)


def rvol_tod(df: pd.DataFrame, days: int = 20) -> pd.Series:
    """Intraday relative volume: this bar's volume vs the mean volume of the same bar-of-day over the previous
    ``days`` sessions (session-aware; needs ``day``)."""
    slot = df.groupby("day").cumcount()
    v = df["volume"].astype(float)
    prev = v.groupby(slot.to_numpy()).transform(lambda x: x.rolling(days, min_periods=days).mean().shift(1))
    return v / prev.replace(0, np.nan)


# ---------------------------------------------------------------------------------------------- session structure
def vwap(df: pd.DataFrame) -> pd.Series:
    """Session-anchored VWAP of the typical price, reset on each trading day (``day``)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    g = df["day"].to_numpy()
    pv = (tp * df["volume"]).groupby(g).cumsum()
    vv = df["volume"].groupby(g).cumsum()
    out = pv / vv.replace(0, np.nan)
    return out.fillna(tp.where(vv == 0))          # no volume yet today: typical price


def opening_range(df: pd.DataFrame, bars: int = 15) -> pd.DataFrame:
    """High / low of the first ``bars`` bars of each session (e.g. 15 x 1m = ORB15). NaN until the range is
    complete (the value is unknown before it has formed)."""
    pos = df.groupby("day").cumcount()
    first = df[pos < bars]
    hi = first.groupby("day")["high"].max()
    lo = first.groupby("day")["low"].min()
    done = pos >= bars - 1
    out = pd.DataFrame({"high": df["day"].map(hi), "low": df["day"].map(lo)}, index=df.index)
    return out.where(done)


def cpr(daily: pd.DataFrame) -> pd.DataFrame:
    """Central pivot range from a day's H/L/C (on the DAILY frame: aligning it to intraday bars of the next session
    gives the levels traders use that day). width_pct = |TC - BC| / pivot * 100."""
    p = (daily["high"] + daily["low"] + daily["close"]) / 3
    bc = (daily["high"] + daily["low"]) / 2
    tc = 2 * p - bc
    top, bot = np.maximum(tc, bc), np.minimum(tc, bc)
    return pd.DataFrame({"pivot": p, "tc": top, "bc": bot, "width_pct": (top - bot) / p * 100,
                         "r1": 2 * p - daily["low"], "s1": 2 * p - daily["high"],
                         "r2": p + (daily["high"] - daily["low"]), "s2": p - (daily["high"] - daily["low"])})


def camarilla(daily: pd.DataFrame) -> pd.DataFrame:
    rng = daily["high"] - daily["low"]
    c = daily["close"]
    return pd.DataFrame({"h3": c + rng * 1.1 / 4, "h4": c + rng * 1.1 / 2, "l3": c - rng * 1.1 / 4,
                         "l4": c - rng * 1.1 / 2})


def gap_pct(intraday: pd.DataFrame, prev_close: pd.Series) -> pd.Series:
    """Session open vs the previous official close (``prev_close`` already aligned to the intraday frame)."""
    day_open = intraday.groupby("day")["open"].transform("first")
    return (day_open / prev_close - 1) * 100


def dist_52w(daily: pd.DataFrame, n: int = 252) -> pd.DataFrame:
    hh = daily["high"].rolling(n, min_periods=n).max()
    ll = daily["low"].rolling(n, min_periods=n).min()
    return pd.DataFrame({"from_high_pct": (daily["close"] / hh - 1) * 100, "from_low_pct": (daily["close"] / ll - 1) * 100})


# ---------------------------------------------------------------------------------------------- relative
def rs_ratio(close: pd.Series, index_close: pd.Series, n: int = 20) -> pd.DataFrame:
    """Relative strength vs an index (index series already aligned): ratio and its n-bar % change."""
    r = close / index_close
    return pd.DataFrame({"ratio": r, "change_pct": (r / r.shift(n) - 1) * 100})


def mansfield_rs(close: pd.Series, index_close: pd.Series, n: int = 250) -> pd.Series:
    r = close / index_close
    return (r / r.rolling(n, min_periods=n).mean() - 1) * 100


def rolling_beta(close: pd.Series, index_close: pd.Series, n: int = 60) -> pd.Series:
    a, b = np.log(close / close.shift(1)), np.log(index_close / index_close.shift(1))
    cov = a.rolling(n, min_periods=n).cov(b)
    return cov / b.rolling(n, min_periods=n).var().replace(0, np.nan)


def rank_cross_section(panel: pd.DataFrame) -> pd.DataFrame:
    """Percentile rank (0..1) across columns (instruments) for each row (decision time). NaN stays NaN."""
    return panel.rank(axis=1, pct=True, method="average")


__all__ = [n for n in dir() if not n.startswith("_") and n not in ("math", "np", "pd", "annotations")]
