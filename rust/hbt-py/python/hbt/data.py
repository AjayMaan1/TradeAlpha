"""
Bars for the core, as dicts of int64 arrays (ts, open, high, low, close in price units, volume, day, minute).

* bars_from_store     inside the Historify process: Arrow C stream from the store's MVCC cursor -> Rust (zero-copy)
* bars_from_snapshot  out of process: a verified snapshot Parquet file -> DuckDB -> Arrow -> Rust
* bars_from_frame     any pandas frame with ts, open, high, low, close, volume
Prices are converted once to integer price units and REFUSED if they are not exact multiples of 1/price_scale.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

_COLS = "ts::BIGINT AS ts, open::DOUBLE AS open, high::DOUBLE AS high, low::DOUBLE AS low, close::DOUBLE AS close, " \
        "volume::BIGINT AS volume"


def _core():
    import hbt_core

    return hbt_core


def bars_from_store(store, instrument_id: int, interval_id: int, ts_from=None, ts_to=None, price_scale: int = 100) -> dict:
    """Read-only from a Historify BarStore through its public Arrow stream (``BarStore.read_arrow``)."""
    return store.read_arrow(instrument_id, interval_id, ts_from, ts_to,
                            lambda rel: _core().bars_from_arrow(rel, price_scale))


def bars_from_snapshot(folder: str, symbol: str, exchange: str, interval: str, price_scale: int = 100,
                       verify: bool = True) -> dict:
    import duckdb

    with open(os.path.join(folder, "manifest.json")) as f:
        man = json.load(f)
    sr = next((x for x in man["series"] if x["symbol"] == symbol and x["exchange"] == exchange
               and x["interval"] == interval), None)
    if sr is None:
        raise KeyError(f"{symbol} {exchange} {interval} is not in snapshot {man.get('name')}")
    path = os.path.realpath(os.path.join(folder, sr["file"]))
    if os.path.commonpath([path, os.path.realpath(folder)]) != os.path.realpath(folder):
        raise ValueError(f"snapshot file outside the snapshot folder: {sr['file']}")      # audit 2 A2-L6
    if verify:
        import hashlib
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != sr["sha256"]:
            raise ValueError(f"snapshot file modified: {sr['file']}")
    con = duckdb.connect()
    try:
        rel = con.sql(f"SELECT {_COLS} FROM read_parquet('{path.replace(chr(39), chr(39) * 2)}') ORDER BY ts")
        out = _core().bars_from_arrow(rel, price_scale)
    finally:
        con.close()
    out["provenance"] = {"snapshot_id": man["id"], "snapshot_name": man["name"], "file_sha256": sr["sha256"],
                         "calendar_hash": man.get("calendar_hash"), "adjustment": man.get("adjustment", "raw")}
    return out


TS_MIN, TS_MAX = 946_684_800, 4_102_444_800          # 2000-01-01 .. 2100-01-01 in epoch SECONDS


def bars_from_frame(df, price_scale: int = 100) -> dict:
    """pandas frame (ts, open, high, low, close, volume) -> core bars (same conversion rules as the Arrow path).
    Refused (audit 2 A2-M3): non-integer ts (datetime columns: use ``.astype('int64') // 10**9``), ts outside
    2000-2100 (milliseconds / nanoseconds), non-finite prices, NULL volume, prices off the 1/price_scale grid."""
    col = df["ts"]
    if not pd.api.types.is_integer_dtype(col):
        raise ValueError(f"ts must be integer epoch seconds, got dtype {col.dtype} "
                         "(convert datetimes with .astype('int64') // 10**9)")
    ts = col.to_numpy("int64")
    if len(ts) and (ts.min() < TS_MIN or ts.max() >= TS_MAX):
        raise ValueError("ts must be epoch SECONDS between 2000 and 2100 (milliseconds or nanoseconds given?)")
    out = {"ts": ts}
    for c in ("open", "high", "low", "close"):
        u = df[c].to_numpy("float64") * price_scale
        if not np.isfinite(u).all():
            raise ValueError(f"{c} has NaN / infinite prices")
        r = np.round(u)
        if len(u) and np.abs(u - r).max() > 1e-6 * max(1.0, price_scale):
            raise ValueError(f"{c} has prices that are not multiples of 1/{price_scale} rupee")
        out[c] = r.astype("int64")
    vol = df["volume"]
    if vol.isna().any():
        raise ValueError("volume has NULL values")
    out["volume"] = vol.to_numpy("int64")
    out["day"] = (ts + 19_800) // 86_400
    out["minute"] = ((ts + 19_800) % 86_400) // 60
    return out
