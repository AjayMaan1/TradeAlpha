"""
Phase 3-4 measurements of the v2 application (download engine, scheduler, API v2, new UI) against the Angel One
mock broker (3/s, 180/min, 5000/h, max days per interval). Baselines for the same scenarios come from the
Phase 1-2 raw results (docs/metrics/raw/{before,after}.json) where a scenario existed there.

    cd historify && python ../benchmarks/phase34.py --out ../docs/metrics/raw/phase34.json

Runs entirely in a temporary instance directory; the frozen "now" is Fri 2025-03-28 16:00 IST (after the close).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = "2025-03-28T16:00:00+05:30"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    sys.path.insert(0, os.getcwd())
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    from tests.support.mock_provider.server import MockServer

    mock = MockServer().start()
    results: dict = {"python": sys.version.split()[0], "now": NOW}

    from tradealpha.apps.container import Services
    from tradealpha.platform.time.clock import FrozenClock

    def services(name, configure=True, now=NOW, **engine):
        mock.configure(now=now)
        opts = {"poll_s": 0.05, "backoff_base_s": 0.2, "auth_probe_s": 3600, "short_recheck_delay_s": 0.2}
        opts.update(engine)
        sv = Services(os.path.join(tmp, name), clock=FrozenClock(datetime.fromisoformat(now)), engine_options=opts)
        if configure:
            sv.settings.set("openalgo_api_host", mock.url)
            sv.settings.set("openalgo_api_key", "bench-key")
            sv.settings_changed()
        sv.engine.start()
        sv.runner.start()
        return sv

    def instruments(sv, symbols, exchange="NSE"):
        from tradealpha.platform.db.models import Instrument
        ids = []
        with sv.meta.session() as s:
            for sym in symbols:
                inst = Instrument(symbol=sym, exchange=exchange, name=sym)
                s.add(inst)
                s.flush()
                ids.append(inst.id)
            s.commit()
        return ids

    def wait(sv, job_id, timeout=900, until=("succeeded", "partial", "failed", "cancelled", "paused_auth")):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            j = sv.jobs.job(job_id)
            if j["status"] in until:
                return j
            time.sleep(0.05)
        raise RuntimeError(f"timeout waiting for {job_id}")

    def download(sv, ids, intervals, mode="fixed", start=None, end=None, wait_until=None):
        t0 = time.perf_counter()
        r = sv.jobs.enqueue_download(ids, intervals, mode=mode, start=start, end=end, origin="bench")
        j = wait(sv, r.job_id, until=wait_until) if wait_until else wait(sv, r.job_id)
        return j, round(time.perf_counter() - t0, 2)

    def run(name, fn):
        if args.only and name not in args.only.split(","):
            return
        mock.reset()
        t0 = time.perf_counter()
        try:
            results[name] = fn()
        except Exception as e:                                   # record and continue
            import traceback
            results[name] = {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]}
        results[name]["_wall_s"] = round(time.perf_counter() - t0, 2) if isinstance(results[name], dict) else None
        print(f"{name}: {json.dumps(results[name])[:300]}", flush=True)

    tmp = tempfile.mkdtemp(prefix="historify-p34-")

    # ------------------------------------------------------------------ A. download reliability
    def daily_20():
        sv = services("daily20")
        ids = instruments(sv, [f"SYM{i:02d}" for i in range(20)])
        j, secs = download(sv, ids, ["D"], start=date(2025, 3, 1), end=date(2025, 3, 28))
        out = {"succeeded": j["tasks"]["succeeded"], "total": j["tasks"]["total"], "broker_429": mock.stats["history_429"],
               "requests": j["requests"], "seconds": secs}
        sv.close()
        return out

    def chunked():
        sv = services("chunked")
        a, b, c = instruments(sv, ["SBIN", "TCS", "INFY"])
        out = {}
        for key, iid, iv, start in (("1m_90d", a, "1m", date(2024, 12, 29)), ("5m_150d", b, "5m", date(2024, 10, 30)),
                                    ("D_10y", c, "D", date(2015, 3, 30))):
            j, secs = download(sv, [iid], [iv], start=start, end=date(2025, 3, 28))
            out[key] = {"status": j["status"], "rows": j["rows_written"], "requests": j["tasks"]["total"], "seconds": secs}
        out["broker_429"] = mock.stats["history_429"]
        out["range_errors"] = mock.stats["history_range_err"]
        sv.close()
        return out

    def flaky():
        sv = services("flaky")
        mock.configure(error_rate=0.3, seed=7)
        ids = instruments(sv, [f"FLK{i}" for i in range(10)])
        j, secs = download(sv, ids, ["D"], start=date(2025, 3, 1), end=date(2025, 3, 28))
        out = {"succeeded": j["tasks"]["succeeded"], "total": j["tasks"]["total"], "retries": j["retries"],
               "broker_5xx": mock.stats["5xx"], "seconds": secs}
        sv.close()
        return out

    def auth_expired():
        sv = services("auth")
        mock.configure(session_valid=False)
        ids = instruments(sv, [f"AUT{i}" for i in range(5)])
        r = sv.jobs.enqueue_download(ids, ["D"], mode="fixed", start=date(2025, 3, 1), end=date(2025, 3, 28),
                                     origin="bench")
        j = wait(sv, r.job_id, until=("paused_auth", "failed"))
        calls = mock.stats["history_requests"]
        time.sleep(1.0)
        calls_while_paused = mock.stats["history_requests"] - calls
        mock.configure(session_valid=True)
        sv.engine.resume_auth()
        j2 = wait(sv, r.job_id)
        out = {"status_when_expired": j["status"], "broker_calls_before_pause": calls,
               "broker_calls_while_paused_1s": calls_while_paused, "status_after_resume": j2["status"],
               "succeeded_after_resume": j2["tasks"]["succeeded"]}
        sv.close()
        return out

    def truncated():
        sv = services("trunc")
        mock.configure(truncate_rate=1.0)
        (iid,) = instruments(sv, ["SBIN"])
        j, _ = download(sv, [iid], ["D", "5m"], start=date(2025, 1, 1), end=date(2025, 3, 28))
        tasks = sv.jobs.tasks(j["id"])
        from tradealpha.marketdata.bars import coverage as cov
        row = cov.coverage_rows(sv, [iid])[0]["series"]
        mock.configure(truncate_rate=0.0)
        j2, _ = download(sv, [iid], ["D", "5m"], mode="repair")
        j3, _ = download(sv, [iid], ["D", "5m"], mode="incremental")
        row2 = cov.coverage_rows(sv, [iid])[0]["series"]
        out = {"tasks_flagged_short_data": sum(1 for t in tasks if t["error_class"] == "short_data"),
               "status_after_truncation": {k: row[k]["status"] for k in ("D", "5m")},
               "status_after_repair_and_update": {k: row2[k]["status"] for k in ("D", "5m")},
               "repair_requests": j2["requests"] + j3["requests"]}
        sv.close()
        return out

    def backfill_1y_1m():
        sv = services("backfill")
        ids = instruments(sv, [f"BF{i}" for i in range(10)])
        t0 = time.perf_counter()
        r = sv.jobs.enqueue_download(ids, ["1m"], mode="fixed", start=date(2024, 3, 29), end=date(2025, 3, 28),
                                     origin="bench", priority="backfill")
        j = wait(sv, r.job_id, timeout=1800)
        secs = time.perf_counter() - t0
        out = {"status": j["status"], "requests": j["requests"], "rows": j["rows_written"], "seconds": round(secs, 1),
               "effective_req_per_s": round(j["requests"] / secs, 2), "broker_429": mock.stats["history_429"],
               "budget_req_per_s": sv.engine.rate_snapshot()["effective_rate_per_s"]}
        sv.close()
        return out

    # ------------------------------------------------------------------ B. performance
    def dl_1m_30d():
        times = []
        for n in range(3):
            sv = services(f"perf{n}")
            (iid,) = instruments(sv, ["SBIN"])
            j, secs = download(sv, [iid], ["1m"], start=date(2025, 2, 27), end=date(2025, 3, 28))
            times.append(secs)
            rows = j["rows_written"]
            sv.close()
        return {"rows": rows, "median_s": statistics.median(times), "all_s": times,
                "rows_per_s": round(rows / statistics.median(times))}

    def chart_api():
        sv = services("chart")
        (iid,) = instruments(sv, ["SBIN"])
        download(sv, [iid], ["1m"], start=date(2024, 12, 29), end=date(2025, 3, 28))
        from tradealpha.apps.research import create_app
        app = create_app({"HISTORIFY_SERVICES": sv, "TESTING": True}, instance_path=sv.instance_path)
        c = app.test_client()
        total = sv.store.series_stats(iid, 1).count
        lat, size = [], 0
        for _ in range(5):
            t0 = time.perf_counter()
            r = c.get(f"/api/v2/bars?instrument_id={iid}&interval=1m&limit=1500")
            lat.append(time.perf_counter() - t0)
            size = len(r.data)
        etag = r.headers["ETag"]
        t0 = time.perf_counter()
        r304 = c.get(f"/api/v2/bars?instrument_id={iid}&interval=1m&limit=1500", headers={"If-None-Match": etag})
        t304 = time.perf_counter() - t0
        # full history in pages of 5000 (what a user gets by scrolling all the way back)
        t0, pages, full_bytes, to = time.perf_counter(), 0, 0, None
        while True:
            r = c.get(f"/api/v2/bars?instrument_id={iid}&interval=1m&limit=5000" + (f"&to={to}" if to else ""))
            pages += 1
            full_bytes += len(r.data)
            b = r.get_json()
            if not b["has_more"]:
                break
            to = b["next_to"]
        full_s = time.perf_counter() - t0
        w0 = time.perf_counter()
        c.get(f"/api/v2/bars?instrument_id={iid}&interval=W")
        weekly = time.perf_counter() - w0
        out = {"series_bars": total, "first_paint_bars": 1500, "first_paint_ms_median": round(statistics.median(lat) * 1000, 1),
               "first_paint_kb": round(size / 1024, 1), "etag_304_status": r304.status_code,
               "etag_304_ms": round(t304 * 1000, 1), "etag_304_bytes": len(r304.data),
               "full_history_pages": pages, "full_history_s": round(full_s, 3), "full_history_kb": round(full_bytes / 1024, 1),
               "weekly_from_daily_ms": round(weekly * 1000, 1)}
        sv.close()
        return out

    def quotes_50():
        sv = services("quotes", now="2025-03-28T11:00:00+05:30")           # NSE session is live
        sv.quotes.start()
        ids = instruments(sv, [f"Q{i:02d}" for i in range(50)])
        t0 = time.perf_counter()
        q1 = sv.quotes.get(ids)
        first = time.perf_counter() - t0
        pending_first = sum(1 for q in q1 if q.get("pending"))
        while any(q.get("pending") for q in sv.quotes.get(ids)):
            time.sleep(0.25)
        all_loaded = time.perf_counter() - t0
        lat = []
        for _ in range(5):
            t1 = time.perf_counter()
            sv.quotes.get(ids)
            lat.append(time.perf_counter() - t1)
        time.sleep(7)                                                        # TTL (5 s) expired; warmer refreshes
        t1 = time.perf_counter()
        q3 = sv.quotes.get(ids)
        after_ttl = time.perf_counter() - t1
        out = {"first_call_s": round(first, 2), "pending_after_first_call": pending_first,
               "all_50_loaded_s": round(all_loaded, 2), "cached_call_ms_median": round(statistics.median(lat) * 1000, 2),
               "call_after_ttl_ms": round(after_ttl * 1000, 2), "max_quote_age_s": max(q.get("age_s", 0) for q in q3),
               "errors": sum(1 for q in q3 if q.get("error")), "broker_quote_calls": mock.stats["quotes_requests"]}
        sv.close()
        return out

    def export_800():
        import pandas as pd

        from tradealpha.marketdata.bars.base import BarStore, ExportSeries
        from tradealpha.platform.time.timeutil import ist_midnight_epoch
        sv = services("export")
        ids = instruments(sv, [f"E{i:03d}" for i in range(800)])
        days = [ist_midnight_epoch(d.date()) for d in pd.bdate_range("2024-04-01", "2025-03-28")]
        df = pd.DataFrame([(iid, 8, t, 100.0, 101.0, 99.0, 100.5, 1000) for iid in ids for t in days],
                          columns=["instrument_id", "interval_id", "ts", "open", "high", "low", "close", "volume"])
        df["oi"] = pd.array([None] * len(df), dtype="Int64")
        cur = sv.store._con.cursor()
        cur.register("_seed", df)
        cur.execute("INSERT INTO ohlcv SELECT * FROM _seed")
        cur.close()
        sel = [ExportSeries(i, "D", f"E{n:03d}", "NSE") for n, i in enumerate(ids)]
        t0 = time.perf_counter()
        BarStore.export_file(sv.store, os.path.join(tmp, "generic.csv"), "csv", sel)   # generic: one read per series
        generic = time.perf_counter() - t0
        t0 = time.perf_counter()
        n = sv.store.export_file(os.path.join(tmp, "batched.csv"), "csv", sel)        # v2: one streamed query
        batched = time.perf_counter() - t0
        from tradealpha.apps.research import create_app
        app = create_app({"HISTORIFY_SERVICES": sv, "TESTING": True}, instance_path=sv.instance_path)
        c = app.test_client()
        t0 = time.perf_counter()
        r = c.post("/api/v2/jobs/export", json={"instrument_ids": ids, "intervals": ["D", "W"], "format": "csv_zip"})
        j = wait(sv, r.get_json()["job_id"])
        job_s = time.perf_counter() - t0
        out = {"series": 800, "rows": n, "per_series_reads_s": round(generic, 2), "single_query_s": round(batched, 2),
               "speedup": round(generic / batched, 1), "api_job_csv_zip_D_plus_W_s": round(job_s, 2),
               "api_job_rows": j["rows_written"], "set_cookie_header": "Set-Cookie" in r.headers}
        sv.close()
        return out

    def repair_planning():
        import pandas as pd

        from tradealpha.marketdata.bars.base import BarStore
        from tradealpha.platform.time.timeutil import ist_midnight_epoch
        sv = services("repair")
        ids = instruments(sv, [f"R{i:03d}" for i in range(400)])
        days = [ist_midnight_epoch(d.date()) for d in pd.bdate_range("2025-01-01", "2025-03-28")]
        df = pd.DataFrame([(iid, 8, t, 1.0, 1.0, 1.0, 1.0, 1) for iid in ids for t in days],
                          columns=["instrument_id", "interval_id", "ts", "open", "high", "low", "close", "volume"])
        df["oi"] = pd.array([None] * len(df), dtype="Int64")
        cur = sv.store._con.cursor()
        cur.register("_seed", df)
        cur.execute("INSERT INTO ohlcv SELECT * FROM _seed")
        cur.close()
        pairs = [(i, 8) for i in ids]
        t0 = time.perf_counter()
        BarStore.daily_counts_many(sv.store, pairs)
        generic = time.perf_counter() - t0
        t0 = time.perf_counter()
        sv.store.daily_counts_many(pairs)
        batched = time.perf_counter() - t0
        return {"series": 400, "per_series_queries_s": round(generic, 2), "single_query_s": round(batched, 3),
                "speedup": round(generic / batched, 1)}

    # ------------------------------------------------------------------ C. scheduler
    def scheduler():
        from tradealpha.marketdata.ingestion import triggers as T
        sv = services("sched", now="2025-03-28T10:00:00+05:30")
        ids = instruments(sv, ["SBIN", "TCS"])
        from tradealpha.platform.db.models import Watchlist, WatchlistItem
        with sv.meta.session() as s:
            wl = Watchlist(name="Bench")
            s.add(wl)
            s.flush()
            for n, iid in enumerate(ids):
                s.add(WatchlistItem(watchlist_id=wl.id, instrument_id=iid, position=n))
            s.commit()
            wid = wl.id
        sched = sv.scheduler.create({"name": "Post-close", "trigger": {"type": "market_event", "event": "after_close",
                                                                       "exchange": "NSE", "offset_min": 15},
                                     "target": {"watchlist_id": wid}, "intervals": ["D"]})
        pv = T.preview(sched["trigger"], sv.calendar(), sv.clock.now(), n=5)
        holiday = sv.scheduler.fire(sched["id"], datetime.fromisoformat("2025-03-31T15:45:00+05:30"))
        download(sv, ids, ["D"], start=date(2025, 3, 3), end=date(2025, 3, 27))
        sv.clock.set(datetime.fromisoformat("2025-03-28T15:45:00+05:30"))
        mock.configure(now="2025-03-28T15:45:00+05:30")
        run = sv.scheduler.fire(sched["id"], datetime.fromisoformat("2025-03-28T15:45:00+05:30"))
        j = wait(sv, run["job_id"])
        last_day = sv.store.daily_counts(ids[0], 8)["trade_date"].max().isoformat()
        return {"next_runs": pv["next"], "skipped_shown": pv["skipped"], "holiday_fire": holiday["status"],
                "post_close_1545_status": j["status"], "post_close_latest_daily_bar": last_day,
                "post_close_requests": j["requests"]}

    # ------------------------------------------------------------------ D. UI
    def ui():
        sv = services("ui")
        from tradealpha.apps.research import create_app
        app = create_app({"HISTORIFY_SERVICES": sv, "TESTING": True}, instance_path=sv.instance_path)
        c = app.test_client()
        pages = {}
        root = os.path.join(os.getcwd(), "app")
        for path in ("/", "/watchlists", "/charts", "/data", "/jobs", "/import", "/export", "/scheduler", "/settings"):
            r = c.get(path)
            html = r.get_data(as_text=True)
            assets = re.findall(r'(?:src|href)="(/static/[^"]+)"', html)
            size = len(r.data) + sum(os.path.getsize(os.path.join(root, a.split("?")[0].lstrip("/"))) for a in assets)
            pages[path] = {"status": r.status_code, "requests": 1 + len(assets), "kb_uncompressed": round(size / 1024),
                           "external_resources": len(re.findall(r'<(?:script|img|iframe)[^>]+src="https?://|<link[^>]+href="https?://', html)),
                           "csp": "script-src 'self'" in r.headers.get("Content-Security-Policy", "")}
        tpl2 = os.path.join(root, "templates_v2")
        v2_src = "".join(open(os.path.join(tpl2, f)).read() for f in os.listdir(tpl2))
        js2 = os.path.join(root, "static", "js", "v2")
        js_src = "".join(open(os.path.join(d, f)).read() for d, _, fs in os.walk(js2) for f in fs)
        tpl1 = os.path.join(root, "templates")
        v1_src = "".join(open(os.path.join(tpl1, f)).read() for f in os.listdir(tpl1) if f.endswith(".html"))
        js1 = os.path.join(root, "static", "js")
        js1_src = "".join(open(os.path.join(js1, f)).read() for f in os.listdir(js1) if f.endswith(".js"))
        handlers = r"\son(click|change|input|submit|load|keyup|keydown)="
        out = {"pages": pages,
               "v1": {"inline_handlers": len(re.findall(handlers, v1_src)), "innerHTML_uses": js1_src.count("innerHTML"),
                      "external_cdn_urls": len(set(re.findall(r'(?:src|href)="(https://[^"]+)"', v1_src)))},
               "v2": {"inline_handlers": len(re.findall(handlers, v2_src)), "innerHTML_uses": js_src.count("innerHTML"),
                      "external_cdn_urls": len(set(re.findall(r'(?:src|href)="(https://[^"]+)"', v2_src)) -
                                               {"https://www.tradingview.com/"}),
                      "style_attributes": v2_src.count(" style=")}}
        sv.close()
        return out

    # ------------------------------------------------------------------ E. storage
    def storage():
        sv = services("storage")
        (iid,) = instruments(sv, ["SBIN"])
        download(sv, [iid], ["1m"], start=date(2024, 12, 29), end=date(2025, 3, 28))
        sv.store.maintenance()
        n = sv.store.total_rows()
        sv.close()
        size = os.path.getsize(os.path.join(tmp, "storage", "bars.duckdb"))
        return {"bars": n, "file_bytes": size, "bytes_per_bar": round(size / n, 1)}

    # ------------------------------------------------------------------ G3: kill -9 mid-job, API latency under load
    child_code = """
