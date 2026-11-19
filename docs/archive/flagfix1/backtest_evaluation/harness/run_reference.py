import time, sys; sys.path.insert(0, "/tmp/eval")
from parity_common import *
data = load_daily()
for mode in ("next_open", "close"):
    t0 = time.perf_counter(); r = {s: reference(df, mode) for s, df in data.items()}
    report("reference", r, time.perf_counter() - t0, mode)
