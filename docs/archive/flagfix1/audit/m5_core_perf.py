"""Metric: Phase 1.2 core performance (full engine: costs per charge in paise, brackets, MIS rules)."""
import json, os, subprocess, sys, time, statistics
sys.path.insert(0, "/workspace/historify-master/historify"); sys.path.insert(0, "/workspace/historify-master/historify/lab")
import numpy as np, pandas as pd, hbt_core
from hbt import bars_from_frame, exec_spec, order_plan, instrument
from hbt.costs import core_costs, cost_model
from hbt.spec import core_json, resolve_spec
from tests.lab import oracles as O
res = {}
ts, o, h, lo, c = O.intraday_market(1, sessions=522)                       # 39,150 5m bars
bars = bars_from_frame(pd.DataFrame({"ts": ts, "open": o, "high": h, "low": lo, "close": c, "volume": 10**6}))
bars = {k: np.ascontiguousarray(v) for k, v in bars.items()}
le, lx, _ = O.e5_signals(ts, c); z = np.zeros(len(ts), np.uint8)
args = (core_json(resolve_spec(exec_spec(squareoff_at="15:15", no_new_entries_after=None), 300)), json.dumps(order_plan(stop_pct=0.005)),
        json.dumps(instrument(tick=1)), core_costs(cost_model("EQ_INTRADAY", {"pct": 0.0003, "cap": 20})), 100_000_000, 18600.0)
call = lambda: hbt_core.run(bars, 300, le.astype(np.uint8), lx.astype(np.uint8), z, z, *args)
call(); xs = []
for _ in range(30):
    t0 = time.perf_counter(); r = call(); xs.append(time.perf_counter() - t0)
res["single_run_39k_bars_ms"] = round(statistics.median(xs) * 1000, 2)
res["fills_per_run"] = int(len(r["fills"]["qty"]))
# batch: 256 signal sets (EMA fast/slow grid) over the same bars, rayon parallel
sets = []
for f in range(3, 35, 2):
    for s in range(10, 170, 10):
        e1, e2 = O.ema(c, f), O.ema(c, s); a = e1 > e2; p = np.r_[False, a[:-1]]
        sets.append(((a & ~p).astype(np.uint8), (~a & p).astype(np.uint8)))
K = len(sets)
LE = np.ascontiguousarray(np.stack([x[0] for x in sets], 1)); LX = np.ascontiguousarray(np.stack([x[1] for x in sets], 1))
Z = np.zeros_like(LE)
plans = [json.dumps(order_plan(stop_pct=0.005))] * K
bargs = (args[0], plans, *args[2:])
hbt_core.run_batch(bars, 300, LE, LX, Z, Z, *bargs)
t0 = time.perf_counter(); out = hbt_core.run_batch(bars, 300, LE, LX, Z, Z, *bargs); dt = time.perf_counter() - t0
res["batch_sets"] = K; res["batch_s"] = round(dt, 3); res["batch_backtests_per_s"] = round(K / dt)
# cold start (fresh interpreter): import + first run of a small backtest
code = ("import time,sys;t=time.perf_counter();sys.path.insert(0,'/workspace/historify-master/historify/lab');"
        "import numpy as np,hbt;import hbt_core;from hbt import *;"
        "b={k:np.arange(1,11,dtype=np.int64)*100 for k in ('open','high','low','close')};b['ts']=np.arange(10,dtype=np.int64)*300+1741000000;"
        "b['volume']=np.ones(10,np.int64);b['day']=(b['ts']+19800)//86400;b['minute']=((b['ts']+19800)%86400)//60;"
        "backtest(b,300,np.ones(10),None,costs=hbt.cost_model(zero=True));print(round(time.perf_counter()-t,3))")
cs = [float(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip() or "nan") for _ in range(3)]
res["cold_start_s"] = min(cs)
res["wheel_kb"] = None
# Arrow ingestion at realistic scale: 1m bars of one symbol (195,750) and all 50 symbols from the realistic store
import duckdb
con = duckdb.connect("/tmp/eval/inst_p11/bars.duckdb", read_only=True)
q1 = "SELECT ts, open::DOUBLE open, high::DOUBLE high, low::DOUBLE low, close::DOUBLE close, volume FROM ohlcv WHERE instrument_id=1 AND interval_id=1 ORDER BY ts"
hbt_core.bars_from_arrow(con.sql(q1))
t0 = time.perf_counter(); b1 = hbt_core.bars_from_arrow(con.sql(q1)); res["arrow_1m_one_symbol_ms"] = round((time.perf_counter() - t0) * 1000, 1)
res["arrow_1m_one_symbol_bars"] = int(len(b1["ts"]))
qa = q1.replace("instrument_id=1 AND ", "")
t0 = time.perf_counter(); ba = hbt_core.bars_from_arrow(con.sql(qa.replace("ORDER BY ts", "ORDER BY instrument_id, ts")))
res["arrow_1m_50_symbols_s"] = round(time.perf_counter() - t0, 2); res["arrow_1m_50_symbols_bars"] = int(len(ba["ts"]))
print(json.dumps(res, indent=1)); json.dump(res, open("/workspace/audit/m5_core_perf_after_1_3.json", "w"), indent=1)
