"""Phase 1.3 performance (design NFR-1..6): evaluator throughput, single IR run, sweep sharing, streaming step, compile."""
import json, sys, time, statistics
H = "/workspace/historify-master/historify"
sys.path[:0] = [H, H + "/lab"]
import numpy as np, pandas as pd, hbt_core
from hbt import Dataset, backtest, exec_spec, instrument, ir, run, sweep
from hbt.costs import cost_model
from tests.lab import oracles as O

def best(f, k=7):
    xs = []
    for _ in range(k):
        t = time.perf_counter(); f(); xs.append(time.perf_counter() - t)
    return min(xs)

res = {}
ts, o, h, lo, c = O.intraday_market(1, sessions=522)                     # 39,150 5m bars (~2 years)
df = pd.DataFrame({"ts": ts, "open": o, "high": h, "low": lo, "close": c, "volume": 10**6})
ds = Dataset.from_frame(df, "5m", tfs=["D"])
n = len(ds)
costs = cost_model("EQ_INTRADAY", broker="zerodha")
spec = exec_spec(squareoff_at="15:15", no_new_entries_after=None)
# NFR-2: EMA cross (signals + run)
ema_doc = {"ir_version": 1, "name": "ema", "signals": {"f": {"fn": "ema", "args": [{"col": "close"}, 9]},
           "s": {"fn": "ema", "args": [{"col": "close"}, 21]}},
           "rules": [{"when": {"fn": "cross_below", "args": [{"sig": "f"}, {"sig": "s"}]}, "do": {"exit_long": {}}},
                     {"when": {"fn": "cross_above", "args": [{"sig": "f"}, {"sig": "s"}]},
                      "do": {"enter_long": {"bracket": {"stop": {"pct": 0.005}}}}}]}
prog = ir.compile(ema_doc)
core = ds.core()
res["bars"] = n
res["ema_signals_ms"] = round(best(lambda: hbt_core.eval_signals(prog, core, ["f", "s"], None)) * 1e3, 2)
kw = dict(spec=spec, inst=instrument(tick=1), costs=costs)
res["ema_ir_run_ms"] = round(best(lambda: run(prog, ds, **kw)) * 1e3, 2)
r = run(prog, ds, **kw)
le = np.zeros(n, bool); lx = np.zeros(n, bool)
le[r.intents.loc[r.intents.kind == "enter_long", "bar"].to_numpy()] = True
res["ema_array_run_ms"] = round(best(lambda: backtest(ds.exec, 300, le, lx, plan={"entry_type": "market", "entry_offset_pct": 0.0, "entry_expiry_bars": 0, "stop_pct": 0.005, "target_pct": None, "trail_pct": None, "time_stop_bars": None, "sizing": "all_in", "sizing_value": 0.0}, **kw)) * 1e3, 2)
res["fills"] = len(r.fills)
# NFR-1 / NFR-3: 40-node program throughput
sigs = {}
for k in range(8):
    sigs[f"e{k}"] = {"fn": "ema", "args": [{"col": "close"}, 5 + 7 * k]}
for k in range(4):
    sigs[f"s{k}"] = {"fn": "sma", "args": [{"col": "close"}, 10 + 20 * k]}
    sigs[f"r{k}"] = {"fn": "rsi", "args": [{"col": "close"}, 7 + 7 * k]}
for k in range(7):
    sigs[f"d{k}"] = {"fn": "gt", "args": [{"fn": "sub", "args": [{"sig": f"e{k}"}, {"sig": f"e{k+1}"}]}, 0]}
big = {"ir_version": 1, "name": "big", "signals": sigs, "rules": [{"when": {"fn": "and", "args": [{"sig": f"d{k}"} for k in range(7)]}, "do": {"enter_long": {}}}]}
bp = ir.compile(big)
nodes = len([x for x in bp.plan("5m")["nodes"] if x["frame"] != "const"])
t = best(lambda: hbt_core.eval_signals(bp, core, ["d0"], None))                 # all market nodes run; one output
res["big_program_nodes"] = nodes
res["big_program_ms"] = round(t * 1e3, 2)
res["node_updates_per_s_M"] = round(nodes * n / t / 1e6, 1)
# NFR-4: sweep 256 EMA variants, shared market nodes
P = {"fast": {"type": "int", "default": 9, "min": 2, "max": 60}, "slow": {"type": "int", "default": 21, "min": 5, "max": 200}}
sw = dict(ema_doc, params=P, signals={"f": {"fn": "ema", "args": [{"col": "close"}, {"param": "fast"}]},
                                       "s": {"fn": "ema", "args": [{"col": "close"}, {"param": "slow"}]}})
sp = ir.compile(sw)
variants = [{"fast": f, "slow": s} for f in range(3, 35, 2) for s in range(10, 170, 10)]
t0 = time.perf_counter(); tab = sweep(sp, ds, variants, **kw); dt = time.perf_counter() - t0
res["sweep_variants"] = len(tab); res["sweep_s"] = round(dt, 3); res["sweep_runs_per_s"] = round(len(tab) / dt)
out = hbt_core.sweep_ir(sp, core, json.dumps(variants), json.dumps({**{k: v for k, v in spec.items()}, "participation_cap": 0.1, "product": "MIS", "no_new_entries_after": None, "squareoff_at": 915}), "{}", '{"tick":1}', json.dumps({k: v for k, v in costs.items() if k in ("versions", "brokerage", "dp_paise")}), 100_000_000, 18600.0)
res["sweep_shared_market_nodes"] = out["shared_nodes"]; res["sweep_unshared_would_be"] = 1 + 2 * len(variants)
# signals-only sharing: one program with all variants' EMAs vs one program per variant
sig_alone = sum(best(lambda v=v: hbt_core.eval_signals(sp, core, ["f", "s"], json.dumps(v)), 1) for v in variants)
res["sweep_signals_unshared_ms"] = round(sig_alone * 1e3, 1)
# NFR-5: compile time
res["compile_ms_small"] = round(best(lambda: ir.compile(ema_doc)) * 1e3, 3)
chain = {"fn": "add", "args": [{"col": "close"}, 0]}
for i in range(1, 1000):
    chain = {"fn": "add", "args": [chain, i]} if i % 50 else {"fn": "ema", "args": [chain, 3]}
deep = {"ir_version": 1, "name": "deep", "signals": {}, "rules": [{"when": {"fn": "gt", "args": [{"fn": "sma", "args": [{"col": "close"}, k + 2]}, 0]}, "do": {"exit_all": {}}} for k in range(60)]}
wide = {"ir_version": 1, "name": "wide", "signals": {f"x{k}": {"fn": "gt", "args": [{"fn": "sma", "args": [{"fn": "ema", "args": [{"col": "close"}, k % 97 + 2]}, k % 89 + 2]}, {"fn": "wma", "args": [{"col": "high"}, k % 83 + 2]}]} for k in range(800)}, "rules": [{"when": True, "do": {"exit_all": {}}}]}
res["compile_ms_4000_nodes"] = round(best(lambda: ir.compile(wide), 3) * 1e3, 1)
# NFR-6: streaming step, 100 symbols x 40 nodes: per-bar cost of MarketEval.advance over the big program
t = hbt_core.stream_signals
st = best(lambda: hbt_core.stream_signals(bp, core, ["d0"], None), 3)
res["stream_step_us_per_symbol"] = round(st / n * 1e6, 2)
res["stream_step_us_100_symbols"] = round(st / n * 1e6 * 100, 1)
print(json.dumps(res, indent=1))
json.dump(res, open("/workspace/audit2/m6_ir_perf.json", "w"), indent=1)
