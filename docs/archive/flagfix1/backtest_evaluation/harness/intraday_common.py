"""
E5 intraday spec (NSE MIS-style), identical signals for every engine - engines differ only in EXECUTION.

  data    : 5m bars rolled up by Historify from 1m (session-anchored 09:15), 50 symbols, 2023-2024, 75 bars/session
  signal  : EMA(close, 9) crosses above EMA(close, 21) on the CLOSE of bar t, bar t opens <= 14:55 -> buy at OPEN t+1
            (same session only); cross below -> sell at OPEN t+1
  stop    : protective stop 0.5 % below the FILL price, active from the entry bar itself (bracket / cover order);
            hit if low <= stop -> exit at stop, or at the bar open if the open gapped through the stop
  squareoff: any open position is closed at the OPEN of the 15:15 bar (broker MIS auto square-off)
  size    : all-in whole shares; FEE 0.03 % per side; 1,000,000 per symbol; symbols independent
"""
from __future__ import annotations

import glob
import os

import duckdb
import numpy as np
import pandas as pd

FAST, SLOW, STOP, FEE, CASH = 9, 21, 0.005, 0.0003, 1_000_000.0
IST_OFFSET = 19_800


def load_5m(n: int | None = None) -> dict[str, pd.DataFrame]:
    con = duckdb.connect()
    out = {}
    for f in sorted(glob.glob("/tmp/eval/m5/*.parquet"))[:n]:
        df = con.execute(f"SELECT ts, open, high, low, close, volume FROM read_parquet('{f}') ORDER BY ts").df()
        out[os.path.basename(f)[:-8]] = df
    con.close()
    return out


def ema(x: np.ndarray, n: int) -> np.ndarray:
    a = 2.0 / (n + 1)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def minute_of_day(ts: np.ndarray) -> np.ndarray:
    return ((ts + IST_OFFSET) % 86_400) // 60


def signals(df: pd.DataFrame):
    """entry/exit on bar t (to be executed at the open of t+1), and the square-off bar mask."""
    ts, c = df["ts"].to_numpy("int64"), df["close"].to_numpy()
    f, s = ema(c, FAST), ema(c, SLOW)
    above = f > s
    prev = np.r_[False, above[:-1]]
    mod = minute_of_day(ts)
    day = (ts + IST_OFFSET) // 86_400
    same_next = np.r_[day[1:] == day[:-1], False]          # bar t+1 is in the same session
    entry = above & ~prev & (mod <= 14 * 60 + 55) & same_next
    entry[: SLOW] = False
    exit_ = ~above & prev & same_next
    squareoff = mod == 15 * 60 + 15
    return entry, exit_, squareoff


def reference(df: pd.DataFrame) -> tuple[float, int, int]:
    """Returns (final equity, entries, stop exits)."""
    o, h, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    entry, exit_, sq = signals(df)
    cash, sh, stop, n, nstop = CASH, 0, 0.0, 0, 0
    pend = 0
    for t in range(len(c)):
        if sh > 0 and sq[t]:                                  # MIS square-off at the 15:15 open
            cash += sh * o[t] * (1 - FEE); sh = 0; pend = 0
        if pend == 1 and sh == 0 and not sq[t]:
            q = int(cash // (o[t] * (1 + FEE)))
            if q > 0:
                cash -= q * o[t] * (1 + FEE); sh, n = q, n + 1
                stop = o[t] * (1 - STOP)
        elif pend == -1 and sh > 0:
            cash += sh * o[t] * (1 - FEE); sh = 0
        pend = 0
        if sh > 0 and lo[t] <= stop:                          # protective stop, intrabar
            px = min(o[t], stop)
            cash += sh * px * (1 - FEE); sh = 0; nstop += 1
        if entry[t]:
            pend = 1
        elif exit_[t]:
            pend = -1
    return cash + sh * c[-1], n, nstop
