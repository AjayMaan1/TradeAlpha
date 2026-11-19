"""Download engine end-to-end against the Angel One mock (plan E2E-01..19)."""
import time
from datetime import date, datetime

import pytest

from tests.conftest import add_instrument, wait_job
from tests.support.mock_provider.server import expected_bars
from tradealpha.marketdata.bars.base import checksum_frame
from tradealpha.marketdata.providers.normalize import frame_from_payload, normalize_bars
from tradealpha.platform.db.models import SyncState
from tradealpha.platform.time.intervals import resolve
from tradealpha.platform.time.timeutil import ist_date_of_epoch


def oracle(sym, ex, iv, s, e):
    return normalize_bars(frame_from_payload(expected_bars(sym, ex, iv, s, e)), resolve(iv))[0]


def stored(sv, iid, iv):
    return sv.store.read_range(iid, resolve(iv).id)


def test_backfill_chunked_matches_oracle_without_rate_violations(make_services, mock_server):   # E2E-01/02
    from tradealpha.marketdata.providers.openalgo.client import chunk_ranges
    sv = make_services(start=False)
    ids = [add_instrument(sv, f"S{i}") for i in range(4)]
    big = sv.jobs.enqueue_download(ids, ["D", "1m"], mode="fixed", start=date(2015, 1, 1), end=date(2025, 3, 28))
    expected = 4 * (len(list(chunk_ranges(date(2015, 1, 1), date(2025, 3, 28), 2000)))
                    + len(list(chunk_ranges(date(2015, 1, 1), date(2025, 3, 28), 30))))
    assert big.tasks == expected == 4 * (2 + 125)                   # Angel One caps: D 2000 d, 1m 30 d per request
    sv.jobs.cancel(big.job_id)
    job = sv.jobs.enqueue_download(ids, ["D"], mode="fixed", start=date(2015, 1, 1), end=date(2025, 3, 28))
    sv.engine.start()
    j = wait_job(sv, job.job_id)
    assert j["status"] == "succeeded" and j["requests"] == 8 and j["tasks"]["succeeded"] == 8
    assert mock_server.stats["history_429"] == 0 and mock_server.stats["history_range_err"] == 0
    for i, iid in enumerate(ids):
        assert checksum_frame(stored(sv, iid, "D")) == checksum_frame(oracle(f"S{i}", "NSE", "D", date(2015, 1, 1),
                                                                           date(2025, 3, 28)))


def test_transient_errors_retried_no_duplicates(make_services, mock_server):                    # E2E-04
    sv = make_services()
    mock_server.configure(error_rate=0.3, seed=5)
    iid = add_instrument(sv, "FLAKY")
    j = wait_job(sv, sv.jobs.enqueue_download([iid], ["5m"], mode="fixed", start=date(2024, 6, 1),
                                              end=date(2025, 3, 28)).job_id)
    assert j["status"] == "succeeded" and j["retries"] == mock_server.stats["5xx"] > 0
    got = stored(sv, iid, "5m")
    assert not got["ts"].duplicated().any()
    assert checksum_frame(got) == checksum_frame(oracle("FLAKY", "NSE", "5m", date(2024, 6, 1), date(2025, 3, 28)))


def test_truncated_responses_are_rechecked_then_repaired(make_services, mock_server):         # E2E-05
    sv = make_services()
    mock_server.configure(truncate_rate=1.0)                        # every response loses its second half
    iid = add_instrument(sv, "TRUNC")
    j = wait_job(sv, sv.jobs.enqueue_download([iid], ["5m"], mode="fixed", start=date(2025, 1, 1),
                                              end=date(2025, 3, 28)).job_id)
    assert j["status"] == "succeeded" and j["requests"] == 2        # original + one automatic re-check
    task = sv.jobs.tasks(j["id"])[0]
    assert task["error_class"] == "short_data" and "kept" in task["error"]
    with sv.meta.session() as s:
        gaps = s.get(SyncState, {"instrument_id": iid, "interval_id": resolve("5m").id}).gap_sessions
    assert gaps > 0                                                  # visible in the Data Manager
    mock_server.configure(truncate_rate=0.0)
    j2 = wait_job(sv, sv.jobs.enqueue_download([iid], ["5m"], mode="repair").job_id)
    assert j2["status"] == "succeeded"
    assert checksum_frame(stored(sv, iid, "5m")) == checksum_frame(oracle("TRUNC", "NSE", "5m", date(2025, 1, 1),
                                                                             date(2025, 3, 28)))
    with sv.meta.session() as s:
        assert s.get(SyncState, {"instrument_id": iid, "interval_id": resolve("5m").id}).gap_sessions == 0


