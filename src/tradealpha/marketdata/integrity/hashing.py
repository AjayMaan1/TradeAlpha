"""
Order-independent, engine-independent fingerprints of bars (vectorised numpy, no per-row Python).

row hash = splitmix64 chain over (ts, open, high, low, close in 1/10000, volume, oi)
session checksum = sum of the row hashes of one IST trading day, mod 2**64, as 16 hex digits
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from tradealpha.platform.time.timeutil import DAY_S, IST_OFFSET_S

_EPOCH = date(1970, 1, 1)
_M1, _M2 = np.uint64(0xBF58476D1CE4E5B9), np.uint64(0x94D049BB133111EB)


def _mix(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.uint64, copy=True)
    with np.errstate(over="ignore"):
        x ^= x >> np.uint64(30)
        x *= _M1
        x ^= x >> np.uint64(27)
        x *= _M2
        x ^= x >> np.uint64(31)
    return x


def row_hashes(df: pd.DataFrame) -> np.ndarray:
    if df.empty:
        return np.zeros(0, dtype=np.uint64)
    cols = [df["ts"].to_numpy("int64")]
    for c in ("open", "high", "low", "close"):
        cols.append(np.round(df[c].to_numpy("float64") * 10_000).astype("int64"))
    cols.append(df["volume"].to_numpy("int64"))
    oi = df["oi"] if "oi" in df.columns else pd.Series(pd.NA, index=df.index)
    cols.append(pd.to_numeric(oi, errors="coerce").fillna(-1).to_numpy("int64"))
    h = np.zeros(len(df), dtype=np.uint64)
    with np.errstate(over="ignore"):
        for k, c in enumerate(cols):
            h = _mix(h ^ _mix(c.view(np.uint64) + np.uint64(k + 1)))
    return h


def trade_days(ts: np.ndarray) -> np.ndarray:
    """IST trading-day index (days since 1970-01-01) of bar-open timestamps."""
    return (np.asarray(ts, dtype="int64") + IST_OFFSET_S) // DAY_S


def session_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Per IST trading day: bars and checksum.  Columns: trade_date (date), bars (int), checksum (str)."""
    if df.empty:
        return pd.DataFrame({"trade_date": pd.Series(dtype="object"), "bars": pd.Series(dtype="int64"),
                             "checksum": pd.Series(dtype="object")})
    days = trade_days(df["ts"].to_numpy("int64"))
    h = row_hashes(df)
    order = np.argsort(days, kind="stable")
    days, h = days[order], h[order]
    starts = np.flatnonzero(np.r_[True, days[1:] != days[:-1]])
    with np.errstate(over="ignore"):
        sums = np.add.reduceat(h, starts)
    counts = np.diff(np.r_[starts, len(days)])
    return pd.DataFrame({"trade_date": [_EPOCH + timedelta(days=int(d)) for d in days[starts]],
                         "bars": counts.astype("int64"), "checksum": [f"{int(x):016x}" for x in sums]})
