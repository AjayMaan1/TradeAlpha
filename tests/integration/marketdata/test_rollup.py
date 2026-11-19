"""Hybrid roll-up (intraday 3m..1h from 1m, session-anchored; D from the broker) and the roll-up audit."""
from __future__ import annotations

import io
from datetime import date, datetime

from tests.conftest import add_instrument, wait_job
from tests.support.mock_provider.server import STATE, expected_bars
from tradealpha.marketdata.providers.normalize import frame_from_payload, normalize_bars
from tradealpha.platform.time.intervals import resolve
from tradealpha.platform.time.timeutil import IST


def rollup_mode(sv):
    sv.settings.set("intraday_source", "rollup_1m")
    sv.settings_changed()


def oracle(sym, iv, s, e):
    return normalize_bars(frame_from_payload(expected_bars(sym, "NSE", iv, s, e)), resolve(iv))[0]


def test_rollup_mode_downloads_only_1m(make_services, mock_server):
    sv = make_services()
    rollup_mode(sv)
    iid = add_instrument(sv, "SBIN")
    r = sv.jobs.enqueue_download([iid], ["5m", "15m", "1h", "D"], mode="fixed", start=date(2025, 3, 24),
                                 end=date(2025, 3, 28))
    wait_job(sv, r.job_id)
    assert sorted({x["interval"] for x in mock_server.request_log}) == ["1m", "D"]
    assert {t["interval"] for t in sv.jobs.tasks(r.job_id)} == {"1m", "D"}


def test_derived_bars_equal_the_broker_candles_when_the_broker_rolls_up_1m(make_v2, mock_server):
    c, sv, _ = make_v2()
    mock_server.configure(consistent=True)                       # broker 5m/15m/1h are roll-ups of its 1m
    rollup_mode(sv)
    iid = add_instrument(sv, "SBIN")
    wait_job(sv, sv.jobs.enqueue_download([iid], ["1m"], mode="fixed", start=date(2025, 3, 17),
                                          end=date(2025, 3, 28)).job_id)
    STATE.config["now"] = "2025-03-28T16:00:00+05:30"
    for code in ("3m", "5m", "10m", "15m", "30m", "1h"):
        b = c.get(f"/api/v2/bars?instrument_id={iid}&interval={code}&limit=5000").get_json()
        assert b["source"] == "derived_from_1m" and b["derived"]
        want = oracle("SBIN", code, date(2025, 3, 17), date(2025, 3, 28))
        assert b["bars"]["t"] == want["ts"].tolist(), code                     # session-anchored grid (09:15)
        assert b["bars"]["c"] == want["close"].round(4).tolist(), code
        assert b["bars"]["v"] == want["volume"].tolist(), code
    first = datetime.fromtimestamp(c.get(f"/api/v2/bars?instrument_id={iid}&interval=1h&limit=5000")
                                   .get_json()["bars"]["t"][0], IST)
    assert first.strftime("%H:%M") == "09:15"                                   # 1h = 09:15-10:15, not 09:00
    d = c.get(f"/api/v2/bars?instrument_id={iid}&interval=D").get_json()
    assert d["source"] == "broker"                                               # D is never rolled up


def test_derived_paging_covers_every_bar_exactly_once(make_v2, mock_server):
    c, sv, _ = make_v2()
    rollup_mode(sv)
    iid = add_instrument(sv, "SBIN")
    wait_job(sv, sv.jobs.enqueue_download([iid], ["1m"], mode="fixed", start=date(2025, 3, 3),
                                          end=date(2025, 3, 28)).job_id)
    full = c.get(f"/api/v2/bars?instrument_id={iid}&interval=15m&limit=5000").get_json()["bars"]["t"]
    seen, to = [], None
    while True:
        b = c.get(f"/api/v2/bars?instrument_id={iid}&interval=15m&limit=100" + (f"&to={to}" if to else "")).get_json()
        seen = b["bars"]["t"] + seen
        if not b["has_more"]:
            break
        to = b["next_to"]
    assert seen == full and len(seen) == len(set(seen)) == 19 * 25
    last = full[-1]
    since = c.get(f"/api/v2/bars?instrument_id={iid}&interval=15m&since={last - 1}").get_json()
    assert since["bars"]["t"] == [last]


