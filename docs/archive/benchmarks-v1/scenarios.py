"""
Scenario runner for the before/after comparison. Runs INSIDE one code tree (before = baseline commit,
after = working tree) so both versions are exercised with identical inputs:

    python benchmarks/scenarios.py --app-dir <copy of historify/> --mock-dir <historify/ with tests/mock_provider> --out x.json

Every scenario uses the Angel One mock (3/s, 180/min, 5000/h, max-days per interval) unless stated otherwise.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import random
import string
import sys
import threading
import time
import traceback


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-dir", required=True)
    ap.add_argument("--mock-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    sys.path.insert(0, args.mock_dir)
    from tests.mock_provider.server import MockServer  # noqa: E402  (mock has no dependency on `app`)

    mock = MockServer().start()
    sys.path.insert(0, args.app_dir)
    os.chdir(args.app_dir)
    results: dict = {"version": None}

    import app as app_pkg  # noqa: E402
    from app import create_app  # noqa: E402
    results["version"] = "after" if hasattr(app_pkg, "start_background_services") else "before"
    t0 = time.perf_counter()
    app = create_app()
    results["create_app_s"] = round(time.perf_counter() - t0, 3)
    from app.utils.scheduler import scheduler_manager
    results["scheduler_started_by_create_app"] = bool(scheduler_manager.scheduler.running)
    scheduler_manager.shutdown() if scheduler_manager.scheduler.running else None
    results["root_log_level_after_create_app"] = logging.getLogger().getEffectiveLevel()
    from sqlalchemy import inspect, text

    from app.models import db

    c = app.test_client()

    def run(name, fn):
        if args.only and name not in args.only.split(","):
            return
        mock.reset()
        try:
            t = time.perf_counter()
            out = fn()
            out["_elapsed_s"] = round(time.perf_counter() - t, 2)
            results[name] = out
        except Exception as e:                               # record, never abort the whole run
            results[name] = {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-800:]}
        print(f"[{results['version']}] {name}: {json.dumps(results[name], default=str)[:220]}", flush=True)

    def rows(table):
        with app.app_context():
            if not inspect(db.engine).has_table(table):
                return 0
            return db.session.execute(text(f'SELECT count(*) FROM "{table}"')).scalar()

    def dl(**kw):
        t = time.perf_counter()
        body = c.post("/api/download", json=kw).get_json()
        return body, round(time.perf_counter() - t, 2)

    # ------------------------------------------------------------- bootstrap (before configuring the API)
    def s_bootstrap():
        from app.models.settings import AppSettings
        with app.app_context():
            n = AppSettings.query.count()
        r = c.get("/api/symbols")
        return {"default_settings_rows": n, "unconfigured_api_status": r.status_code,
                "unconfigured_api_is_json": r.is_json}
    run("bootstrap", s_bootstrap)

    assert c.post("/api/settings", json={"openalgo_api_key": "bench-key-1234",
                                         "openalgo_api_host": mock.url}).status_code == 200

    def s_settings():
        body = c.get("/api/settings").get_json()
        opt = c.post("/api/settings/optimize-database").get_json()
        return {"api_key_returned_in_plaintext": body.get("openalgo_api_key") == "bench-key-1234",
                "optimize_db_success": bool(opt.get("success")), "optimize_msg": opt.get("message", "")[:80]}
    run("settings", s_settings)

    # ------------------------------------------------------------- reliability under Angel One limits
    def s_multi_symbol():
        syms = [f"MS{i:02d}" for i in range(20)]
        body, el = dl(symbols=syms, exchanges=["NSE"] * 20, interval="D", start_date="2025-03-03", end_date="2025-03-28")
        return {"symbols": 20, "succeeded": len(body["success"]), "failed": len(body["failed"]),
                "upstream_429": mock.stats["history_429"], "elapsed_s": el}
    run("multi_symbol_daily", s_multi_symbol)

    for name, iv, s, e, table in (("range_1m_90d", "1m", "2025-01-01", "2025-03-31", "data_rng1_nse_1m"),
                                  ("range_5m_150d", "5m", "2024-11-01", "2025-03-31", "data_rng5_nse_5m"),
                                  ("range_d_10y", "D", "2015-04-01", "2025-03-31", "data_rngd_nse_d")):
        sym = table.split("_")[1].upper()

        def s_range(iv=iv, s=s, e=e, table=table, sym=sym):
            body, el = dl(symbols=[sym], exchanges=["NSE"], interval=iv, start_date=s, end_date=e)
            return {"succeeded": len(body["success"]) == 1, "rows_stored": rows(table),
                    "error": (body["failed"][0]["error"][:100] if body["failed"] else None), "elapsed_s": el}
        run(name, s_range)

    def s_weekend():
        body, _ = dl(symbols=["WKND"], exchanges=["NSE"], interval="D", start_date="2025-03-15", end_date="2025-03-16")
        return {"reported_failure": bool(body["failed"])}
    run("weekend_range", s_weekend)

    def s_weekly():
        body, _ = dl(symbols=["WEEK"], exchanges=["NSE"], interval="W", start_date="2025-01-01", end_date="2025-03-31")
        return {"reported_success": bool(body["success"]), "rows_in_weekly_table": rows("data_week_nse_w"),
                "expected_weekly_bars": 13, "note": "rows are DAILY bars if > expected"}
    run("weekly_interval", s_weekly)

    def s_slow():
        mock.configure(latency_by_symbol={"SLOWCO": 6.5})
        body, el = dl(symbols=["SLOWCO"], exchanges=["NSE"], interval="D", start_date="2025-03-03", end_date="2025-03-07")
        return {"succeeded": bool(body["success"]), "elapsed_s": el,
                "error": (body["failed"][0]["error"][-60:] if body["failed"] else None)}
    run("slow_upstream_6_5s", s_slow)

    def s_transient():
        mock.configure(error_rate=0.3, seed=11, history_limits=[[1000, 1.0]])
        syms = [f"TR{i}" for i in range(10)]
        body, el = dl(symbols=syms, exchanges=["NSE"] * 10, interval="D", start_date="2025-03-03", end_date="2025-03-07")
        return {"symbols": 10, "succeeded": len(body["success"]), "upstream_5xx": mock.stats["5xx"], "elapsed_s": el}
    run("transient_5xx_30pct", s_transient)

    def s_session():
        mock.configure(session_valid=False)
        syms = [f"SE{i}" for i in range(5)]
        body, el = dl(symbols=syms, exchanges=["NSE"] * 5, interval="D", start_date="2025-03-03", end_date="2025-03-07")
        out = {"failed": len(body["failed"]), "upstream_calls_while_expired": mock.stats["auth_err"],
               "message": body["failed"][0]["error"][:110] if body["failed"] else None}
        # user re-logs in to OpenAlgo, then clicks "Test connection" in Settings
        mock.configure(session_valid=True)
        test = c.post("/api/settings/test-api").get_json()
        body2, _ = dl(symbols=["SE9"], exchanges=["NSE"], interval="D", start_date="2025-03-03", end_date="2025-03-07")
        out["recovered_after_relogin_and_test_connection"] = bool(test.get("success")) and bool(body2["success"])
        return out
    run("session_expired", s_session)

    # ------------------------------------------------------------- performance
    def s_write():
        import statistics
        firsts, redos = [], []
        for trial in range(3):                               # median of 3 (fresh symbol per trial)
            sym = "WRT" if trial == 0 else f"WRT{trial}"
            kw = dict(symbols=[sym], exchanges=["NSE"], interval="1m", start_date="2025-02-03", end_date="2025-03-03")
            firsts.append(dl(**kw)[1])
            redos.append(dl(**kw)[1])
        n = rows("data_wrt_nse_1m")
        el1, el2 = statistics.median(firsts), statistics.median(redos)
        mock.reset()
        t = time.perf_counter()
        import httpx
        httpx.post(f"{mock.url}/api/v1/history", json={"apikey": "k", "symbol": "WRT", "exchange": "NSE", "interval": "1m",
                                                       "start_date": "2025-02-03", "end_date": "2025-03-03"}, timeout=60)
        fetch = time.perf_counter() - t
        return {"rows": n, "first_download_s": el1, "redownload_s": el2, "upstream_fetch_s": round(fetch, 3),
                "rows_per_s_end_to_end": round(n / el1) if el1 else None,
                "db_write_share_pct": round(100 * (el1 - fetch) / el1, 1) if el1 else None}
    run("write_path_1m_30d", s_write)

    def s_chart():
        import statistics
        lat = []
        for _ in range(7):                                   # median of 7
            t = time.perf_counter()
            r = c.get("/charts/api/chart-data/WRT/NSE/1m/20/14")
            lat.append(time.perf_counter() - t)
        return {"latency_s": round(statistics.median(lat), 3), "latency_min_s": round(min(lat), 3),
                "payload_kb": len(r.data) // 1024, "candles": len(r.get_json().get("candlestick", []))}
    run("chart_full_history", s_chart)

    def s_quotes():
        syms = ",".join(f"Q{i}" for i in range(50))
        t = time.perf_counter()
        body = c.get(f"/api/quotes?symbols={syms}&exchanges={','.join(['NSE'] * 50)}").get_json()
        return {"latency_s": round(time.perf_counter() - t, 2), "ok": sum(1 for q in body if "error" not in q),
                "errors": sum(1 for q in body if "error" in q)}
    run("quotes_50", s_quotes)

    def s_concurrent():
        # Mock range caps relaxed so that BOTH versions perform a large write (isolates DB locking behaviour).
        mock.configure(max_days={k: 100000 for k in ("1m", "3m", "5m", "10m", "15m", "30m", "1h", "D")},
                       history_limits=[[1000, 1.0]])
        result = {}

        def big():
            result["body"], result["el"] = dl(symbols=["BIGW"], exchanges=["NSE"], interval="1m",
                                              start_date="2024-10-01", end_date="2025-03-31")
        th = threading.Thread(target=big)
        th.start()
        time.sleep(1.0)
        statuses, lat, i = [], [], 0
        while th.is_alive() and i < 200:
            t = time.perf_counter()
            r = app.test_client().post("/watchlist/items", json={"symbol": f"CW{i}", "exchange": "NSE"})
            lat.append(time.perf_counter() - t)
            statuses.append(r.status_code)
            i += 1
            time.sleep(0.25)
        th.join()
        return {"download_rows": rows("data_bigw_nse_1m"), "download_s": result.get("el"),
                "concurrent_writes": len(statuses), "concurrent_write_failures": sum(1 for s in statuses if s != 201),
                "max_write_latency_s": round(max(lat), 2) if lat else None}
    run("concurrent_write", s_concurrent)

    # ------------------------------------------------------------- features / UI plumbing
    def s_excel():
        import openpyxl
        wb = openpyxl.Workbook()
        for row in (["Symbol", "Exchange"], ["HDFCBANK", "NSE"], ["TATAPOWER", "BSE"]):
            wb.active.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        body = c.post("/api/parse-excel", data={"file": (buf, "mine.xlsx")}, content_type="multipart/form-data").get_json()
        return {"returns_uploaded_rows": body.get("rows") == [["Symbol", "Exchange"], ["HDFCBANK", "NSE"], ["TATAPOWER", "BSE"]],
                "first_data_row": (body.get("rows") or [[None]])[1:2]}
    run("excel_import", s_excel)

    def s_pages():
        pages = ["/", "/watchlist/", "/download", "/import", "/export", "/charts/", "/scheduler", "/settings", "/dashboard/"]
        codes = {p: c.get(p).status_code for p in pages}
        wm = sum(('value="W"' in c.get(p).get_data(as_text=True)) for p in ("/download", "/scheduler", "/export", "/charts/"))
        return {"pages_ok": sum(v == 200 for v in codes.values()), "pages_total": len(pages),
                "profile_status": c.get("/profile/").status_code, "pages_offering_weekly": wm,
                "watchlist_nav_highlighted": 'href="/watchlist" class="nav-link active"' in c.get("/watchlist/").get_data(as_text=True)}
    run("pages", s_pages)

    def s_export_cookie():
        random.seed(1)
        syms = [{"symbol": "".join(random.choices(string.ascii_uppercase, k=random.randint(3, 12))),
                 "exchange": random.choice(["NSE", "BSE", "NFO"])} for _ in range(800)]
        r = c.post("/api/export", json={"symbols": syms, "interval": "D", "format": "combined"})
        return {"set_cookie_bytes": len(r.headers.get("Set-Cookie", "")), "browser_limit": 4096}
    run("export_cookie_800_symbols", s_export_cookie)

    # ------------------------------------------------------------- storage engine (v1 dynamic table vs v2 BarStore)
    def s_storage():
        import numpy as np
        import pandas as pd
        n_series, bars_per = 10, 93_750
        base = int(pd.Timestamp("2024-01-01 03:45", tz="UTC").timestamp())
        ts = base + np.arange(bars_per, dtype="int64") * 60
        out = {"rows": n_series * bars_per, "data": "random-walk prices (2 dp) + random volumes, identical for both versions"}

        def realistic(k):
            rng = np.random.default_rng(1000 + k)
            close = np.round(500 + np.cumsum(rng.normal(0, 0.4, bars_per)), 2)
            open_ = np.round(close + rng.normal(0, 0.2, bars_per), 2)
            high = np.round(np.maximum(open_, close) + np.abs(rng.normal(0, 0.3, bars_per)), 2)
            low = np.round(np.minimum(open_, close) - np.abs(rng.normal(0, 0.3, bars_per)), 2)
            vol = rng.integers(100, 200_000, bars_per).astype("int64")
            return open_, high, low, close, vol
        if results["version"] == "before":
            from app.models.dynamic_tables import ensure_table_exists, get_data_by_timeframe
            db_path = None
            with app.app_context():
                db_path = db.engine.url.database
                size0 = os.path.getsize(db_path)
                # v1 per-row ORM write rate (what /api/download did), measured on 10k rows then extrapolated
                model = ensure_table_exists("ORMRATE", "NSE", "1m")
                stamps = pd.to_datetime(ts[:10_000], unit="s", utc=True).tz_convert("Asia/Kolkata")
                t = time.perf_counter()
                for st in stamps:
                    ex = model.query.filter_by(date=st.date(), time=st.time()).first()
                    if ex is None:
                        db.session.add(model(date=st.date(), time=st.time(), open=1, high=2, low=0.5, close=1.5, volume=1))
                db.session.commit()
                out["v1_orm_rows_per_s"] = round(10_000 / (time.perf_counter() - t))
                # footprint: load all rows into v1 dynamic tables with a fast raw executemany
                stamps = pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Kolkata")
                dates = stamps.strftime("%Y-%m-%d").tolist()
                times = stamps.strftime("%H:%M:%S.000000").tolist()
                raw = db.engine.raw_connection()
                for k in range(n_series):
                    m = ensure_table_exists(f"STOR{k}", "NSE", "1m")
                    cur = raw.cursor()
                    cur.executemany(f'INSERT INTO "{m.__tablename__}" (date, time, open, high, low, close, volume, created_at) '
                                    "VALUES (?,?,?,?,?,?,?,'2025-01-01 00:00:00.000000')",
                                    [(d, tt, float(o), float(h), float(lo), float(cl), int(v)) for d, tt, o, h, lo, cl, v
                                     in zip(dates, times, *realistic(k), strict=True)])
                    raw.commit()
                raw.close()
                out["v1_bytes_per_row"] = round((os.path.getsize(db_path) - size0) / (n_series * bars_per), 1)
                t = time.perf_counter()
                data = get_data_by_timeframe("STOR3", "NSE", "1m", pd.Timestamp(dates[20_000]).date(),
                                             pd.Timestamp(dates[60_000]).date())
                out["range_read_s"] = round(time.perf_counter() - t, 3)
                out["range_read_rows"] = len(data)
                out["tables_for_data"] = n_series + 1
        else:
            import tempfile

            from app.core.store import open_bar_store
            d = tempfile.mkdtemp()
            for engine in ("duckdb", "sqlite"):
                store = open_bar_store(os.path.join(d, f"bars.{engine}"), engine)
                frames = []
                for k in range(n_series):
                    o, h, lo_, cl, v = realistic(k)
                    frames.append(pd.DataFrame({"ts": ts, "open": o, "high": h, "low": lo_, "close": cl, "volume": v}))
                t = time.perf_counter()
                for k, f in enumerate(frames):
                    store.replace_range(k + 1, 1, int(ts[0]), int(ts[-1]), f)
                el = time.perf_counter() - t
                out[f"{engine}_rows_per_s"] = round(n_series * bars_per / el)
                if engine == "duckdb":
                    store.maintenance()
                size = os.path.getsize(os.path.join(d, f"bars.{engine}"))
                wal = os.path.join(d, f"bars.{engine}-wal")
                size += os.path.getsize(wal) if os.path.exists(wal) else 0
                out[f"{engine}_bytes_per_row"] = round(size / (n_series * bars_per), 1)
                lo = int(pd.Timestamp(pd.to_datetime(ts[20_000], unit="s", utc=True).tz_convert("Asia/Kolkata").date(), tz="Asia/Kolkata").timestamp())
                hi = int(pd.Timestamp(pd.to_datetime(ts[60_000], unit="s", utc=True).tz_convert("Asia/Kolkata").date(), tz="Asia/Kolkata").timestamp()) + 86_399
                t = time.perf_counter()
                got = store.read_range(4, 1, lo, hi)
                out[f"{engine}_range_read_s"] = round(time.perf_counter() - t, 3)
                out[f"{engine}_range_read_rows"] = len(got)
                store.close()
            out["tables_for_data"] = 1
            # resample 10 y of daily bars for 50 instruments -> W and M
            store = open_bar_store(os.path.join(d, "res.duckdb"), "duckdb")
            days = pd.bdate_range("2015-01-01", "2024-12-31")
            dts = ((days.asi8 // 10**9) - 19800).astype("int64")
            for k in range(50):
                store.replace_range(k + 1, 8, int(dts[0]), int(dts[-1]),
                                    pd.DataFrame({"ts": dts, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10}))
            t = time.perf_counter()
            for k in range(50):
                store.resample(k + 1, "W")
                store.resample(k + 1, "M")
            out["resample_50_instruments_W_and_M_s"] = round(time.perf_counter() - t, 3)
            store.close()
        return out
    run("storage_engine", s_storage)

    # ------------------------------------------------------------- correctness of stored daily dates / identity
    def s_correctness():
        out = {}
        body, _ = dl(symbols=["DATECHK"], exchanges=["NSE"], interval="D", start_date="2025-01-01", end_date="2025-01-03")
        with app.app_context():
            first = db.session.execute(text("SELECT date, time FROM data_datechk_nse_d ORDER BY date LIMIT 1")).fetchone()
        out["v1_path_first_daily_row_for_2025_01_01"] = f"{first[0]} {first[1]}" if first else None
        for s in ("M&M", "M-M"):
            dl(symbols=[s], exchanges=["NSE"], interval="D", start_date="2025-01-01", end_date="2025-01-10")
        with app.app_context():
            out["v1_path_tables_for_M&M_and_M-M"] = len([t for t in inspect(db.engine).get_table_names() if t.startswith("data_m_m")])
        if results["version"] == "after":
            import tempfile
            from datetime import date

            from app.core.provider.openalgo import get_provider
            from app.core.store import open_bar_store
            from app.core.timeutil import ist_date_of_epoch
            prov = get_provider(mock.url, "bench-key-1234")
            store = open_bar_store(os.path.join(tempfile.mkdtemp(), "c.duckdb"))
            res = {}
            for conv in ("ist_midnight", "utc_midnight"):
                mock.configure(daily_ts=conv)
                r = prov.history("DATECHK", "NSE", "D", date(2025, 1, 1), date(2025, 1, 3))
                res[conv] = str(ist_date_of_epoch(int(r.bars["ts"].iloc[0])))
            out["v2_first_daily_trading_date_by_broker_convention"] = res
            for iid, s in ((1, "M&M"), (2, "M-M")):
                r = prov.history(s, "NSE", "D", date(2025, 1, 1), date(2025, 1, 10))
                store.replace_range(iid, 8, int(r.bars.ts.min()), int(r.bars.ts.max()), r.bars)
            out["v2_series_for_M&M_and_M-M"] = len(store.coverage())
            store.close()
        return out
    run("correctness", s_correctness)

    def s_legacy_import():
        if results["version"] == "before":
            return {"available": False}
        import shutil
        import tempfile

        from app.core.importer.legacy import LegacyImporter
        from app.core.meta.db import MetaDB
        from app.core.secrets import SecretBox
        from app.core.store import open_bar_store
        d = tempfile.mkdtemp()
        shutil.copy(os.path.join(args.mock_dir, "tests", "fixtures", "legacy_v1.db"), os.path.join(d, "v1.db"))
        meta, store = MetaDB(os.path.join(d, "m.db")), open_bar_store(os.path.join(d, "b.duckdb"))
        os.environ["HISTORIFY_SECRET_BACKEND"] = "file"
        rep = LegacyImporter(os.path.join(d, "v1.db"), meta, store, SecretBox(d), d).run()
        store.close()
        meta.dispose()
        s = rep.to_dict()["summary"]
        return {"available": True, "reconciled": rep.reconciled, "legacy_db_unchanged": rep.legacy_unchanged,
                "series": s["series"], "rows_imported": s["rows_imported"], "duplicates_merged": s["duplicates_merged"],
                "collisions_reported": s["collisions"], "orphans_reported": s["orphans"], "duration_s": rep.duration_s}
    run("legacy_import", s_legacy_import)

    mock.stop()
    with open(args.out, "w") as f:
        json.dump(results, f, indent=2, default=str)


if __name__ == "__main__":
    main()
