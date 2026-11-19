"""P4: 1-year data plan - backfill requests/time per broker (1m x 1 y + D x 10 y, 80 % budget) and storage incl. 3 months forward."""
import json, math, sys, tomllib
sys.path.insert(0, "/workspace/broker_evaluation")
from broker_compare import backfill_seconds, fmt_t
T = tomllib.load(open("/workspace/broker_evaluation/broker_tariffs.toml", "rb"))
B = {k: v for k, v in T.items() if isinstance(v, dict) and isinstance(v.get("data", {}).get("max_days"), dict)}
res = {"backfill": {}, "storage": {}}
for n in (50, 100, 200):
    for k, b in B.items():
        md, rate = b["data"]["max_days"], b["data"]["rate"]
        r = n * math.ceil(365 / md["1m"]) + n * math.ceil(3650 / md["D"])
        res["backfill"][f"{k}@{n}"] = {"requests": r, "time": fmt_t(backfill_seconds(r, rate, 0.8))}
    bars = n * (248 + 63) * 375
    res["storage"][n] = {"bars_M": round(bars / 1e6, 1), "duckdb_MB": round(bars * 13 / 1e6), "snapshot_MB": round(bars * 14.5 / 1e6)}
print(json.dumps(res, indent=1))
json.dump(res, open("/workspace/backtest_evaluation/results/p4_data_plan.json", "w"), indent=1)
