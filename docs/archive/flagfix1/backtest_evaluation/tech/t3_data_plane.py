"""T3: fastest path from Historify storage to engine-ready columns (9.79 M 1m bars, 50 symbols), no pyarrow."""
import glob, json, os, sys, time, statistics
import numpy as np, polars as pl, duckdb
BARS = "/tmp/eval/inst/bars.duckdb"; SNAP = glob.glob("/tmp/eval/inst/snapshots/eval-50-*")[0]
OUT = "/tmp/eval/t3"; os.makedirs(OUT, exist_ok=True)
Q = "SELECT instrument_id, ts, open, high, low, close, volume FROM ohlcv WHERE interval_id = 1 ORDER BY instrument_id, ts"
res = {}
def T(name, fn, k=3):
    fn(); xs = []
    for _ in range(k):
        t0 = time.perf_counter(); r = fn(); xs.append(time.perf_counter() - t0)
    res[name] = round(statistics.median(xs), 3); print(f"{name:64s} {res[name]:7.3f} s"); return r
con = duckdb.connect(BARS, read_only=True)
T("DuckDB store -> numpy (fetchnumpy)", lambda: con.execute(Q).fetchnumpy())
df = T("DuckDB store -> Polars via Arrow PyCapsule (zero-copy, no pyarrow)", lambda: pl.DataFrame(con.sql(Q)))
print("   rows", df.height)
files = sorted(glob.glob(f"{SNAP}/data/*_NSE_1m.parquet"))
T("snapshot Parquet zstd (50 files) -> Polars (parallel)", lambda: pl.read_parquet(files, columns=["ts", "open", "high", "low", "close", "volume"]))
# candidate engine cache formats
df.write_ipc(f"{OUT}/m1.arrow", compression="uncompressed"); df.write_ipc(f"{OUT}/m1_lz4.arrow", compression="lz4")
df.write_parquet(f"{OUT}/m1.parquet", compression="zstd")
T("Arrow IPC uncompressed -> Polars", lambda: pl.read_ipc(f"{OUT}/m1.arrow"))
T("Arrow IPC uncompressed -> close column as numpy", lambda: pl.read_ipc(f"{OUT}/m1.arrow")["close"].to_numpy())
T("Arrow IPC LZ4 -> Polars", lambda: pl.read_ipc(f"{OUT}/m1_lz4.arrow"))
T("single Parquet zstd -> Polars", lambda: pl.read_parquet(f"{OUT}/m1.parquet"))
T("Polars lazy scan: 1 symbol, 3 months (predicate pushdown)",
  lambda: pl.scan_parquet(f"{OUT}/m1.parquet").filter((pl.col("instrument_id") == 7) & pl.col("ts").is_between(1704067200, 1711929600)).collect())
sizes = {os.path.basename(f): round(os.path.getsize(f) / 1e6, 1) for f in glob.glob(f"{OUT}/*")}
sizes["bars.duckdb (all intervals)"] = round(os.path.getsize(BARS) / 1e6, 1)
sizes["snapshot 1m parquet (50 files)"] = round(sum(os.path.getsize(f) for f in files) / 1e6, 1)
print("sizes MB:", sizes)
res["sizes_mb"] = sizes
json.dump(res, open("/workspace/backtest_evaluation/results/t3_data_plane.json", "w"), indent=1)
