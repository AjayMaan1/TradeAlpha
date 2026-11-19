"""P3: paper-trading data feed capacity when bars are POLLED via OpenAlgo history (one request per symbol per bar
close, Historify's Follow model) at 80 % of the broker's history limits. Latency = 60 s finality buffer + queueing."""
import json, math, tomllib
T = tomllib.load(open("/workspace/broker_evaluation/broker_tariffs.toml", "rb"))
SHARE, FINAL = 0.8, 60
brokers = {k: v for k, v in T.items() if isinstance(v, dict) and isinstance(v.get("data", {}).get("rate"), dict)}
def feasible(rate, n, cadence_min):
    per_min = n / cadence_min; per_hour = per_min * 60
    if "per_min" in rate and per_min > rate["per_min"] * SHARE: return False, "per-minute limit"
    if "per_hour" in rate and per_hour > rate["per_hour"] * SHARE: return False, "per-hour limit"
    if "per_day" in rate and per_hour * 6.25 > rate["per_day"] * SHARE: return False, "per-day limit"
    return True, ""
def last_bar_delay(rate, n):
    r = rate.get("per_s", rate.get("per_min", 60) / 60) * SHARE
    burst = min(n, rate.get("per_min", 10**9) * SHARE)          # requests that fit in the first minute
    t = n / r
    if n > burst: t = max(t, 60 * (math.ceil(n / burst) - 1))
    return FINAL + t
res = {}
print(f"{'broker':24s} {'N':>4s} {'5m cadence: last bar at':>26s} {'1m cadence: last bar at':>26s}")
for key, b in brokers.items():
    rate = b["data"]["rate"]; res[key] = {}
    for n in (20, 50, 100, 200):
        cells = []
        for cad in (5, 1):
            ok, why = feasible(rate, n, cad)
            d = last_bar_delay(rate, n)
            cells.append(f"close+{d:4.0f}s" if ok and d < cad * 60 else f"NO ({why or 'slower than bar'})")
            res[key][f"{n}@{cad}m"] = cells[-1]
        print(f"{b['name'][:24]:24s} {n:4d} {cells[0]:>26s} {cells[1]:>26s}")
json.dump(res, open("/workspace/backtest_evaluation/results/p3_live_capacity.json", "w"), indent=1)