def test_holiday_and_weekend_ranges_are_empty_not_failed(make_services):                         # E2E-06
    sv = make_services()
    iid = add_instrument(sv, "HOLI")
    j = wait_job(sv, sv.jobs.enqueue_download([iid], ["D"], mode="fixed", start=date(2025, 3, 14),
                                              end=date(2025, 3, 16)).job_id)
    assert j["status"] == "succeeded" and j["tasks"]["empty"] == 1 and j["tasks"]["failed"] == 0


def test_new_listing_prefix_is_empty(make_services):                                             # E2E-07
    sv = make_services()
    iid = add_instrument(sv, "NEWLISTCO")
    wait_job(sv, sv.jobs.enqueue_download([iid], ["D"], mode="fixed", start=date(2025, 1, 1),
                                          end=date(2025, 3, 28)).job_id)
    first = stored(sv, iid, "D")["ts"].min()
    assert ist_date_of_epoch(int(first)) == date(2025, 3, 3)


def test_session_expiry_pauses_then_resumes(make_services, mock_server):                         # E2E-08
    sv = make_services()
    ids = [add_instrument(sv, f"A{i}") for i in range(12)]
    mock_server.configure(session_valid=False)
    job = sv.jobs.enqueue_download(ids, ["D"], mode="fixed", start=date(2025, 3, 3), end=date(2025, 3, 7))
    j = wait_job(sv, job.job_id, until=("paused_auth",), timeout=30)
    time.sleep(0.5)
    assert sv.engine.status()["auth_blocked"] is True
    assert mock_server.stats["auth_err"] <= 3                       # at most one call per worker, then nothing
    calls = mock_server.stats["history_requests"]
    time.sleep(0.5)
    assert mock_server.stats["history_requests"] == calls           # no broker calls while blocked
    mock_server.configure(session_valid=True)
    sv.engine.resume_auth()                                         # user re-logged in
    j = wait_job(sv, job.job_id)
    assert j["status"] == "succeeded" and j["tasks"]["succeeded"] == 12


def test_auth_probe_resumes_automatically(make_services, mock_server):
    sv = make_services(auth_probe_s=0.5)
    ids = [add_instrument(sv, f"P{i}") for i in range(6)]
    mock_server.configure(session_valid=False)
    job = sv.jobs.enqueue_download(ids, ["D"], mode="fixed", start=date(2025, 3, 3), end=date(2025, 3, 7))
    wait_job(sv, job.job_id, until=("paused_auth",), timeout=30)
    mock_server.configure(session_valid=True)
    assert wait_job(sv, job.job_id, timeout=30)["status"] == "succeeded"


def test_restart_mid_job_gives_identical_data(make_services, mock_server, tmp_path):             # E2E-09
    sv = make_services(name="a")
    ids = [add_instrument(sv, f"R{i}") for i in range(10)]
    job = sv.jobs.enqueue_download(ids, ["5m"], mode="fixed", start=date(2024, 12, 1), end=date(2025, 3, 28))
    deadline = time.monotonic() + 30
    while sv.jobs.job(job.job_id)["tasks"]["done"] < 5 and time.monotonic() < deadline:
        time.sleep(0.05)
    sv.engine.stop()                                                # "crash": in-flight tasks left running
    with sv.meta.session() as s:
        from sqlalchemy import text
        s.execute(text("UPDATE job_tasks SET status='running' WHERE status='queued' AND id % 3 = 0"))
        s.commit()
    sv.engine.start()                                               # recover() re-queues running tasks
    j = wait_job(sv, job.job_id)
    assert j["status"] == "succeeded" and j["tasks"]["succeeded"] == 20
    for i, iid in enumerate(ids):
        assert checksum_frame(stored(sv, iid, "5m")) == checksum_frame(
            oracle(f"R{i}", "NSE", "5m", date(2024, 12, 1), date(2025, 3, 28)))


def test_pause_resume_cancel_retry(make_services, mock_server):                                   # E2E-10
    sv = make_services()
    ids = [add_instrument(sv, f"C{i}") for i in range(15)]
    job = sv.jobs.enqueue_download(ids, ["D"], mode="fixed", start=date(2025, 3, 3), end=date(2025, 3, 7))
    time.sleep(0.6)
    assert sv.jobs.pause(job.job_id) in ("pausing", "paused")
    j = wait_job(sv, job.job_id, until=("paused",), timeout=10)
    done = j["tasks"]["done"]
    time.sleep(0.6)
    assert sv.jobs.job(job.job_id)["tasks"]["done"] == done          # nothing dispatched while paused
    sv.jobs.resume(job.job_id)
    time.sleep(0.5)
    sv.jobs.cancel(job.job_id)
    j = wait_job(sv, job.job_id)
    assert j["status"] == "cancelled" and j["tasks"]["cancelled"] > 0
    # retry-failed only re-runs failed tasks
    bad = add_instrument(sv, "INVALIDSYM")
    job2 = sv.jobs.enqueue_download([ids[0], bad], ["D"], mode="fixed", start=date(2025, 3, 3), end=date(2025, 3, 7))
    j2 = wait_job(sv, job2.job_id)
    assert j2["status"] == "partial" and j2["tasks"]["failed"] == 1
    failed = [t for t in sv.jobs.tasks(job2.job_id) if t["status"] == "failed"][0]
    assert failed["error_class"] == "permanent" and failed["attempts"] == 1        # E2E-12: no retries
    assert sv.jobs.retry_failed(job2.job_id) == 1
    assert wait_job(sv, job2.job_id)["status"] == "partial"


