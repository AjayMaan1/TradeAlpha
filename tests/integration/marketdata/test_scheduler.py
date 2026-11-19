"""Scheduler on the job engine (plan E2E-20..23)."""
import json
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime

from tests.conftest import add_instrument, wait_job
from tradealpha.platform.db.models import Watchlist, WatchlistItem

POST_CLOSE = {"type": "market_event", "event": "after_close", "exchange": "NSE", "offset_min": 15}


def watchlist(sv, ids, name="Default"):
    with sv.meta.session() as s:
        wl = Watchlist(name=name)
        s.add(wl)
        s.flush()
        for pos, iid in enumerate(ids):
            s.add(WatchlistItem(watchlist_id=wl.id, instrument_id=iid, position=pos))
        s.commit()
        return wl.id


def seed(sv, ids, end=date(2025, 3, 14)):
    wait_job(sv, sv.jobs.enqueue_download(ids, ["D"], mode="fixed", start=date(2025, 3, 3), end=end).job_id)


def test_post_close_run_enqueues_job_and_stores_final_bar(make_services, mock_server):
    sv = make_services(now="2025-03-17T15:45:00+05:30")
    ids = [add_instrument(sv, s) for s in ("SBIN", "TCS")]
    seed(sv, ids)
    sched = sv.scheduler.create({"name": "Post-close", "trigger": POST_CLOSE,
                                 "target": {"watchlist_id": watchlist(sv, ids)}, "intervals": ["D"]})
    assert sched["next_run"].startswith("2025-03-18T15:45")        # 15:45 today already passed at fixture "now"
    run = sv.scheduler.fire(sched["id"], datetime.fromisoformat("2025-03-17T15:45:00+05:30"))
    j = wait_job(sv, run["job_id"])
    assert j["status"] == "succeeded" and j["origin"] == f"schedule:{sched['id']}" and j["priority"] == "scheduled"
    assert sv.scheduler.runs(sched["id"])[0]["status"] == "succeeded"
    from tradealpha.platform.time.intervals import resolve
    from tradealpha.platform.time.timeutil import ist_date_of_epoch
    assert ist_date_of_epoch(int(sv.store.read_range(ids[0], resolve("D").id)["ts"].max())) == date(2025, 3, 17)


def test_holiday_recorded_with_zero_broker_calls(make_services, mock_server):               # D6
    sv = make_services(now="2025-03-31T15:45:00+05:30")
    ids = [add_instrument(sv, "SBIN")]
    sched = sv.scheduler.create({"name": "Post-close", "trigger": POST_CLOSE,
                                 "target": {"instrument_ids": ids}, "intervals": ["D"]})
    before = mock_server.stats["history_requests"]
    run = sv.scheduler.fire(sched["id"], datetime.fromisoformat("2025-03-31T15:45:00+05:30"))
    assert run["status"] == "skipped_holiday" and "Mon 31 Mar 2025" in run["detail"] and run["job_id"] is None
    assert mock_server.stats["history_requests"] == before


def test_overlap_skip_and_queue(make_services):
    sv = make_services(now="2025-03-17T15:45:00+05:30", start=False)            # engine stopped: job stays active
    ids = [add_instrument(sv, "SBIN")]
    sched = sv.scheduler.create({"name": "S", "trigger": POST_CLOSE, "target": {"instrument_ids": ids},
                                 "intervals": ["D"]})
    first = sv.scheduler.fire(sched["id"], datetime.fromisoformat("2025-03-17T15:45:00+05:30"))
    second = sv.scheduler.fire(sched["id"], datetime.fromisoformat("2025-03-17T15:45:00+05:30"))
    assert first["status"] == "queued" and second["status"] == "skipped_overlap"
    sv.scheduler.update(sched["id"], {"overlap_policy": "queue"})
    assert sv.scheduler.fire(sched["id"])["status"] == "queued"


def test_catch_up_after_downtime_fires_once(make_services):                                  # E2E-21
    sv = make_services(now="2025-03-17T17:00:00+05:30")
    ids = [add_instrument(sv, "SBIN")]
    seed(sv, ids)
    sched = sv.scheduler.create({"name": "Post-close", "trigger": POST_CLOSE, "target": {"instrument_ids": ids},
                                 "intervals": ["D"]})
    runs = sv.scheduler.catch_up()
    assert len(runs) == 1 and runs[0]["origin"] == "catch_up" and runs[0]["scheduled_for"].startswith("2025-03-17T10:15")
    wait_job(sv, runs[0]["job_id"])
    assert sv.scheduler.catch_up() == []                                      # recorded -> not again
    assert sched["id"] == runs[0]["schedule_id"]


