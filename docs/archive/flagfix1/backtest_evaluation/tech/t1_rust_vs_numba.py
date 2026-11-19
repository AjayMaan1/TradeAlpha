"""T1: Rust (PyO3 + rayon) vs numba for the same execution kernel: parity, single-run speed, sweep speed, cold start."""
import json, subprocess, sys, time
sys.path.insert(0, "/tmp/eval"); sys.path.insert(0, "/workspace/backtest_evaluation/prototype")
import numpy as np
import hbt_core
from hbt import costs, data as hd, kernel as K
import intraday_common as ic
out = {}
# ---- parity on E5 (vs independent reference)
data = ic.load_5m(); ref = json.load(open("/tmp/eval/e5_reference.json"))["results"]
prep = {}
for s, d in data.items():
    e, x, sq = ic.signals(d)
    r = np.ascontiguousarray(costs.rate_vectors("FLAT_0.03PCT", (d.ts.to_numpy() + 19800) // 86400))
    prep[s] = [np.ascontiguousarray(d[k].to_numpy()) for k in ("open", "high", "low", "close")] + [e, x, sq, r]
errs = []
for s, a in prep.items():
    v = hbt_core.simulate(*a, ic.STOP, 0.0, ic.CASH, 1.0)
    errs.append(abs(v[0] - ref[s][0]) / ref[s][0]); assert int(v[1]) == ref[s][1] and int(v[2]) == ref[s][2]
out["rust_parity_max_rel_err"] = max(errs); print(f"Rust parity vs reference: max rel err {max(errs):.1e}, entries/stops identical 50/50")
# ---- single-run speed (50 backtests, 39,150 bars each), warm
buf = np.zeros(K.N_RESULT)
for name, fn in (("numba", lambda a: K.simulate(*a[:8], ic.STOP, 0.0, ic.CASH, 1.0, 0, buf)),
                 ("rust", lambda a: hbt_core.simulate(*a, ic.STOP, 0.0, ic.CASH, 1.0))):
    for a in prep.values(): fn(a)
    xs = []
    for _ in range(5):
        t0 = time.perf_counter(); [fn(a) for a in prep.values()]; xs.append(time.perf_counter() - t0)
    out[f"{name}_50_runs_ms"] = round(min(xs) * 1000, 1); print(f"{name:6s} 50 intraday backtests (single thread): {min(xs)*1000:6.1f} ms")
# ---- sweep (same 12,200-backtest grid as the strategy-search demo)
u = hd.from_parquet_dir("/tmp/eval/m5", "m5"); n = len(u.ts)
tod, sn, sq = u.minute_of_day.astype(np.int64), u.same_session_next, u.mask_at(915)
rates = np.ascontiguousarray(costs.rate_vectors("EQ_INTRADAY", u.trade_day))
F = [5, 8, 9, 12, 15, 20, 25, 30]; SL = [21, 30, 40, 50, 60, 80, 100, 120]; ST = [0.003, 0.005, 0.0075, 0.01]
O, H, L, C = (np.ascontiguousarray(m) for m in (u.open, u.high, u.low, u.close))
nb_args = (O, H, L, C, tod, sn, sq, rates, np.array(F), np.array(SL), np.array(ST), 0, n, 895, 120, 1e6, 1.0)
K.sweep_ema_cross(*nb_args)
t0 = time.perf_counter(); rn = K.sweep_ema_cross(*nb_args); tn = time.perf_counter() - t0
hbt_core.sweep_ema_cross(O, H, L, C, tod, sn, sq, rates, F, SL, ST, 0, n, 895, 120, 1e6, 1.0)
t0 = time.perf_counter(); rr = hbt_core.sweep_ema_cross(O, H, L, C, tod, sn, sq, rates, F, SL, ST, 0, n, 895, 120, 1e6, 1.0); tr = time.perf_counter() - t0
# rust rows are ordered (sym, fast, slow, stop); numba array is [fast, slow, stop, sym, :]
rn_rows = np.transpose(rn, (3, 0, 1, 2, 4)).reshape(-1, K.N_RESULT)
diff = np.nanmax(np.abs(rr[:, 0] - rn_rows[:, 0]) / np.where(rn_rows[:, 0] == 0, 1, rn_rows[:, 0]))
out.update(numba_sweep_s=round(tn, 2), rust_sweep_s=round(tr, 2), sweep_max_rel_diff=float(diff))
print(f"sweep 12,200 backtests: numba {tn:.2f}s  rust {tr:.2f}s  (equity max rel diff rust vs numba {diff:.1e})")
# ---- cold start: fresh interpreter, import + first call
snip = ("import time;t=time.perf_counter();import numpy as np;{imp};a=np.ones(1000);b=np.zeros(1000,bool);r=np.zeros((1000,4));"
        "{call};print(round(time.perf_counter()-t,2))")
for name, imp, call in (("numba", "sys=__import__('sys');sys.path.insert(0,'/workspace/backtest_evaluation/prototype');from hbt import kernel as K",
                         "K.simulate(a,a,a,a,b,b,b,r,0.01,0.0,1e6,1.0,0,np.zeros(10))"),
                        ("rust", "import hbt_core", "hbt_core.simulate(a,a,a,a,b,b,b,r,0.01,0.0,1e6,1.0)")):
    v = [float(subprocess.run([sys.executable, "-c", snip.format(imp=imp, call=call)], capture_output=True, text=True).stdout) for _ in range(3)]
    out[f"{name}_cold_start_s"] = min(v); print(f"{name:6s} cold start (fresh process, import + first call, cache warm): {min(v):.2f}s")
json.dump(out, open("/workspace/backtest_evaluation/results/t1_rust_vs_numba.json", "w"), indent=1)
