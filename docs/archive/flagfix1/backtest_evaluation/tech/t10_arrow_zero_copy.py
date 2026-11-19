"""T10: DuckDB -> Rust through the Arrow C stream interface (PyCapsule), no pyarrow, no numpy conversion."""
import json, time, statistics
import duckdb, numpy as np, hbt_arrow_probe
con = duckdb.connect("/tmp/eval/inst/bars.duckdb", read_only=True)
Q = "SELECT ts, close FROM ohlcv WHERE interval_id = 1 ORDER BY instrument_id, ts"
def rust(): return hbt_arrow_probe.scan(con.sql(Q))
def numpy_path():
    a = con.execute(Q).fetchnumpy(); return len(a["ts"]), 0, float(a["close"].sum()), int(a["ts"].min()), int(a["ts"].max())
res = {}
for name, fn in (("duckdb -> Arrow C stream -> Rust", rust), ("duckdb -> fetchnumpy -> numpy", numpy_path)):
    fn(); xs = []
    for _ in range(3):
        t0 = time.perf_counter(); r = fn(); xs.append(time.perf_counter() - t0)
    res[name] = {"s": round(statistics.median(xs), 3), "rows": r[0], "batches": r[1], "sum_close": r[2]}
    print(f"{name:36s} {statistics.median(xs):6.3f}s rows={r[0]:,} batches={r[1]} sum(close)={r[2]:.4f}")
k = list(res); assert res[k[0]]["rows"] == res[k[1]]["rows"] and abs(res[k[0]]["sum_close"] - res[k[1]]["sum_close"]) / res[k[1]]["sum_close"] < 1e-12
print("identical results; pyarrow importable:", __import__("importlib").util.find_spec("pyarrow") is not None)
json.dump(res, open("/workspace/backtest_evaluation/results/t10_arrow_zero_copy.json", "w"), indent=1)
