"""Final performance re-measurement after phases 1.4-1.7 (T1.6.1 gates). Best-of-N timings on this sandbox
(4 vCPU, shared): evaluator throughput, single IR run, sweeps (1 core and 4 worker processes), compile,
streaming step, symbol-major loader (10 M bars), paper host per-bar cost."""
import json, os, sys, time, tempfile
H = "/workspace/historify-master/historify"
sys.path[:0] = [H, H + "/lab"]
import numpy as np, pandas as pd, hbt_core
from hbt import Dataset, exec_spec, instrument, ir, run, sweep
from hbt.costs import cost_model
from tests.lab import oracles as O


def best(f, k=5):
    xs = []
    for _ in range(k):
        t = time.perf_counter(); f(); xs.append(time.perf_counter() - t)
    return min(xs)


def market():
    ts, o, h, lo, c = O.intraday_market(1, sessions=522)                     # 39,150 5m bars (~2 years)
    df = pd.DataFrame({"ts": ts, "open": o, "high": h, "low": lo, "close": c, "volume": 10**6})
    return Dataset.from_frame(df, "5m", tfs=["D"])


EMA = {"ir_version": 1, "name": "ema",
       "params": {"fast": {"type": "int", "default": 9, "min": 2, "max": 60}, "slow": {"type": "int", "default": 21, "min": 5, "max": 200}},
       "signals": {"f": {"fn": "ema", "args": [{"col": "close"}, {"param": "fast"}]}, "s": {"fn": "ema", "args": [{"col": "close"}, {"param": "slow"}]}},
       "rules": [{"when": {"fn": "cross_below", "args": [{"sig": "f"}, {"sig": "s"}]}, "do": {"exit_long": {}}},
                 {"when": {"fn": "cross_above", "args": [{"sig": "f"}, {"sig": "s"}]}, "do": {"enter_long": {"bracket": {"stop": {"pct": 0.005}}}}}]}
KW = dict(spec=exec_spec(squareoff_at="15:15", no_new_entries_after=None), inst=instrument(tick=1),
          costs=cost_model("EQ_INTRADAY", broker="zerodha"))
def main():
    res = {"cpu_count": os.cpu_count()}
    ds = market()
    n = len(ds)
    res["bars"] = n
    prog = ir.compile(EMA)
    core = ds.core()
    res["ema_ir_run_ms"] = round(best(lambda: run(prog, ds, **KW)) * 1e3, 2)
    # evaluator throughput: 40-node program
    sigs = {f"e{k}": {"fn": "ema", "args": [{"col": "close"}, 5 + 7 * k]} for k in range(8)}
    for k in range(4):
        sigs[f"s{k}"] = {"fn": "sma", "args": [{"col": "close"}, 10 + 20 * k]}
        sigs[f"r{k}"] = {"fn": "rsi", "args": [{"col": "close"}, 7 + 7 * k]}
    for k in range(7):
        sigs[f"d{k}"] = {"fn": "gt", "args": [{"fn": "sub", "args": [{"sig": f"e{k}"}, {"sig": f"e{k+1}"}]}, 0]}
    big = {"ir_version": 1, "name": "big", "signals": sigs,
           "rules": [{"when": {"fn": "and", "args": [{"sig": f"d{k}"} for k in range(7)]}, "do": {"enter_long": {}}}]}
    bp = ir.compile(big)
    nodes = len([x for x in bp.plan("5m")["nodes"] if x["frame"] != "const"])
    t = best(lambda: hbt_core.eval_signals(bp, core, ["d0"], None))
    res["big_program_nodes"] = nodes
    res["node_updates_per_s_M"] = round(nodes * n / t / 1e6, 1)
    # sweep: 256 variants; the core runs the variants on a rayon thread pool (all cores) with fused metric
    # accumulators (no equity vectors)
    variants = [{"fast": f, "slow": s} for f in range(3, 35, 2) for s in range(10, 170, 10)]
    t = best(lambda: sweep(prog, ds, variants, **KW), 3)
    res["sweep_256_s"] = round(t, 3)
    res["sweep_runs_per_s_all_cores"] = round(len(variants) / t)
    # compile
    res["compile_ms_small"] = round(best(lambda: ir.compile(EMA)) * 1e3, 3)
    wide = {"ir_version": 1, "name": "wide", "signals": {f"x{k}": {"fn": "gt", "args": [{"fn": "sma", "args": [{"fn": "ema", "args": [{"col": "close"}, k % 97 + 2]}, k % 89 + 2]}, {"fn": "wma", "args": [{"col": "high"}, k % 83 + 2]}]} for k in range(800)}, "rules": [{"when": True, "do": {"exit_all": {}}}]}
    res["compile_ms_4000_nodes"] = round(best(lambda: ir.compile(wide), 3) * 1e3, 1)
    wide10 = {**wide, "signals": {f"x{k}": {"fn": "gt", "args": [{"fn": "sma", "args": [{"fn": "ema", "args": [{"col": "close"}, k % 97 + 2]}, k % 89 + 2]}, {"fn": "wma", "args": [{"col": "high"}, k % 83 + 2]}]} for k in range(1100)}}
    res["compile_ms_9900_nodes"] = round(best(lambda: ir.compile(wide10), 3) * 1e3, 1)
    # streaming step
    st = best(lambda: hbt_core.stream_signals(bp, core, ["d0"], None), 3)
    res["stream_step_us_100_symbols"] = round(st / n * 1e6 * 100, 1)
    # symbol-major loader: 10 M bars (100 symbols x 100k) from one Arrow stream
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE b AS SELECT (i % 100)::BIGINT AS instrument_id, (1700000000 + (i // 100) * 60)::BIGINT AS ts, "
                "(100.0 + (i % 7) * 0.05)::DOUBLE AS open, 100.5::DOUBLE AS high, 99.5::DOUBLE AS low, 100.25::DOUBLE AS close, "
                "1000::BIGINT AS volume "
                "FROM range(10000000) t(i)")
    def load():
        rel = con.sql("SELECT instrument_id, ts, open, high, low, close, volume FROM b ORDER BY instrument_id, ts")
        return hbt_core.bars_from_arrow_multi(rel, 100)
    t0 = time.perf_counter(); out = load(); res["load_10M_bars_s_incl_sort"] = round(time.perf_counter() - t0, 3)
    con.execute("CREATE TABLE bs AS SELECT * FROM b ORDER BY instrument_id, ts")
    def load_sorted():
        rel = con.sql("SELECT instrument_id, ts, open, high, low, close, volume FROM bs")
        return hbt_core.bars_from_arrow_multi(rel, 100)
    res["load_10M_bars_s"] = round(best(load_sorted, 3), 3)
    res["load_symbols"] = len(out)
    # paper host: per-bar cost (1 member, 5m, 5-day warm-up)
    os.environ.setdefault("HISTORIFY_SECRET_BACKEND", "file")
    from datetime import UTC, datetime
    from app.lab.paper.drills import _scratch_services, scenario
    sv = _scratch_services(tempfile.mkdtemp(), datetime(2025, 1, 1, 10, tzinfo=UTC))
    sid, replay, _, _ = scenario(sv, warm_days=5, replay_days=1)
    t0 = time.perf_counter(); sv.lab_paper.replay(sid, replay); dt = time.perf_counter() - t0
    res["paper_ms_per_member_bar"] = round(dt / sum(len(d) for d in replay.values()) * 1e3, 1)
    sv.close()
    print(json.dumps(res, indent=1))
    json.dump(res, open("/workspace/audit3/m7_perf_final.json", "w"), indent=1)


if __name__ == "__main__":
    main()
