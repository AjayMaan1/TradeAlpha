"""
Screenshots of the REAL v2 application (not the prototype): serves the app with the mock OpenAlgo broker
(Angel One profile, synthetic market data), seeds watchlists / downloads / schedules through the public services,
then captures every page with headless Chromium.

    cd historify && python ../design/phase4-screens/shoot.py --out ../design/phase4-screens

No user data is involved - every symbol, price and bar comes from the deterministic mock.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import time
from datetime import date, datetime

NOW = "2025-03-28T16:05:00+05:30"      # Friday, after the NSE close


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--chromium", default=os.getenv("HISTORIFY_CHROMIUM", ""))
    args = ap.parse_args()
    sys.path.insert(0, os.getcwd())
    os.makedirs(args.out, exist_ok=True)
    import logging
    logging.disable(logging.WARNING)

    from werkzeug.serving import make_server

    from app import create_app
    from app.core.clock import FrozenClock
    from app.core.meta.models import Instrument, Watchlist, WatchlistItem
    from app.core.services import Services
    from tests.mock_provider.server import MockServer

    mock = MockServer().start()
    mock.configure(now=NOW, consistent=True)           # broker-like data: 5m..1h and D built from the 1m candles
    tmp = tempfile.mkdtemp(prefix="historify-shots-")
    sv = Services(os.path.join(tmp, "instance"), clock=FrozenClock(datetime.fromisoformat(NOW)),
                  engine_options={"poll_s": 0.05, "backoff_base_s": 0.1, "auth_probe_s": 3600,
                                  "short_recheck_delay_s": 0.1})
    sv.settings.set("openalgo_api_host", mock.url)
    sv.settings.set("openalgo_api_key", "demo-key-7d41")
    sv.settings_changed()
    sv.engine.start()
    sv.runner.start()

    lists = {"Default": [("RELIANCE", "NSE"), ("SBIN", "NSE"), ("TCS", "NSE"), ("INFY", "NSE"), ("HDFCBANK", "NSE"),
                         ("ICICIBANK", "NSE"), ("M&M", "NSE"), ("BAJAJ-AUTO", "NSE"), ("TCS", "BSE"),
                         ("NIFTY", "NSE_INDEX")],
             "Commodities": [("CRUDEOIL", "MCX"), ("GOLDM", "MCX"), ("SILVERM", "MCX")]}
    ids = {}
    with sv.meta.session() as s:
        for name, items in lists.items():
            wl = s.query(Watchlist).filter_by(name=name).first() or Watchlist(name=name)
            s.add(wl)
            s.flush()
            for pos, (sym, ex) in enumerate(items):
                inst = Instrument(symbol=sym, exchange=ex, name=sym)
                s.add(inst)
                s.flush()
                ids[(sym, ex)] = inst.id
                s.add(WatchlistItem(watchlist_id=wl.id, instrument_id=inst.id, position=pos))
        s.commit()

    def run(job):
        while sv.jobs.job(job.job_id)["status"] not in ("succeeded", "partial", "failed", "cancelled"):
            time.sleep(0.05)

    eq = [v for k, v in ids.items() if k[1] in ("NSE", "BSE")]
    run(sv.jobs.enqueue_download(eq, ["D"], mode="fixed", start=date(2023, 4, 1), end=date(2025, 3, 28),
                                 origin="ui", label="Backfill 2 years · Default · D"))
    run(sv.jobs.enqueue_download(eq[:6], ["5m"], mode="fixed", start=date(2025, 1, 1), end=date(2025, 3, 28),
                                 origin="ui"))
    run(sv.jobs.enqueue_download(eq[:3], ["1m"], mode="fixed", start=date(2025, 2, 26), end=date(2025, 3, 28),
                                 origin="ui"))
    mock.configure(truncate_rate=1.0)                            # one series with a visible gap in the middle
    run(sv.jobs.enqueue_download([ids[("ICICIBANK", "NSE")]], ["1m"], mode="fixed", start=date(2025, 3, 3),
                                 end=date(2025, 3, 7), origin="ui"))
    mock.configure(truncate_rate=0.0)
    run(sv.jobs.enqueue_download([ids[("ICICIBANK", "NSE")]], ["1m"], mode="fixed", start=date(2025, 3, 10),
                                 end=date(2025, 3, 28), origin="ui"))
    from app.core import coverage as cov
    print("gap series:", cov.coverage_rows(sv, [ids[("ICICIBANK", "NSE")]])[0]["series"].get("1m"))
    comm = [v for k, v in ids.items() if k[1] == "MCX"]
    run(sv.jobs.enqueue_download(comm, ["D", "15m"], mode="fixed", start=date(2025, 1, 1), end=date(2025, 3, 27),
                                 origin="ui"))
    wl_default = 1
    post = sv.scheduler.create({"name": "Post-close update", "trigger": {"type": "market_event", "event": "after_close",
                                "exchange": "NSE", "offset_min": 15}, "target": {"all_watchlists": True},
                                "intervals": ["D", "5m", "1m"]})
    sv.scheduler.create({"name": "Pre-open check", "trigger": {"type": "market_event", "event": "before_open",
                         "exchange": "NSE", "offset_min": -30}, "target": {"watchlist_id": wl_default}, "intervals": ["D"]})
    sv.scheduler.create({"name": "Intraday 5m · Default", "trigger": {"type": "session_interval", "exchange": "NSE",
                         "every_min": 5}, "target": {"watchlist_id": wl_default}, "intervals": ["5m"]})
    sv.scheduler.create({"name": "MCX evening close", "trigger": {"type": "market_event", "event": "after_close",
                         "exchange": "MCX", "offset_min": 10}, "target": {"watchlist_id": 2}, "intervals": ["D", "15m"]})
    sv.scheduler.create({"name": "Weekly gap repair", "trigger": {"type": "cron", "expr": "0 10 * * SUN"},
                         "target": {"all_watchlists": True}, "intervals": ["D", "5m"], "mode": "repair"})
    sv.scheduler.fire(post["id"], datetime.fromisoformat("2025-03-14T15:45:00+05:30"))     # Holi -> skipped
    r = sv.scheduler.fire(post["id"], datetime.fromisoformat(NOW))
    if r.get("job_id"):
        while sv.jobs.job(r["job_id"])["status"] in ("queued", "running"):
            time.sleep(0.05)
    # data-health content: an off-grid bar (quarantine), a split-adjusted re-download (revisions), a roll-up audit,
    # a full maintenance run and a snapshot
    from datetime import datetime as _dt

    from app.core.integrity.rollup_audit import run_job as rollup_job
    from app.core.timeutil import IST as _IST
    off = int(_dt(2025, 3, 26, 10, 2, tzinfo=_IST).timestamp())
    mock.configure(extra_bars={"HDFCBANK|5m": [{"timestamp": off, "open": 1, "high": 1, "low": 1, "close": 1,
                                                "volume": 1}]})
    run(sv.jobs.enqueue_download([ids[("HDFCBANK", "NSE")]], ["5m"], mode="fixed", start=date(2025, 3, 24),
                                 end=date(2025, 3, 28), origin="ui"))
    mock.configure(extra_bars={}, adjust={"BAJAJ-AUTO": 0.5})
    run(sv.jobs.enqueue_download([ids[("BAJAJ-AUTO", "NSE")]], ["D"], mode="fixed", start=date(2025, 3, 3),
                                 end=date(2025, 3, 28), origin="ui", label="Re-download BAJAJ-AUTO (broker adjusted)"))
    mock.configure(adjust={})
    rollup_job(sv, "shots", {"symbols": ["NSE:RELIANCE", "NSE:SBIN"], "sessions": 5, "probe": False})
    mj = sv.jobs.create_background_job("maintenance", {}, "Weekly maintenance", origin="schedule")
    sv.runner.wake()
    while sv.jobs.job(mj)["status"] in ("queued", "running"):
        time.sleep(0.1)
    snap = sv.snapshots.create("nifty-pilot-2023-2025", watchlist_id=1, intervals=["D", "5m", "W"],
                               notes="pilot strategy backtest")
    while sv.snapshots.get(snap["id"])["status"] == "building":
        time.sleep(0.1)
    # a long backfill left running (paused) so the Jobs page shows progress
    bf = sv.jobs.enqueue_download(eq[:8], ["1m"], mode="fixed", start=date(2024, 4, 1), end=date(2025, 3, 28),
                                  origin="ui", priority="backfill", label="1 year of 1-minute bars · Default")
    time.sleep(6)
    sv.jobs.pause(bf.job_id)

    app = create_app({"HISTORIFY_SERVICES": sv}, instance_path=sv.instance_path)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    from playwright.sync_api import sync_playwright

    shots = [
        ("dashboard", "/", None, "light"), ("watchlists", "/watchlists", None, "light"),
        ("charts", f"/charts?i={ids[('RELIANCE', 'NSE')]}&tf=D", None, "light"),
        ("charts-5m-dark", f"/charts?i={ids[('SBIN', 'NSE')]}&tf=5m", None, "dark"),
        ("data", "/data", None, "light"), ("data-gap-drawer", "/data?status=gaps", "gapdrawer", "light"),
        ("jobs", f"/jobs?id={bf.job_id}", None, "light"), ("jobs-new-download", "/jobs?new=1", None, "light"),
        ("scheduler", "/scheduler", None, "light"), ("scheduler-editor", "/scheduler", "preset", "light"),
        ("import", "/import", "import", "light"), ("export", "/export", None, "light"),
        ("settings", "/settings", None, "light"), ("settings-calendar", "/settings#calendar", None, "light"),
        ("dashboard-dark", "/", None, "dark"),
        ("health", "/health", None, "light"), ("health-issues", "/health?tab=issues", None, "light"),
        ("health-revisions", "/health?tab=revisions", "revision", "light"),
        ("health-snapshots", "/health?tab=snapshots", "manifest", "light"),
        ("health-maintenance", "/health?tab=maintenance", None, "light"),
        ("settings-candles", "/settings#candles", None, "light"),
    ]
    with sync_playwright() as p:
        kw = {"args": ["--no-sandbox"]}
        if args.chromium and os.path.exists(args.chromium):
            kw["executable_path"] = args.chromium
        browser = p.chromium.launch(**kw)
        for name, path, action, theme in shots:
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=theme)
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            page.on("console", lambda m, errors=errors: errors.append(m.text) if m.type == "error" else None)
            page.goto(base + path)
            page.wait_for_load_state("networkidle")
            if action == "preset":
                page.locator("div.card-h", has_text="After market close").get_by_role("button", name="Use").click()
                page.wait_for_timeout(800)
            elif action == "gapdrawer":
                page.locator("#coverage-table button.cell-btn", has_text="gap").first.click()
                page.wait_for_timeout(800)
            elif action == "revision":
                page.get_by_role("button", name="Compare").first.click()
                page.wait_for_timeout(800)
            elif action == "manifest":
                page.get_by_role("button", name="Manifest").first.click()
                page.wait_for_timeout(800)
            elif action == "import":
                page.fill("#import-text", "symbol,exchange\nSBIN,NSE\nRELIANCE,NSE\nTCS,BSE\nSBIN,NSE\nGOLDM,MCX\nbad sym,NSE")
                page.click("#import-read")
                page.wait_for_timeout(800)
            page.wait_for_timeout(700)
            page.screenshot(path=os.path.join(args.out, f"{name}.png"), full_page=False)
            print(name, "errors:" if errors else "ok", errors or "")
            ctx.close()
        # broker session expired: the engine pauses every job, the banner offers "resume"
        mock.configure(session_valid=False)
        exp = sv.jobs.enqueue_download([ids[("INFY", "NSE")]], ["15m"], mode="fixed", start=date(2025, 3, 3),
                                       end=date(2025, 3, 28), origin="ui", label="15m · INFY")
        while sv.jobs.job(exp.job_id)["status"] != "paused_auth":
            time.sleep(0.05)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(base + "/")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(700)
        page.screenshot(path=os.path.join(args.out, "dashboard-auth-expired.png"))
        print("dashboard-auth-expired ok")
        ctx.close()
        mock.configure(session_valid=True)
        sv.engine.resume_auth()
        # first-run setup page on an unconfigured instance
        sv.settings.set("openalgo_api_key", None)
        sv.settings_changed()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(base + "/")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=os.path.join(args.out, "setup.png"))
        print("setup", page.url)
        browser.close()
    server.shutdown()
    sv.close()
    mock.stop()


if __name__ == "__main__":
    main()
