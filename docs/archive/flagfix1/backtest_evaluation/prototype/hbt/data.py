"""
Data access for backtests - the two paths that work with Historify's single-process DuckDB store:

* from_snapshot(folder)      out of process (notebooks, CLI, CI). Verifies checksums, returns the manifest so a
                              result can record exactly which data it ran on (snapshot id + manifest sha256).
* from_store(store, ...)     inside the Historify process (a backtest job), through the store's MVCC readers.

Both return a Universe: aligned float64 matrices [bars x symbols] + session metadata derived from timestamps.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

import duckdb
import numpy as np

IST_OFFSET = 19_800


@dataclass
class Universe:
    symbols: list[str]
    ts: np.ndarray                      # int64 bar-open epoch seconds (UTC)
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    provenance: dict = field(default_factory=dict)

    @property
    def trade_day(self) -> np.ndarray:
        return (self.ts + IST_OFFSET) // 86_400

    @property
    def minute_of_day(self) -> np.ndarray:
        return ((self.ts + IST_OFFSET) % 86_400) // 60

    @property
    def same_session_next(self) -> np.ndarray:
        d = self.trade_day
        return np.r_[d[1:] == d[:-1], False]

    def sessions(self) -> int:
        return int(np.unique(self.trade_day).size)

    def mask_at(self, minute: int) -> np.ndarray:
        return self.minute_of_day == minute


def _align(parts: dict[str, dict]) -> Universe:
    syms = sorted(parts)
    uts = np.unique(np.concatenate([parts[s]["ts"] for s in syms]))
    mats = {f: np.full((uts.size, len(syms)), np.nan) for f in ("open", "high", "low", "close", "volume")}
    for j, s in enumerate(syms):
        r = np.searchsorted(uts, parts[s]["ts"])
        for f in mats:
            mats[f][r, j] = parts[s][f]
    return Universe(syms, uts, **mats)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def from_snapshot(folder: str, interval: str, exchange: str = "NSE", verify: bool = True) -> Universe:
    man_path = os.path.join(folder, "manifest.json")
    manifest = json.load(open(man_path))
    series = [s for s in manifest["series"] if s["interval"] == interval and s["exchange"] == exchange]
    con = duckdb.connect()
    parts = {}
    try:
        for s in series:
            path = os.path.join(folder, s["file"])
            if verify and _sha256(path) != s["sha256"]:
                raise ValueError(f"snapshot file modified: {s['file']}")
            parts[s["symbol"]] = con.execute(
                f"SELECT ts, open, high, low, close, volume FROM read_parquet('{path}') ORDER BY ts").fetchnumpy()
    finally:
        con.close()
    u = _align(parts)
    u.provenance = {"snapshot_id": manifest["id"], "snapshot_name": manifest["name"],
                    "manifest_sha256": _sha256(man_path), "interval": interval,
                    "sources": sorted({s["source"] for s in series}),
                    "incomplete_sessions": {s["symbol"]: s["incomplete_sessions"] for s in series
                                            if s["incomplete_sessions"]},
                    "calendar_version": manifest.get("calendar_version")}
    return u


def from_parquet_dir(folder: str, label: str) -> Universe:
    """Plain Parquet files <SYMBOL>.parquet (used for the 5m roll-up written by the harness)."""
    con = duckdb.connect()
    parts = {}
    try:
        for fn in sorted(os.listdir(folder)):
            if fn.endswith(".parquet"):
                parts[fn[:-8]] = con.execute(f"SELECT ts, open, high, low, close, volume FROM "
                                             f"read_parquet('{os.path.join(folder, fn)}') ORDER BY ts").fetchnumpy()
    finally:
        con.close()
    u = _align(parts)
    u.provenance = {"source": label}
    return u


def from_store(store, instrument_ids: dict[int, str], interval_id: int, ts_from=None, ts_to=None) -> Universe:
    """In-process (Historify job): one batched query through the store's MVCC reader."""
    lo = -(2**62) if ts_from is None else int(ts_from)
    hi = 2**62 if ts_to is None else int(ts_to)
    ids = sorted(instrument_ids)
    with store._reading() as cur:
        a = cur.execute(f"SELECT instrument_id, ts, open, high, low, close, volume FROM {store._src} "
                        f"WHERE interval_id = ? AND ts BETWEEN ? AND ? AND instrument_id IN "
                        f"({','.join(str(int(i)) for i in ids)}) ORDER BY instrument_id, ts",
                        [int(interval_id), lo, hi]).fetchnumpy()
    parts = {}
    for iid in ids:
        m = a["instrument_id"] == iid
        parts[instrument_ids[iid]] = {k: a[k][m] for k in ("ts", "open", "high", "low", "close", "volume")}
    return _align(parts)
