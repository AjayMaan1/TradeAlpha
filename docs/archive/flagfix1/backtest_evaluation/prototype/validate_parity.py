"""
Prove the hbt kernel reproduces the independent reference implementations:
  E3 daily SMA(20/50), next-open fills, 0.1 % fee           (vs /tmp/eval/parity_reference_next_open.json)
  E5 intraday 5m EMA(9/21), 0.5 % bracket stop, MIS 15:15   (vs /tmp/eval/e5_reference.json)
"""
import glob, json, sys, time
sys.path.insert(0, "/tmp/eval"); sys.path.insert(0, ".")
import numpy as np
from hbt import data as hd, costs, kernel as K
# ---------------------------------------------------------------- E3 daily
import parity_common as pc
snap = glob.glob("/tmp/eval/inst/snapshots/eval-50-*")[0]
u = hd.from_snapshot(snap, "D")
rates = costs.rate_vectors("FLAT_0.1PCT", u.trade_day)
ref = json.load(open("/tmp/eval/parity_reference_next_open.json"))["results"]
out = np.zeros(K.N_RESULT); errs = []
t0 = time.perf_counter()
for j, s in enumerate(u.symbols):
    e, x = pc.signals(u.close[:, j])
    K.simulate(u.open[:, j], u.high[:, j], u.low[:, j], u.close[:, j], e, x, np.zeros(len(u.ts), np.bool_), rates,
               0.0, 0.0, pc.CASH, 1.0, 0, out)
    errs.append(abs(out[K.R_EQUITY] - ref[s][0]) / ref[s][0])
    assert int(out[K.R_TRADES]) == ref[s][1], (s, out[K.R_TRADES], ref[s][1])
print(f"E3 daily  : hbt vs reference  max rel err {max(errs):.1e}  trades identical 50/50  (snapshot {u.provenance['snapshot_id'][:8]})")
# ---------------------------------------------------------------- E5 intraday
import intraday_common as ic
data = ic.load_5m()
ref5 = json.load(open("/tmp/eval/e5_reference.json"))["results"]
errs, t_sim = [], 0.0
for s, d in data.items():
    e, x, sq = ic.signals(d)
    r = costs.rate_vectors("FLAT_0.03PCT", (d.ts.to_numpy() + 19800) // 86400)
    t0 = time.perf_counter()
    K.simulate(d.open.to_numpy(), d.high.to_numpy(), d.low.to_numpy(), d.close.to_numpy(), e, x, sq, r,
               ic.STOP, 0.0, ic.CASH, 1.0, 0, out)
    t_sim += time.perf_counter() - t0
    errs.append(abs(out[K.R_EQUITY] - ref5[s][0]) / ref5[s][0])
    assert int(out[K.R_TRADES]) == ref5[s][1] and int(out[K.R_STOPS]) == ref5[s][2], s
print(f"E5 intraday: hbt vs reference  max rel err {max(errs):.1e}  entries+stop exits identical 50/50  "
      f"(kernel time {t_sim*1000:.0f} ms for 50 x 39,150 bars)")
