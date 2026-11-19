"""
Vectorised normalisation & validation of provider bars (replaces iterrows(), finding DL-10).

Output columns: ts (int64 epoch s, bar open; daily -> 00:00 IST of trading date), open, high, low, close (float64),
volume (int64), oi (nullable Int64).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradealpha.platform.time.intervals import IntervalSpec
from tradealpha.platform.time.timeutil import canonical_daily_ts

BAR_COLUMNS = ["ts", "open", "high", "low", "close", "volume", "oi"]
_PRICE = ["open", "high", "low", "close"]


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame({"ts": pd.Series(dtype="int64"), **{c: pd.Series(dtype="float64") for c in _PRICE},
                         "volume": pd.Series(dtype="int64"), "oi": pd.Series(dtype="Int64")})


def _to_epoch(values: pd.Series) -> pd.Series:
    """Accept epoch seconds, epoch milliseconds or ISO-8601 strings."""
    num = pd.to_numeric(values, errors="coerce").astype("float64")
    ms = num.abs() > 1e11              # > year 5138 in seconds -> must be milliseconds
    num[ms] = num[ms] / 1000.0
    out = num.round().astype("Int64")
    text_mask = num.isna() & values.notna()
    if text_mask.any():                # ISO-8601 strings, parsed element-wise (never re-parse numeric epochs)
        parsed = pd.to_datetime(values[text_mask].astype(str), utc=True, errors="coerce", format="ISO8601")
        secs = (parsed - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)
        out[text_mask] = secs.astype("Int64")
    return out


def frame_from_payload(rows) -> pd.DataFrame:
    """Provider JSON rows -> raw frame (ts still as delivered by the provider)."""
    if not rows:
        return empty_bars()
    df = pd.DataFrame(rows)
    if "timestamp" in df.columns and "ts" not in df.columns:
        df = df.rename(columns={"timestamp": "ts"})
    for c in BAR_COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[BAR_COLUMNS].copy()
    df["ts"] = _to_epoch(df["ts"])
    for c in _PRICE:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df["oi"] = pd.to_numeric(df["oi"], errors="coerce").astype("Int64")
    return df


def normalize_bars(raw: pd.DataFrame, spec: IntervalSpec) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Canonicalise timestamps, de-duplicate, validate OHLC. Returns (clean, rejects[with 'reason'])."""
    if raw.empty:
        return empty_bars(), raw.assign(reason=pd.Series(dtype="object"))
    df = raw.copy()
    reasons = pd.Series("", index=df.index, dtype="object")

    def flag(mask, why):
        nonlocal reasons
        mask = mask.fillna(True) if hasattr(mask, "fillna") else mask
        reasons = reasons.where(~(mask & (reasons == "")), why)

    flag(df["ts"].isna(), "missing_timestamp")
    flag(df[_PRICE].isna().any(axis=1) | ~np.isfinite(df[_PRICE].astype("float64")).all(axis=1), "missing_or_nan_price")
    flag(df["volume"].isna() | (df["volume"] < 0), "invalid_volume")
    flag((df[_PRICE] <= 0).any(axis=1), "non_positive_price")
    hi_ok = df["high"] >= df[["open", "close"]].max(axis=1) - 1e-9
    lo_ok = df["low"] <= df[["open", "close"]].min(axis=1) + 1e-9
    flag(~(hi_ok & lo_ok & (df["low"] <= df["high"])), "ohlc_inconsistent")

    bad = reasons != ""
    rejects = df[bad].assign(reason=reasons[bad])
    good = df[~bad].copy()
    good["ts"] = good["ts"].astype("int64")
    if not spec.is_intraday:
        good["ts"] = canonical_daily_ts(good["ts"].to_numpy())
    good["volume"] = good["volume"].astype("int64")
    # keep the LAST occurrence of a timestamp (latest revision wins)
    dup = good.duplicated("ts", keep="last")
    if dup.any():
        rejects = pd.concat([rejects, good[dup].assign(reason="duplicate_timestamp")])
        good = good[~dup]
    good = good.sort_values("ts", kind="stable").reset_index(drop=True)
    return good[BAR_COLUMNS], rejects.reset_index(drop=True)
