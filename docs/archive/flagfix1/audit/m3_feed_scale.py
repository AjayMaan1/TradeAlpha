"""Metric: live-bar feed for a 100-instrument paper universe (5m) against the rate-limited mock (Angel One profile)."""
import json, os, sys, tempfile, time
sys.path.insert(0, "/workspace/historify-master/historify"); os.environ["HISTORIFY_SECRET_BACKEND"] = "file"
os.environ["HISTORIFY_CALENDAR_AUTO_SYNC"] = "0"
from datetime import datetime
from tests.mock_provider.server import MockServer
from app.core.clock import FrozenClock
from app.core.services import Services
from app.core.meta.models import Instrument, Watchlist, WatchlistItem
NOW = "2025-03-27T10:06:10+05:30"
mock = MockServer().start(); mock.configure(now=NOW)
sv = Services(os.path.join(tempfile.mkdtemp(), "i"), clock=FrozenClock(datetime.fromisoformat(NOW)), engine_options={"poll_s": 0.05})
sv.settings.set("openalgo_api_host", mock.url); sv.settings.set("openalgo_api_key", "k"); sv.settings_changed(); sv.engine.start()
with sv.meta.session() as s:
    wl = Watchlist(name="paper"); s.add(wl); s.commit()
    for i in range(100):
        inst = Instrument(symbol=f"EQ{i:03d}", exchange="NSE", name="x"); s.add(inst); s.flush()
        s.add(WatchlistItem(watchlist_id=wl.id, instrument_id=inst.id, position=i))
    s.commit(); wl_id = wl.id
sv.feeds.create("paper", watchlist_id=wl_id, intervals=["5m"])
def run_bar(label):
    r0 = mock.stats["history_requests"]; t0 = time.perf_counter()
    jobs = sv.feeds.tick()
    while any(sv.jobs.job(j)["status"] not in ("succeeded", "partial", "failed") for j in jobs):
        time.sleep(0.1)
    dt = time.perf_counter() - t0
    return {"bar": label, "jobs": len(jobs), "tasks": sum(sv.jobs.job(j)["tasks"]["total"] for j in jobs),
            "requests": mock.stats["history_requests"] - r0, "fetch_s": round(dt, 1),
            "status": [sv.jobs.job(j)["status"] for j in jobs]}
out = [run_bar("first (backfill 30 d of 1m)")]
sv.clock.advance(minutes=5); mock.configure(now=sv.clock.now().isoformat())
out.append(run_bar("next bar (steady state)"))
for o in out: print(json.dumps(o))
json.dump(out, open("/workspace/audit/m3_feed_scale.json", "w"), indent=1)
sv.close(); mock.stop()