import sys, os, time, json
sys.path.insert(0, os.getcwd())
from datetime import date, datetime
from tradealpha.platform.time.clock import FrozenClock
from tradealpha.apps.container import Services
inst, mock_url, mode, now = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
sv = Services(inst, clock=FrozenClock(datetime.fromisoformat(now)), engine_options={'poll_s': 0.05})
sv.settings.set('openalgo_api_host', mock_url); sv.settings.set('openalgo_api_key', 'k')
sv.settings.set('provider_profile', 'generic'); sv.settings.set('rate_budget_share', 1.0, 'float'); sv.settings_changed()
if mode in ('enqueue', 'full'):
    from tradealpha.platform.db.models import Instrument
    with sv.meta.session() as s:
        ids = []
        for i in range(200):
            x = Instrument(symbol=f'K{i:03d}', exchange='NSE', name='x'); s.add(x); s.flush(); ids.append(x.id)
        s.commit()
    job = sv.jobs.enqueue_download(ids, ['D', '1h'], mode='fixed', start=date(2025, 1, 1), end=date(2025, 3, 28)).job_id
    open(os.path.join(inst, 'job_id'), 'w').write(job)
job = open(os.path.join(inst, 'job_id')).read()
sv.engine.start()
print('started', flush=True)
while sv.jobs.job(job)['status'] not in ('succeeded', 'partial', 'failed', 'cancelled'):
    time.sleep(0.1)
