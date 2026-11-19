"""
SQLite BarStore — the alternative engine (review §6 option A) and the reference implementation for the
contract test-suite. Unified WITHOUT ROWID table with a composite primary key, WAL mode.
"""
from __future__ import annotations

import os
import sqlite3
import threading

import pandas as pd

from tradealpha.marketdata.bars.base import DAILY_ID, BarStore, SeriesStats, resample_frame, validate_bars
from tradealpha.marketdata.providers.normalize import BAR_COLUMNS
from tradealpha.platform.time.timeutil import DAY_S, IST_OFFSET_S

_MIN_TS, _MAX_TS = -(2**62), 2**62

_DDL = """
CREATE TABLE IF NOT EXISTS ohlcv (
    instrument_id INTEGER NOT NULL,
    interval_id   INTEGER NOT NULL,
    ts            INTEGER NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    volume INTEGER NOT NULL,
    oi INTEGER,
    PRIMARY KEY (instrument_id, interval_id, ts)
) WITHOUT ROWID;
"""


class SQLiteBarStore(BarStore):
    engine = "sqlite"

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._con = sqlite3.connect(path, check_same_thread=False, timeout=30, isolation_level=None)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=NORMAL")
        self._con.executescript(_DDL)
        self._lock = threading.RLock()   # sqlite3 connection shared across threads -> serialise access

    def _df(self, sql, params=()) -> pd.DataFrame:
        with self._lock:
            return pd.read_sql_query(sql, self._con, params=list(params))

    @staticmethod
    def _typed(df: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame({c: pd.Series(dtype=t) for c, t in
                            zip(BAR_COLUMNS, ["int64", "float64", "float64", "float64", "float64", "int64", "Int64"], strict=True)})
        if df.empty:
            return out
        df = df[BAR_COLUMNS].copy()
        for c, t in zip(BAR_COLUMNS, ["int64", "float64", "float64", "float64", "float64", "int64", "Int64"], strict=True):
            df[c] = df[c].astype(t)
        return df.reset_index(drop=True)

    def replace_range(self, instrument_id, interval_id, ts_from, ts_to, bars) -> int:
        clean = validate_bars(bars, ts_from, ts_to)
        rows = [(int(instrument_id), int(interval_id), int(r.ts), float(r.open), float(r.high), float(r.low),
                 float(r.close), int(r.volume), None if pd.isna(r.oi) else int(r.oi))
                for r in clean.itertuples(index=False)]
        with self._lock:
            cur = self._con.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                cur.execute("DELETE FROM ohlcv WHERE instrument_id=? AND interval_id=? AND ts BETWEEN ? AND ?",
                            (int(instrument_id), int(interval_id), int(ts_from), int(ts_to)))
                cur.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?)", rows)
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
        return len(rows)

    def upsert(self, instrument_id, interval_id, bars) -> int:
        if bars is None or len(bars) == 0:
            return 0
        ts = bars["ts"].astype("int64")
        clean = validate_bars(bars, int(ts.min()), int(ts.max()))
        rows = [(int(instrument_id), int(interval_id), int(r.ts), float(r.open), float(r.high), float(r.low),
                 float(r.close), int(r.volume), None if pd.isna(r.oi) else int(r.oi))
                for r in clean.itertuples(index=False)]
        with self._lock:
            cur = self._con.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                cur.executemany("INSERT OR REPLACE INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?)", rows)
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
        return len(rows)

    def delete_series(self, instrument_id, interval_id=None) -> int:
        with self._lock:
            if interval_id is None:
                cur = self._con.execute("DELETE FROM ohlcv WHERE instrument_id=?", (instrument_id,))
            else:
                cur = self._con.execute("DELETE FROM ohlcv WHERE instrument_id=? AND interval_id=?",
                                        (instrument_id, interval_id))
            return cur.rowcount

    def read_range(self, instrument_id, interval_id, ts_from=None, ts_to=None, limit=None, tail=False):
        order = "DESC" if tail else "ASC"
        lim = f" LIMIT {int(limit)}" if limit else ""
        df = self._df(f"SELECT ts, open, high, low, close, volume, oi FROM ohlcv WHERE instrument_id=? AND "
                      f"interval_id=? AND ts BETWEEN ? AND ? ORDER BY ts {order}{lim}",
                      (int(instrument_id), int(interval_id), _MIN_TS if ts_from is None else int(ts_from),
                       _MAX_TS if ts_to is None else int(ts_to)))
        if tail:
            df = df.iloc[::-1]
        return self._typed(df)

    def series_stats(self, instrument_id, interval_id) -> SeriesStats:
        with self._lock:
            first, last, n = self._con.execute("SELECT min(ts), max(ts), count(*) FROM ohlcv WHERE instrument_id=? "
                                               "AND interval_id=?", (instrument_id, interval_id)).fetchone()
        return SeriesStats(first, last, int(n))

    def coverage(self) -> pd.DataFrame:
        return self._df("SELECT instrument_id, interval_id, min(ts) AS first_ts, max(ts) AS last_ts, count(*) AS "
                        "bar_count FROM ohlcv GROUP BY 1, 2 ORDER BY 1, 2")

    def daily_counts(self, instrument_id, interval_id, ts_from=None, ts_to=None) -> pd.DataFrame:
        df = self._df(f"SELECT (ts + {IST_OFFSET_S}) / {DAY_S} AS day, count(*) AS bars FROM ohlcv WHERE "
                      f"instrument_id=? AND interval_id=? AND ts BETWEEN ? AND ? GROUP BY 1 ORDER BY 1",
                      (instrument_id, interval_id, _MIN_TS if ts_from is None else int(ts_from),
                       _MAX_TS if ts_to is None else int(ts_to)))
        df["trade_date"] = (pd.Timestamp("1970-01-01") + pd.to_timedelta(df["day"], unit="D")).dt.date
        return df[["trade_date", "bars"]]

    def resample(self, instrument_id, rule, ts_from=None, ts_to=None) -> pd.DataFrame:
        return self._typed(resample_frame(self.read_range(instrument_id, DAILY_ID, ts_from, ts_to), rule))

    def total_rows(self) -> int:
        with self._lock:
            return int(self._con.execute("SELECT count(*) FROM ohlcv").fetchone()[0])

    def export(self, path, fmt, series, ts_from=None, ts_to=None) -> int:
        frames = []
        for iid, ivid in series:
            df = self.read_range(iid, ivid, ts_from, ts_to)
            frames.append(df.assign(instrument_id=iid, interval_id=ivid))
        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=BAR_COLUMNS)
        out = out[["instrument_id", "interval_id", *BAR_COLUMNS]]
        if fmt == "csv":
            out.to_csv(path, index=False)
        elif fmt == "parquet":
            import duckdb  # writes Parquet natively; avoids a pyarrow dependency

            con = duckdb.connect()
            try:
                con.register("_out", out)
                safe = str(path).replace("'", "''")
                con.execute(f"COPY _out TO '{safe}' (FORMAT parquet, COMPRESSION zstd)")
            finally:
                con.close()
        else:
            raise ValueError("fmt must be csv or parquet")
        return len(out)

    def close(self) -> None:
        with self._lock:
            self._con.close()
