"""A3: latency of the main UI/API endpoints on a realistic instance (9.9 M bars, 51 instruments, 101 series)."""
import json, os, sys, time, statistics
sys.path.insert(0, "/workspace/historify-master/historify"); os.environ.setdefault("HISTORIFY_SECRET_BACKEND", "file")
from app.core.services import Services
from app import create_app
sv = Services("/tmp/eval/inst_p11")
app = create_app({"HISTORIFY_SERVICES": sv, "TESTING": True}, instance_path=sv.instance_path)
c = app.test_client()
out = {}
for path in ["/api/v2/status", "/api/v2/summary", "/api/v2/coverage", "/api/v2/health", "/api/v2/series/1/1m?days=120",
             "/api/v2/bars?instrument_id=1&interval=1m&limit=5000", "/api/v2/bars?instrument_id=1&interval=5m&limit=1500",
             "/api/v2/storage", "/api/v2/snapshots", "/api/v2/jobs"]:
    xs = []
    for _ in range(3):
        t0 = time.perf_counter(); r = c.get(path); xs.append(time.perf_counter() - t0)
    out[path] = {"status": r.status_code, "median_ms": round(statistics.median(xs) * 1000)}
    print(f"{path:55s} {r.status_code} {out[path]['median_ms']:>7} ms")
json.dump(out, open("/workspace/audit/a3_endpoint_latency.json", "w"), indent=1)
sv.close()