def test_fresh_rerun_is_idempotent(make_services):                                                # E2E-11
    sv = make_services()
    iid = add_instrument(sv, "IDEM")
    kw = dict(mode="fixed", start=date(2025, 1, 1), end=date(2025, 3, 28))
    wait_job(sv, sv.jobs.enqueue_download([iid], ["5m"], **kw).job_id)
    first = sv.store.checksum(iid, resolve("5m").id)
    wait_job(sv, sv.jobs.enqueue_download([iid], ["5m"], **kw).job_id)
    assert sv.store.checksum(iid, resolve("5m").id) == first


def test_interval_isolation_no_gap(make_services):                                               # E2E-14 (CP-01)
    sv = make_services()
    iid = add_instrument(sv, "ISO")
    wait_job(sv, sv.jobs.enqueue_download([iid], ["D"], mode="fixed", start=date(2025, 1, 1),
                                          end=date(2025, 1, 10)).job_id)
    wait_job(sv, sv.jobs.enqueue_download([iid], ["5m"], mode="fixed", start=date(2025, 3, 3),
                                          end=date(2025, 3, 5)).job_id)
    wait_job(sv, sv.jobs.enqueue_download([iid], ["D"], mode="incremental").job_id)
    assert checksum_frame(stored(sv, iid, "D")) == checksum_frame(oracle("ISO", "NSE", "D", date(2025, 1, 1),
                                                                        date(2025, 3, 28)))


def test_provisional_intraday_bars_are_corrected(make_services, mock_server):                   # E2E-16
    sv = make_services(now="2025-03-18T11:00:00+05:30")
    iid = add_instrument(sv, "PROV")
    wait_job(sv, sv.jobs.enqueue_download([iid], ["5m"], mode="fixed", start=date(2025, 3, 17),
                                          end=date(2025, 3, 18)).job_id)
    assert len(stored(sv, iid, "5m")) < 150                        # 18 Mar only up to 11:00
    sv.clock.set(datetime.fromisoformat("2025-03-18T15:45:00+05:30"))
    mock_server.configure(now="2025-03-18T15:45:00+05:30")
    j = wait_job(sv, sv.jobs.enqueue_download([iid], ["5m"], mode="incremental").job_id)
    assert j["requests"] == 1
    assert checksum_frame(stored(sv, iid, "5m")) == checksum_frame(oracle("PROV", "NSE", "5m", date(2025, 3, 17),
                                                                          date(2025, 3, 18)))


def test_outage_gap_fully_recovered(make_services):                                               # E2E-17 (CP-03)
    sv = make_services(now="2025-03-07T16:00:00+05:30")
    iid = add_instrument(sv, "GAP")
    wait_job(sv, sv.jobs.enqueue_download([iid], ["5m"], mode="fixed", start=date(2025, 3, 3),
                                          end=date(2025, 3, 7)).job_id)
    sv.clock.set(datetime.fromisoformat("2025-03-28T16:00:00+05:30"))
    wait_job(sv, sv.jobs.enqueue_download([iid], ["5m"], mode="incremental").job_id)
    assert checksum_frame(stored(sv, iid, "5m")) == checksum_frame(oracle("GAP", "NSE", "5m", date(2025, 3, 3),
                                                                         date(2025, 3, 28)))


def test_post_close_stores_todays_final_daily_bar(make_services, mock_server):                  # E2E-18 (CP-04)
    sv = make_services(now="2025-03-17T16:00:00+05:30")
    iid = add_instrument(sv, "DAILY")
    wait_job(sv, sv.jobs.enqueue_download([iid], ["D"], mode="fixed", start=date(2025, 3, 3),
                                          end=date(2025, 3, 17)).job_id)
    for when, expect_tasks in (("2025-03-18T15:44:00+05:30", 0), ("2025-03-18T15:45:00+05:30", 1)):
        sv.clock.set(datetime.fromisoformat(when))
        mock_server.configure(now=when)
        res = sv.jobs.enqueue_download([iid], ["D"], mode="incremental", origin="schedule:test")
        assert res.tasks == expect_tasks
        if res.tasks:
            wait_job(sv, res.job_id)
    last = stored(sv, iid, "D")["ts"].max()
    assert ist_date_of_epoch(int(last)) == date(2025, 3, 18)
    with sv.meta.session() as s:
        st = s.get(SyncState, {"instrument_id": iid, "interval_id": resolve("D").id})
        assert st.last_final_ts == last                              # final at 15:45 -> no re-fetch needed
    assert sv.jobs.enqueue_download([iid], ["D"], mode="incremental").tasks == 0


