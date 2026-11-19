"""Metric: meta DB growth bound by purge_history (a year of Follow-style jobs: ~1,875 jobs/day x 250 days)."""
import json, os, sys, tempfile, time, uuid
sys.path.insert(0, "/workspace/historify-master/historify"); os.environ["HISTORIFY_SECRET_BACKEND"] = "file"
from datetime import timedelta, date
from app.core.services import Services
from app.core.meta.models import Job, JobTask, Instrument, utcnow
sv = Services(os.path.join(tempfile.mkdtemp(), "i"))
with sv.meta.session() as s:
    inst = Instrument(symbol="X", exchange="NSE", name="x"); s.add(inst); s.commit(); iid = inst.id
N = 250 * 1875
rows_j, rows_t = [], []
now = utcnow()
for k in range(N):
    when = now - timedelta(days=250 - k * 250 // N)
    jid = f"{k:08d}-" + "0" * 27
    rows_j.append(dict(id=jid, kind="download", origin="follow", status="succeeded", params_json="{}", created_at=when, finished_at=when))
    rows_t.append(dict(job_id=jid, instrument_id=iid, interval_id=1, range_start=date(2025, 1, 1), range_end=date(2025, 1, 1), status="succeeded"))
with sv.meta.engine.begin() as c:
    c.execute(Job.__table__.insert(), rows_j); c.execute(JobTask.__table__.insert(), rows_t)
size = lambda: sum(os.path.getsize(sv.meta.path + x) for x in ("", "-wal") if os.path.exists(sv.meta.path + x))
b_rows, b_mb = N, size() / 1e6
t0 = time.perf_counter(); res = sv.maintenance.step_purge_history(); dt = time.perf_counter() - t0
with sv.meta.session() as s:
    from sqlalchemy import func, select
    a_rows = s.scalar(select(func.count()).select_from(Job))
out = {"jobs_before": b_rows, "meta_mb_before": round(b_mb, 1), "jobs_after": a_rows, "meta_mb_after": round(size() / 1e6, 1),
       "purge_s": round(dt, 1), "vacuumed": res.get("vacuumed", False)}
print(json.dumps(out)); json.dump(out, open("/workspace/audit/m4_purge.json", "w"))
sv.close()
