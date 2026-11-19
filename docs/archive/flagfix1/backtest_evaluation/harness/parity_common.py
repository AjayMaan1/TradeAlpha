"""
Parity spec shared by every engine run (E3).

  data      : Historify snapshot 'eval-50', interval D, 50 NSE symbols, 2015-2024 (2609 bars each)
  signal    : SMA(close, 20) crosses above SMA(close, 50) -> enter long; crosses below -> exit.
              Evaluated on the CLOSE of bar t.
  fill      : mode 'next_open' -> OPEN of bar t+1 (default, realistic);  mode 'close' -> CLOSE of bar t
  size      : all-in, whole shares: floor(cash / (price * (1 + FEE)))
  costs     : FEE = 0.1 % of traded value on both sides, no slippage
  capital   : 1,000,000 per symbol, symbols independent (no shared cash)
  end       : open positions are NOT closed; final equity = cash + shares * last close
Outputs per symbol: final equity, number of round trips (entries).
"""
from __future__ import annotations

import glob
import json
import os

import duckdb
import numpy as np
import pandas as pd

FAST, SLOW, FEE, CASH = 20, 50, 0.001, 1_000_000.0
SNAP = glob.glob("/tmp/eval/inst/snapshots/eval-50-*")[0]


def load_daily(interval: str = "D") -> dict[str, pd.DataFrame]:
    man = json.load(open(os.path.join(SNAP, "manifest.json")))
    con = duckdb.connect()
    out = {}
    for s in man["series"]:
        if s["interval"] != interval or s["exchange"] != "NSE":
            continue
        df = con.execute(f"SELECT ts, open, high, low, close, volume FROM read_parquet('{SNAP}/{s['file']}') "
                         f"ORDER BY ts").df()
        df.index = pd.to_datetime(df.pop("ts"), unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
        df.index.name = None
        out[s["symbol"]] = df
    con.close()
    return dict(sorted(out.items()))


def sma(x: np.ndarray, n: int) -> np.ndarray:
    c = np.cumsum(np.r_[0.0, x])
    out = np.full(len(x), np.nan)
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def signals(close: np.ndarray):
    f, s = sma(close, FAST), sma(close, SLOW)
    above = f > s
    prev = np.r_[False, above[:-1]]
    valid = ~np.isnan(s) & ~np.isnan(np.r_[np.nan, s[:-1]])
    entry = above & ~prev & valid
    exit_ = ~above & prev & valid
    return entry, exit_


def reference(df: pd.DataFrame, mode: str = "next_open") -> tuple[float, int]:
    o, c = df["open"].to_numpy(), df["close"].to_numpy()
    entry, exit_ = signals(c)
    cash, sh, n = CASH, 0, 0
    pending = None
    for t in range(len(c)):
        if mode == "next_open" and pending is not None:
            px = o[t]
            if pending == "buy" and sh == 0:
                q = int(cash // (px * (1 + FEE)))
                if q > 0:
                    cash -= q * px * (1 + FEE)
                    sh, n = q, n + 1
            elif pending == "sell" and sh > 0:
                cash += sh * px * (1 - FEE)
                sh = 0
            pending = None
        if entry[t] or exit_[t]:
            side = "buy" if entry[t] else "sell"
            if mode == "next_open":
                pending = side if t + 1 < len(c) else None
            else:
                px = c[t]
                if side == "buy" and sh == 0:
                    q = int(cash // (px * (1 + FEE)))
                    if q > 0:
                        cash -= q * px * (1 + FEE)
                        sh, n = q, n + 1
                elif side == "sell" and sh > 0:
                    cash += sh * px * (1 - FEE)
                    sh = 0
    return cash + sh * c[-1], n


def report(engine: str, results: dict[str, tuple[float, int]], seconds: float, mode: str, extra=None):
    path = f"/tmp/eval/parity_{engine}_{mode}.json"
    json.dump({"engine": engine, "mode": mode, "seconds": round(seconds, 3),
               "results": {k: [float(v[0]), int(v[1])] for k, v in results.items()}, **(extra or {})},
              open(path, "w"), indent=1)
    print(f"{engine:14s} {mode:10s} {seconds:8.2f}s  symbols={len(results)}  -> {path}")
