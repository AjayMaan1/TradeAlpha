"""Metric: backup + restore on the realistic instance (9.9 M bars): time, size, exactness per series."""
import json, os, sys, time
sys.path.insert(0, "/workspace/historify-master/historify"); os.environ["HISTORIFY_SECRET_BACKEND"] = "file"
os.environ["HISTORIFY_CALENDAR_AUTO_SYNC"] = "0"
from app.core.services import Services
from app.core.restore import restore
inst = "/tmp/eval/inst_p11b"
sv = Services(inst)
def fp(store):
    with store._reading() as cur:
        return cur.execute("SELECT instrument_id, interval_id, count(*), sum(hash(ts, open, high, low, close, volume))::HUGEINT "
                           "FROM ohlcv GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
before = fp(sv.store)
t0 = time.perf_counter(); bk = sv.maintenance.backup(keep=2); tb = time.perf_counter() - t0
sv.store.delete_series(1)                                          # damage: one instrument gone
sv.close()
t0 = time.perf_counter(); res = restore(inst, bk["backup"]); tr = time.perf_counter() - t0
sv2 = Services(inst); after = fp(sv2.store); sv2.close()
out = {"bars": bk["bars"], "backup_s": round(tb, 1), "backup_mb": round(bk["bytes"] / 1e6, 1), "restore_s": round(tr, 1),
       "series": len(before), "identical_series": sum(a == b for a, b in zip(before, after)), "exact": before == after}
print(json.dumps(out)); json.dump(out, open("/workspace/audit/m2_restore_scale.json", "w"))
