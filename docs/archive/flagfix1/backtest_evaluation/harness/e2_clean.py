import json, os, sys, time, statistics, glob
sys.path.insert(0, "/workspace/historify-master/historify"); sys.path.insert(0, "/tmp/eval")
os.environ["HISTORIFY_SECRET_BACKEND"] = "file"; os.environ["HISTORIFY_LOG_LEVEL"]="ERROR"
import numpy as np, pandas as pd, duckdb
for f in glob.glob("/tmp/eval/inst/**/*", recursive=True):
    if os.path.isfile(f): open(f, "rb").read()          # warm the page cache: measure software, not sandbox disk
from app.core.services import Services
from app.core.intervals import INTERVALS
res = {}
def T(name, fn, n=3):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter(); out = fn(); xs.append(time.perf_counter() - t0)
    res[name] = round(statistics.median(xs), 3); print(f"{name:62s} {res[name]:8.3f}s"); return out
sv = Services("/tmp/eval/inst"); ids = list(range(1, 51)); M1, D = 1, INTERVALS["D"].id
def batched(iv, cols="instrument_id,ts,open,high,low,close,volume"):
    with sv.store._reading() as cur:
        return cur.execute(f"SELECT {cols} FROM {sv.store._src} WHERE interval_id=? AND instrument_id BETWEEN 1 AND 50 "
                           f"ORDER BY instrument_id, ts", [iv]).fetchnumpy()
def to_wide(a, ids=np.arange(1, 51)):
    uts = np.unique(a["ts"]); r = np.searchsorted(uts, a["ts"]); c = np.searchsorted(ids, a["instrument_id"])
    out = {}
    for f in ("open", "high", "low", "close", "volume"):
        m = np.full((len(uts), len(ids)), np.nan); m[r, c] = a[f]; out[f] = m
    return uts, out
T("A 1m 50x2y  public BarStore.read_range() per series", lambda: [sv.store.read_range(i, M1) for i in ids])
a = T("B 1m 50x2y  one batched query -> numpy (in-process)", lambda: batched(M1))
T("B' 1m 50x2y batched query + numpy wide matrices (5 fields)", lambda: to_wide(batched(M1)))
T("B  D 50x10y batched query + wide", lambda: to_wide(batched(D)))
from app.core.rollup import session_opens
from datetime import date
opens = session_opens(sv.calendar(), ["NSE"], date(2023, 1, 1), date(2024, 12, 31))
T("C 5m 50x2y  store.resample_intraday (1m->5m) per series", lambda: [sv.store.resample_intraday(i, "5m", opens) for i in ids])
# HTTP API path, one series of 1m (paging limit 5000)
from app.web import create_v2_app
app = create_v2_app(config={"HISTORIFY_SERVICES": sv, "TESTING": True}, instance_path="/tmp/eval/inst")
c = app.test_client()
def http_one():
    n, to, pages = 0, None, 0
    while True:
        r = c.get(f"/api/v2/bars?instrument_id=1&interval=1m&limit=5000" + (f"&to={to}" if to else "")).get_json()
        n += len(r["bars"]["t"]); pages += 1
        if not r["has_more"]: return n, pages
        to = r["next_to"]
n_p = T("E 1m 1x2y   HTTP /api/v2/bars paging (limit 5000)", http_one, n=1)
res["E_http_pages"] = n_p[1]; res["E_http_bars"] = n_p[0]
print("   HTTP -> x50 symbols estimate:", round(res["E 1m 1x2y   HTTP /api/v2/bars paging (limit 5000)"] * 50, 1), "s", n_p)
sv.close()
folder = glob.glob("/tmp/eval/inst/snapshots/*")[0]
man = json.load(open(f"{folder}/manifest.json")); files = [f"{folder}/{s['file']}" for s in man["series"] if s["interval"] == "1m"]
con = duckdb.connect()
T("D 1m 50x2y  snapshot Parquet -> numpy (out of process, no lock)",
  lambda: [con.execute(f"SELECT ts,open,high,low,close,volume FROM read_parquet('{f}')").fetchnumpy() for f in files])
json.dump(res, open("/tmp/eval/e2_clean.json", "w"), indent=1)
