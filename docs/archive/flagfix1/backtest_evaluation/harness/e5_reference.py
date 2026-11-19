import sys, time, json; sys.path.insert(0, "/tmp/eval")
from intraday_common import *
data = load_5m()
t0 = time.perf_counter(); r = {s: reference(d) for s, d in data.items()}; dt = time.perf_counter() - t0
json.dump({"engine": "reference", "seconds": dt, "results": r}, open("/tmp/eval/e5_reference.json", "w"))
print(f"reference (pure python loop): {dt:.1f}s; EQ00 -> {r['EQ00']}; total entries {sum(v[1] for v in r.values()):,}, stop exits {sum(v[2] for v in r.values()):,}")
