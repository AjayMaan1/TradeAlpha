"""API v2 for Data Health: issues, revisions, quarantine, audit/maintenance jobs, snapshots, new settings."""
from __future__ import annotations

from datetime import datetime

from tests.conftest import add_instrument, wait_job
from tradealpha.platform.time.timeutil import IST


def _dl(c, sv, ids, intervals, start, end):
    r = c.post("/api/v2/jobs/download", json={"instrument_ids": ids, "intervals": intervals, "mode": "fixed",
                                              "start": start, "end": end}).get_json()
    return wait_job(sv, r["job_id"])


def test_health_issues_quarantine_and_revisions(make_v2, mock_server):
    c, sv, _ = make_v2()
    iid = add_instrument(sv, "SBIN")
    off = int(datetime(2025, 3, 25, 9, 17, tzinfo=IST).timestamp())
    mock_server.configure(extra_bars={"SBIN|5m": [{"timestamp": off, "open": 1, "high": 1, "low": 1, "close": 1,
                                                   "volume": 1}]})
    _dl(c, sv, [iid], ["5m", "D"], "2025-03-24", "2025-03-28")
    h = c.get("/api/v2/health").get_json()
    assert h["ledger"]["complete"] == 10 and h["quarantined_bars"] == 1 and h["issues_open"] == 1
    q = c.get("/api/v2/quarantine").get_json()
    assert q["by_reason"] == {"off_grid": 1} and q["rows"][0]["instrument"]["symbol"] == "SBIN"
    assert c.post("/api/v2/quarantine/release", json={}).get_json() == {"released": 0, "checked": 1}
    issues = c.get("/api/v2/issues").get_json()
    assert issues[0]["kind"] == "quarantine"
    assert c.post(f"/api/v2/issues/{issues[0]['id']}/resolve").status_code == 204
    assert c.get("/api/v2/issues").get_json() == [] and len(c.get("/api/v2/issues?state=resolved").get_json()) == 1
    mock_server.configure(adjust={"SBIN": 0.5})
    _dl(c, sv, [iid], ["D"], "2025-03-24", "2025-03-28")
    revs = c.get("/api/v2/revisions").get_json()
    assert len(revs) == 5 and revs[0]["kind"] == "probable_adjustment" and revs[0]["factor"] == 0.5
    det = c.get(f"/api/v2/revisions/{revs[0]['id']}").get_json()
    assert len(det["old"]) == 1 and abs(det["current"][0]["close"] - det["old"][0]["close"] * 0.5) < 0.011


def test_audit_and_maintenance_jobs_via_api(make_v2):
    c, sv, _ = make_v2()
    _dl(c, sv, [add_instrument(sv, "SBIN")], ["D"], "2025-03-03", "2025-03-28")
    job = wait_job(sv, c.post("/api/v2/integrity/audit").get_json()["job_id"])
    assert job["status"] == "succeeded" and job["result"]["duplicates"] == 0
    job = wait_job(sv, c.post("/api/v2/maintenance/run", json={"steps": ["backup", "storage_report"]})
                   .get_json()["job_id"])
    assert job["status"] == "succeeded" and set(job["result"]["steps"]) == {"backup", "storage_report"}
    m = c.get("/api/v2/maintenance").get_json()
    assert m["day"] == "sat" and m["time"] == "10:00" and m["jobs"][0]["id"] == job["id"]
    rep = c.get("/api/v2/storage/report").get_json()
    assert rep["by_interval"][0]["key"] == "D" and rep["rows"] == 19
    assert c.post("/api/v2/maintenance/run", json={"steps": ["nope"]}).status_code == 400
    assert c.get("/api/v2/health").get_json()["last_audit"]["kind"] == "maintenance"


def test_snapshot_api_lifecycle(make_v2):
    c, sv, _ = make_v2()
    iid = add_instrument(sv, "SBIN")
    wl = c.get("/api/v2/watchlists").get_json()[0]["id"]
    c.post(f"/api/v2/watchlists/{wl}/items", json={"instrument_id": iid})
    _dl(c, sv, [iid], ["D"], "2025-03-03", "2025-03-28")
    r = c.post("/api/v2/snapshots", json={"name": "s1", "watchlist_id": wl, "intervals": ["D", "M"]})
    assert r.status_code == 202
    snap = r.get_json()
    wait_job(sv, snap["job_id"])
    got = c.get(f"/api/v2/snapshots/{snap['id']}").get_json()
    assert got["status"] == "ready" and got["manifest"]["totals"]["series"] == 2
    assert c.post(f"/api/v2/snapshots/{snap['id']}/verify").get_json()["ok"] is True
    assert c.post("/api/v2/snapshots", json={"name": "s1", "watchlist_id": wl}).status_code == 400   # duplicate
    assert c.post("/api/v2/snapshots", json={"name": "x"}).status_code == 400                        # no target
    assert c.delete(f"/api/v2/snapshots/{snap['id']}").status_code == 204
    assert c.get("/api/v2/snapshots").get_json() == []


def test_new_settings_are_validated_and_reschedule_maintenance(make_v2):
    c, sv, _ = make_v2()
    sv.maintenance.ensure_schedule()
    for bad in ({"intraday_source": "ticks"}, {"maintenance_day": "someday"}, {"maintenance_time": "25:00"},
                {"backup_keep": 0}, {"rewrite_free_ratio": 2}):
        assert c.put("/api/v2/settings", json=bad).status_code == 400, bad
    s = c.put("/api/v2/settings", json={"intraday_source": "rollup_1m", "maintenance_day": "sun",
                                        "maintenance_time": "09:30", "maintenance_enabled": True}).get_json()
    assert s["intraday_source"] == "rollup_1m" and s["maintenance_day"] == "sun"
    sched = [x for x in c.get("/api/v2/schedules").get_json() if x["kind"] == "maintenance"][0]
    assert sched["trigger"]["expr"] == "30 9 * * SUN" and sched["next_run"].startswith("2025-03-30T09:30")
    assert c.delete(f"/api/v2/schedules/{sched['id']}").status_code == 400
    c.put("/api/v2/settings", json={"maintenance_enabled": False})
    assert [x for x in c.get("/api/v2/schedules").get_json() if x["kind"] == "maintenance"][0]["is_paused"]