def test_up_to_date_and_empty_target_and_manual_run(make_services):
    sv = make_services(now="2025-03-17T16:00:00+05:30")
    ids = [add_instrument(sv, "SBIN")]
    seed(sv, ids, end=date(2025, 3, 17))
    s1 = sv.scheduler.create({"name": "A", "trigger": POST_CLOSE, "target": {"instrument_ids": ids}, "intervals": ["D"]})
    assert sv.scheduler.fire(s1["id"])["status"] == "up_to_date"
    s2 = sv.scheduler.create({"name": "B", "trigger": POST_CLOSE, "target": {"watchlist_id": watchlist(sv, [], "Empty")},
                              "intervals": ["D"], "is_paused": True})
    assert sv.scheduler.fire(s2["id"])["status"] == "paused"                  # paused schedules ignore triggers
    assert sv.scheduler.run_now(s2["id"])["status"] == "skipped_empty"        # manual run still evaluates
    assert sv.scheduler.get(s2["id"])["next_run"] is None


def test_once_schedule_fires_on_real_clock_and_completes(make_services, mock_server):         # APScheduler wiring
    from datetime import timedelta

    from tradealpha.platform.time.timeutil import IST
    sv = make_services(now=None)
    ids = [add_instrument(sv, "SBIN")]
    at = (datetime.now(IST) + timedelta(seconds=2)).isoformat()
    sv.scheduler.start()
    s = sv.scheduler.create({"name": "One-off backfill", "trigger": {"type": "once", "at": at}, "mode": "fixed",
                             "target": {"instrument_ids": ids, "start": "2025-03-03", "end": "2025-03-07"},
                             "intervals": ["D"]})
    deadline = time.monotonic() + 15
    while not sv.scheduler.runs(s["id"]) and time.monotonic() < deadline:
        time.sleep(0.2)
    run = sv.scheduler.runs(s["id"])[0]
    assert run["origin"] == "trigger" and run["job_id"]
    j = wait_job(sv, run["job_id"])
    assert j["priority"] == "backfill" and j["status"] == "succeeded"
    assert sv.scheduler.get(s["id"])["is_paused"] is True                    # one-off completes


def test_validation_errors(make_services):
    import pytest

    from tradealpha.marketdata.ingestion.scheduler import ScheduleError
    sv = make_services(start=False)
    ids = [add_instrument(sv, "SBIN")]
    ok = {"name": "X", "trigger": POST_CLOSE, "target": {"instrument_ids": ids}, "intervals": ["D"]}
    sv.scheduler.create(ok)
    for bad in ({**ok}, {**ok, "name": "Y", "intervals": ["W"]}, {**ok, "name": "Z", "intervals": []},
                {**ok, "name": "Q", "target": {}}):
        with pytest.raises(ScheduleError):
            sv.scheduler.create(bad)


def test_preview_estimate(make_services):
    sv = make_services(now="2025-03-28T15:48:00+05:30", start=False)
    ids = [add_instrument(sv, f"I{i}") for i in range(32)]
    wl = watchlist(sv, ids)
    pv = sv.scheduler.preview({"trigger": POST_CLOSE, "target": {"watchlist_id": wl}, "intervals": ["1m", "5m", "D"]})
    assert pv["estimate"]["requests"] == 96 and pv["estimate"]["seconds"] == 40
    assert pv["estimate"]["hour_budget_pct"] == 2.4 and pv["skipped"][0] == "2025-03-31"


def test_v1_schedules_are_converted(make_services, tmp_path):
    sv = make_services(start=False)
    with sv.meta.session() as s:
        s.add(Watchlist(name="Default"))
        s.commit()
    v1 = tmp_path / "v1.db"
    with closing(sqlite3.connect(v1)) as c:
        c.execute("CREATE TABLE scheduler_jobs (id TEXT, name TEXT, job_type TEXT, time TEXT, minutes INT, "
                  "symbols TEXT, exchanges TEXT, interval TEXT, is_paused BOOL)")
        c.executemany("INSERT INTO scheduler_jobs VALUES (?,?,?,?,?,?,?,?,?)", [
            ("market_close_download", "Market Close Download", "market_close", "15:35", None, None, None, "D", 0),
            ("pre_market_download", "Pre-Market Download", "pre_market", "08:30", None, None, None, "D", 0),
            ("d1", "Daily 18:10", "daily", "18:10", None, json.dumps(["SBIN"]), json.dumps(["NSE"]), "W", 1),
            ("i5", "Every 5", "interval", None, 5, None, None, "5m", 0)])
        c.commit()
    from tradealpha.marketdata.ingestion.legacy_v1_schedules import import_v1_schedules
    out = {x["v1_id"]: x for x in import_v1_schedules(str(v1), sv)}
    assert out["market_close_download"]["when"] == "Trading days · NSE close + 15 min (15:45 IST)"
    assert "15:45" in out["market_close_download"]["note"]
    assert out["pre_market_download"]["when"] == "Trading days · NSE open − 30 min (08:45 IST)"
    assert out["d1"]["when"] == "Custom · cron “10 18 * * 1-5” (IST)" and "converted to D" in out["d1"]["note"]
    assert out["i5"]["when"] == "Every 5 min while NSE is open"
    d1 = [s for s in sv.scheduler.list() if s["name"] == "Daily 18:10"][0]
    assert d1["is_paused"] and d1["intervals"] == ["D"] and d1["target"]["instrument_ids"]
