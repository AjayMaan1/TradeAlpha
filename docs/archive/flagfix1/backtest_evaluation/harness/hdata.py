"""
Prototype data adapter: Historify bar store / snapshots -> the shapes backtesting engines want.

  long  : ts-sorted rows (symbol, ts, o, h, l, c, v)            <- one DuckDB query
  wide  : dict field -> DataFrame[index=IST DatetimeIndex, columns=symbols]   (vectorbt / numpy engines)
  bt    : per-symbol DataFrame with Open/High/Low/Close/Volume             (backtesting.py, backtrader)
"""
from __future__ import annotations

import glob
import json
import os

import duckdb
import pandas as pd

IST = "Asia/Kolkata"
FIELDS = ["open", "high", "low", "close", "volume"]


def _to_index(ts: pd.Series) -> pd.DatetimeIndex:
    return pd.to_datetime(ts.to_numpy("int64"), unit="s", utc=True).tz_convert(IST)


def long_from_store(store, instrument_ids: list[int], interval_id: int, ts_from=None, ts_to=None) -> pd.DataFrame:
    """ONE query for many series, through the store's MVCC reader (works while downloads write)."""
    sel = pd.DataFrame({"instrument_id": instrument_ids}).astype("int64")
    lo = -(2**62) if ts_from is None else int(ts_from)
    hi = 2**62 if ts_to is None else int(ts_to)
    with store._reading() as cur:
        cur.register("_sel", sel)
        return cur.execute(
            f"SELECT o.instrument_id, o.ts, o.open, o.high, o.low, o.close, o.volume FROM {store._src} o "
            f"JOIN _sel USING (instrument_id) WHERE o.interval_id=? AND o.ts BETWEEN ? AND ? "
            f"ORDER BY o.ts, o.instrument_id", [int(interval_id), lo, hi]).df()


def wide(long: pd.DataFrame, key: str = "instrument_id", names: dict | None = None) -> dict[str, pd.DataFrame]:
    out = {}
    for f in FIELDS:
        w = long.pivot(index="ts", columns=key, values=f)
        w.index = _to_index(w.index.to_series())
        if names:
            w.columns = [names[c] for c in w.columns]
        out[f] = w
    return out


def bt_frame(df: pd.DataFrame) -> pd.DataFrame:
    """backtesting.py / backtrader layout."""
    out = pd.DataFrame({"Open": df["open"].to_numpy(), "High": df["high"].to_numpy(), "Low": df["low"].to_numpy(),
                        "Close": df["close"].to_numpy(), "Volume": df["volume"].to_numpy()},
                       index=_to_index(df["ts"]))
    return out


def long_from_snapshot(folder: str, interval: str) -> pd.DataFrame:
    """All series of one interval from a snapshot folder - out of process, no lock on the live store."""
    man = json.load(open(os.path.join(folder, "manifest.json")))
    files = [os.path.join(folder, s["file"]) for s in man["series"] if s["interval"] == interval]
    con = duckdb.connect()
    try:
        lst = ", ".join("'" + f.replace("'", "''") + "'" for f in files)
        return con.execute(
            f"SELECT regexp_extract(filename, '.*/([^/]+)_[A-Z_]+_[^_]+\\.parquet$', 1) AS symbol, ts, open, high, "
            f"low, close, volume FROM read_parquet([{lst}], filename=true) ORDER BY ts, symbol").df()
    finally:
        con.close()
