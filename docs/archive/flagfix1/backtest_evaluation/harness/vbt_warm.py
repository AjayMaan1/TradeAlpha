"""Fair vectorbt timing: compile first, then time a warm call in the same process (signals prebuilt)."""
import sys, time, json, resource, warnings; sys.path.insert(0, "/tmp/eval"); warnings.filterwarnings("ignore")
import numpy as np, vectorbt as vbt
exec(open("/tmp/eval/sweep_vbt_intraday.py").read().split("t_sig = ")[0].replace("K = int(sys.argv[1])", "K = 8"))
tile = lambda X: np.tile(X, (1, K))
args = dict(price=tile(O), open=tile(O), high=tile(H), low=tile(L), sl_stop=sl[None, :], size=np.inf, size_granularity=1,
            fees=0.0003, init_cash=1e6, freq="5min", stop_entry_price="fillprice")
CC = tile(C)
t0 = time.perf_counter(); pf = vbt.Portfolio.from_signals(CC, ent, ex, **args); pf.final_value(); cold = time.perf_counter() - t0
t0 = time.perf_counter(); pf = vbt.Portfolio.from_signals(CC, ent, ex, **args); sim = time.perf_counter() - t0
t0 = time.perf_counter(); pf.final_value(); pf.sharpe_ratio(); stats = time.perf_counter() - t0
print(json.dumps({"backtests": K * 50, "cold_first_call_s": round(cold, 1), "warm_simulation_s": round(sim, 2),
                  "warm_metrics_s": round(stats, 2), "warm_per_backtest_ms": round((sim + stats) / (K * 50) * 1000, 1),
                  "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)}))