print(json.dumps(sv.jobs.job(job)['tasks']), flush=True)
sv.close()
"""

    def kill9_restart():
        import signal
        import subprocess

        from tradealpha.marketdata.bars import open_bar_store
        mock.configure(history_limits=[[10, 1.0], [600, 60.0], [36000, 3600.0]])
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        a, ref = os.path.join(tmp, "kill9"), os.path.join(tmp, "kill9_ref")
        p1 = subprocess.Popen([sys.executable, "-c", child_code, a, mock.url, "enqueue", NOW], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        p1.stdout.readline()
        time.sleep(12)
        os.kill(p1.pid, signal.SIGKILL)
        p1.wait()
        from tradealpha.platform.db.meta import MetaDB
        from tradealpha.platform.db.models import JobTask
        meta = MetaDB(os.path.join(a, "historify_meta.db"))
        with meta.session() as s:
            done_before = s.query(JobTask).filter(JobTask.status.in_(["succeeded", "empty"])).count()
            running_at_kill = s.query(JobTask).filter(JobTask.status == "running").count()
            total = s.query(JobTask).count()
        meta.dispose()
        t0 = time.perf_counter()
        out2 = subprocess.run([sys.executable, "-c", child_code, a, mock.url, "resume", NOW], env=env, capture_output=True,
                              text=True, timeout=900)
        resume_s = time.perf_counter() - t0
        out3 = subprocess.run([sys.executable, "-c", child_code, ref, mock.url, "full", NOW], env=env, capture_output=True,
                              text=True, timeout=900)
        sums = {}
        for name, path in (("restarted", a), ("uninterrupted", ref)):
            st = open_bar_store(os.path.join(path, "bars.duckdb"), "duckdb", read_only=True)
            cov = st.coverage()
            sums[name] = {(int(r.instrument_id), int(r.interval_id)): st.checksum(int(r.instrument_id), int(r.interval_id))
                          for r in cov.itertuples()}
            st.close()
        return {"tasks": total, "done_before_kill": done_before, "running_at_kill": running_at_kill,
                "restart_result": out2.stdout.strip().splitlines()[-1] if out2.stdout else out2.stderr[-500:],
                "reference_result": out3.stdout.strip().splitlines()[-1] if out3.stdout else out3.stderr[-500:],
                "series_restarted": len(sums["restarted"]), "series_reference": len(sums["uninterrupted"]),
                "identical_checksums": sums["restarted"] == sums["uninterrupted"], "resume_s": round(resume_s, 1)}

    def latency_under_load():
        from tradealpha.apps.research import create_app
        mock.configure(history_limits=[[10, 1.0], [600, 60.0], [36000, 3600.0]])
        sv = services("load")
        sv.settings.set("provider_profile", "generic")
        sv.settings.set("rate_budget_share", 1.0, "float")
        sv.settings_changed()
        ids = instruments(sv, [f"L{i:03d}" for i in range(500)])
        from tradealpha.platform.db.models import Watchlist, WatchlistItem
        with sv.meta.session() as s:
            wl = Watchlist(name="Load")
            s.add(wl)
            s.flush()
            for n, iid in enumerate(ids[:50]):
                s.add(WatchlistItem(watchlist_id=wl.id, instrument_id=iid, position=n))
            s.commit()
        app = create_app({"HISTORIFY_SERVICES": sv, "TESTING": True}, instance_path=sv.instance_path)
        c = app.test_client()
        job = sv.jobs.enqueue_download(ids, ["D"], mode="fixed", start=date(2024, 1, 1), end=date(2025, 3, 28)).job_id
        endpoints = ["/api/v2/status", f"/api/v2/jobs/{job}", f"/api/v2/jobs/{job}/tasks?limit=100", "/api/v2/jobs?active=1",
                     "/api/v2/watchlists/1/items", "/api/v2/coverage", "/api/v2/summary", "/api/v2/schedules",
                     f"/api/v2/bars?instrument_id={ids[0]}&interval=D&limit=1500"]
        lat = {e.split("?")[0].replace(job, "{id}").replace("/api/v2", ""): [] for e in endpoints}
        errors, locked = 0, 0
        while sv.jobs.job(job)["status"] in ("queued", "running"):
            for e in endpoints:
                t0 = time.perf_counter()
                r = c.get(e)
                key = e.split("?")[0].replace(job, "{id}").replace("/api/v2", "")
                lat[key].append(time.perf_counter() - t0)
                if r.status_code >= 500:
                    errors += 1
                    locked += "locked" in r.get_data(as_text=True)
        j = sv.jobs.job(job)
        log = open(os.path.join(sv.instance_path, "logs", "historify.log")).read() if os.path.exists(
            os.path.join(sv.instance_path, "logs", "historify.log")) else ""
        allv = sorted(x for v in lat.values() for x in v)
        p95 = lambda v: round(sorted(v)[int(0.95 * (len(v) - 1))] * 1000, 1)   # noqa: E731
        out = {"job_tasks": j["tasks"]["total"], "job_status": j["status"], "requests_sampled": len(allv),
               "p95_ms_all": p95(allv), "p95_ms_by_endpoint": {k: p95(v) for k, v in lat.items() if v},
               "http_5xx": errors, "database_locked": locked + log.count("database is locked")}
        sv.close()
        return out

    def coverage_after_truncation():
        from tradealpha.marketdata.bars import coverage as cov
        sv = services("s2")
        ids = instruments(sv, [f"S2_{i}" for i in range(10)])
        mock.configure(truncate_rate=0.1, seed=11)
        j1, _ = download(sv, ids, ["D", "5m"], start=date(2025, 1, 1), end=date(2025, 3, 28))
        before = cov.summary(sv)
        mock.configure(truncate_rate=0.0)
        j2, _ = download(sv, ids, ["D", "5m"], mode="repair")
        j3, _ = download(sv, ids, ["D", "5m"], mode="incremental")
        after = cov.summary(sv)
        rows = cov.coverage_rows(sv, ids)
        statuses = [c["status"] for r in rows for c in r["series"].values() if c["status"] != "none"]
        out = {"series": len(statuses), "truncated_responses": mock.stats["truncated"],
               "after_backfill": {k: before.get(k) for k in ("gaps", "stale", "coverage_pct")},
               "after_repair_incremental": {k: after.get(k) for k in ("gaps", "stale", "coverage_pct")},
               "all_ok": all(x == "ok" for x in statuses), "repair_requests": j2["requests"] + j3["requests"]}
        sv.close()
        return out

    def chart_1y_1m():
        from tradealpha.apps.research import create_app
        sv = services("chart1y")
        (iid,) = instruments(sv, ["SBIN"])
        download(sv, [iid], ["1m"], start=date(2024, 3, 29), end=date(2025, 3, 28))
        app = create_app({"HISTORIFY_SERVICES": sv, "TESTING": True}, instance_path=sv.instance_path)
        c = app.test_client()
        lat = []
        for _ in range(7):
            t0 = time.perf_counter()
            r = c.get(f"/api/v2/bars?instrument_id={iid}&interval=1m&limit=1500")
            lat.append(time.perf_counter() - t0)
        gz = None
        import gzip
        gz = len(gzip.compress(r.data))
        b = r.get_json()
        t0 = time.perf_counter()
        older = c.get(f"/api/v2/bars?instrument_id={iid}&interval=1m&limit=1500&to={b['next_to']}")
        older_s = time.perf_counter() - t0
        out = {"series_bars": sv.store.series_stats(iid, 1).count, "first_page_bars": len(b["bars"]["t"]),
               "first_page_kb": round(len(r.data) / 1024, 1), "first_page_kb_gzip": round(gz / 1024, 1),
               "api_ms_median": round(statistics.median(lat) * 1000, 1), "api_ms_max": round(max(lat) * 1000, 1),
               "older_page_ms": round(older_s * 1000, 1), "older_page_bars": len(older.get_json()["bars"]["t"]),
               "has_more": b["has_more"]}
        sv.close()
        return out

    for name, fn in (("kill9_restart", kill9_restart), ("latency_under_load", latency_under_load),
                     ("coverage_after_truncation", coverage_after_truncation), ("chart_1y_1m", chart_1y_1m),
                     ("daily_20", daily_20), ("chunked", chunked), ("flaky", flaky), ("auth_expired", auth_expired),
                     ("truncated", truncated), ("dl_1m_30d", dl_1m_30d), ("chart_api", chart_api),
                     ("quotes_50", quotes_50), ("export_800", export_800), ("repair_planning", repair_planning),
                     ("scheduler", scheduler), ("ui", ui), ("storage", storage), ("backfill_1y_1m", backfill_1y_1m)):
        run(name, fn)
    mock.stop()
    with open(args.out, "w") as f:
        json.dump(results, f, indent=2, default=str)


if __name__ == "__main__":
    main()