@pytest.mark.parametrize("conv", ["ist_midnight", "utc_midnight", "ist_open"])
def test_daily_conventions_store_trading_dates(make_services, mock_server, conv):              # E2E-19
    sv = make_services()
    mock_server.configure(daily_ts=conv)
    iid = add_instrument(sv, "CONV")
    wait_job(sv, sv.jobs.enqueue_download([iid], ["D"], mode="fixed", start=date(2025, 1, 1),
                                          end=date(2025, 1, 31)).job_id)
    mock_server.configure(daily_ts="ist_midnight")
    assert checksum_frame(stored(sv, iid, "D")) == checksum_frame(oracle("CONV", "NSE", "D", date(2025, 1, 1),
                                                                        date(2025, 1, 31)))


def test_priorities_interactive_before_backfill(make_services, mock_server):
    sv = make_services(start=False)
    sv.settings.set("parallel_downloads", 1, "integer")           # one worker: request order == dispatch order
    sv.settings_changed()
    ids = [add_instrument(sv, f"B{i}") for i in range(6)]
    hot = add_instrument(sv, "HOT")
    sv.jobs.enqueue_download(ids, ["D"], mode="fixed", start=date(2025, 3, 3), end=date(2025, 3, 7),
                             priority="backfill")
    sv.jobs.enqueue_download([hot], ["D"], mode="fixed", start=date(2025, 3, 3), end=date(2025, 3, 7),
                             priority="interactive")
    sv.engine.start()
    deadline = time.monotonic() + 30
    while len(mock_server.request_log) < 7 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert mock_server.request_log[0]["symbol"] == "HOT"


def test_rate_window_survives_restart(make_services, mock_server):
    sv = make_services(name="rate")
    ids = [add_instrument(sv, f"W{i}") for i in range(5)]
    wait_job(sv, sv.jobs.enqueue_download(ids, ["D"], mode="fixed", start=date(2025, 3, 3),
                                          end=date(2025, 3, 7)).job_id)
    used = sv.engine.rate_snapshot()["hour_used"]
    assert used >= 5
    sv.engine.stop()
    from tradealpha.marketdata.providers.openalgo.client import reset_providers
    reset_providers()                                               # new process: fresh in-memory limiter
    assert sv.engine.rate_snapshot()["hour_used"] == 0
    sv.engine.start()                                               # loads instance/rate_state.json
    assert sv.engine.rate_snapshot()["hour_used"] == used


def test_follow_live_downloads_each_new_bar_during_the_session(make_services, mock_server):   # D2/D7 live charts
    from datetime import datetime as _dt

    from tradealpha.platform.time.timeutil import IST
    sv = make_services(now="2025-03-28T11:02:00+05:30")
    iid = add_instrument(sv, "SBIN")
    wait_job(sv, sv.jobs.enqueue_download([iid], ["5m"], mode="fixed", start=date(2025, 3, 27),
                                          end=date(2025, 3, 28)).job_id)
    last_final = lambda: sv.store.read_range(iid, 3)["ts"].max()                        # noqa: E731
    assert _dt.fromtimestamp(last_final(), IST).strftime("%H:%M") == "11:00"          # forming bar stored too
    sv.follow.follow(iid, "5m")
    assert sv.follow.tick() == []                                       # 11:00 bar not final before 11:06
    sv.clock.set(_dt.fromisoformat("2025-03-28T11:06:30+05:30"))
    mock_server.configure(now="2025-03-28T11:06:30+05:30")
    jobs = sv.follow.tick()
    assert len(jobs) == 1
    j = wait_job(sv, jobs[0])
    assert j["status"] == "succeeded" and j["priority"] == "interactive" and j["origin"] == "follow"
    assert _dt.fromtimestamp(last_final(), IST).strftime("%H:%M") == "11:05"
    assert sv.follow.tick() == []                                       # nothing new until the 11:05 bar is final
    sv.clock.set(_dt.fromisoformat("2025-03-31T11:00:00+05:30"))        # Id-ul-Fitr: exchange closed
    assert sv.follow.tick() == []
    assert sv.follow.list()[0]["jobs_enqueued"] == 1