def test_coverage_export_and_broker_source_in_rollup_mode(make_v2, mock_server):
    c, sv, _ = make_v2()
    iid = add_instrument(sv, "SBIN")
    wait_job(sv, sv.jobs.enqueue_download([iid], ["5m"], mode="fixed", start=date(2025, 3, 24),
                                          end=date(2025, 3, 28)).job_id)   # native 5m stored in broker mode
    rollup_mode(sv)
    wait_job(sv, sv.jobs.enqueue_download([iid], ["1m"], mode="fixed", start=date(2025, 3, 24),
                                          end=date(2025, 3, 28)).job_id)
    cells = c.get("/api/v2/coverage").get_json()["rows"][0]["series"]
    assert cells["5m"]["derived"] and cells["5m"]["source"] == "derived_from_1m" and not cells["1m"]["derived"]
    broker = c.get(f"/api/v2/bars?instrument_id={iid}&interval=5m&source=broker&limit=5000").get_json()
    assert broker["source"] == "broker" and len(broker["bars"]["t"]) == 5 * 75
    job = c.post("/api/v2/jobs/export", json={"instrument_ids": [iid], "intervals": ["5m"], "format": "csv"}).get_json()
    wait_job(sv, job["job_id"])
    import pandas as pd
    df = pd.read_csv(io.BytesIO(c.get(f"/api/v2/exports/{job['job_id']}/file").get_data()))
    assert len(df) == 5 * 75 and df["datetime_ist"].iloc[0].endswith("09:15:00")
    m1 = c.get(f"/api/v2/bars?instrument_id={iid}&interval=1m&limit=5000").get_json()["bars"]["v"]
    assert df["volume"].sum() == sum(m1)                          # nothing lost or double counted in the roll-up


def test_follow_in_rollup_mode_requests_once_per_5m_bucket(make_services, mock_server):
    from datetime import datetime as dt
    sv = make_services(now="2025-03-28T11:06:30+05:30")
    rollup_mode(sv)
    iid = add_instrument(sv, "SBIN")
    wait_job(sv, sv.jobs.enqueue_download([iid], ["1m"], mode="fixed", start=date(2025, 3, 27),
                                          end=date(2025, 3, 28)).job_id)
    sv.follow.follow(iid, "5m")
    assert sv.follow.tick() == []                                 # 11:00-11:05 bucket already complete in 1m
    for t in ("11:08:30", "11:11:30"):
        sv.clock.set(dt.fromisoformat(f"2025-03-28T{t}+05:30"))
        mock_server.configure(now=f"2025-03-28T{t}+05:30")
        jobs = sv.follow.tick()
        if t == "11:08:30":
            assert jobs == []                                     # mid-bucket: no request although 1m bars closed
        else:
            assert len(jobs) == 1 and wait_job(sv, jobs[0])["status"] == "succeeded"


# ---------------------------------------------------------------------------------------------- roll-up audit
def _audit(sv, mock_server, consistent, probe=False):
    from tradealpha.marketdata.integrity.rollup_audit import run_audit
    from tradealpha.marketdata.providers.openalgo.client import OpenAlgoProvider
    mock_server.configure(consistent=consistent)
    cfg = sv.config()
    prov = OpenAlgoProvider(cfg.host, cfg.api_key, cfg.profile, timeout_s=10, max_retries=2)
    try:
        return run_audit(prov, sv.calendar(), [("SBIN", "NSE"), ("TCS", "NSE")], 5, ["5m", "15m", "1h"],
                         sv.clock.now().astimezone(IST), cfg.max_days, probe=probe)
    finally:
        prov.close()


def test_rollup_audit_recommends_rollup_when_the_broker_rolls_up_1m(make_services, mock_server):
    sv = make_services(start=False)
    rep = _audit(sv, mock_server, consistent=True, probe=True)
    assert rep["recommendation"]["intraday_source"] == "rollup_1m"
    for code in ("5m", "15m", "1h"):
        assert rep["summary"][code]["ohlc_match_pct"] == 100.0 and rep["summary"][code]["only_broker"] == 0
    pos = rep["instruments"][0]["intervals"]["1h"]["by_position"]
    assert pos["last"]["compared"] == 5 and pos["last"]["all_fields_match_pct"] == 100.0    # partial 15:15 bar
    assert rep["daily"]["close_equal_pct"] < 50                   # official close (VWAP) != last 1m close
    assert "keep D from the broker" in rep["recommendation"]["daily"]
    assert rep["depth_probe"]["oldest_year_with_data"] == 10


def test_rollup_audit_says_keep_broker_when_candles_differ(make_services, mock_server):
    sv = make_services(start=False)
    rep = _audit(sv, mock_server, consistent=False)
    assert rep["recommendation"]["intraday_source"] == "broker"
    assert rep["summary"]["5m"]["ohlc_match_pct"] < 10


def test_rollup_audit_job_via_api_and_health(make_v2, mock_server):
    c, sv, _ = make_v2()
    mock_server.configure(consistent=True)
    r = c.post("/api/v2/rollup-audit", json={"symbols": "NSE:SBIN", "sessions": 3, "probe": False})
    job = wait_job(sv, r.get_json()["job_id"])
    assert job["status"] == "succeeded" and job["result"]["recommendation"]["intraday_source"] == "rollup_1m"
    h = c.get("/api/v2/health").get_json()
    assert h["last_rollup_audit"]["recommendation"]["intraday_source"] == "rollup_1m"
    import os
    reports = os.listdir(os.path.join(sv.instance_path, "audit_reports"))
    assert any(f.endswith(".md") for f in reports) and any(f.endswith(".json") for f in reports)
