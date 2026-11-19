"""
FollowService: "Follow live" for charts (decisions D2/D7).

While a followed series' exchange session is open, an incremental job (priority=interactive, de-duplicated per
series) is enqueued as soon as a new bar can be final (bar close + 60 s). Max 5 followed series; a registration
expires when the browser stops sending heartbeats (TTL 120 s). No broker calls outside the session.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from sqlalchemy import select

from tradealpha.marketdata.bars.rollup import download_spec
from tradealpha.marketdata.ingestion.planner import SeriesRef, SyncSnapshot, incremental_window, latest_final_possible
from tradealpha.platform.db.models import Instrument, SyncState
from tradealpha.platform.jobs.periodic import start_task
from tradealpha.platform.time.intervals import resolve
from tradealpha.platform.time.timeutil import IST

log = logging.getLogger(__name__)


class FollowLimitError(ValueError):
    pass


@dataclass
class Follow:
    instrument_id: int
    interval: str
    expires_at: float
    jobs_enqueued: int = 0
    last_job: str | None = None
    created_at: float = field(default_factory=time.monotonic)


class FollowService:
    TTL_S = 120.0

    def __init__(self, meta, jobs, calendar_provider, config_provider, clock, tick_s: float = 5.0):
        self.meta, self.jobs = meta, jobs
        self._calendar, self._config, self._clock = calendar_provider, config_provider, clock
        self._tick_s = tick_s
        self._follows: dict[tuple[int, str], Follow] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ registry
    def follow(self, instrument_id: int, interval: str) -> dict:
        spec = resolve(interval)
        if not spec.is_native:
            raise ValueError(f"{spec.code} is derived from daily data; follow the daily series instead")
        key = (int(instrument_id), spec.code)
        limit = self._config().follow_max_series
        with self._lock:
            self._expire()
            if key not in self._follows and len(self._follows) >= limit:
                raise FollowLimitError(f"you can follow at most {limit} series at a time")
            f = self._follows.get(key) or Follow(key[0], spec.code, 0)
            f.expires_at = time.monotonic() + self.TTL_S
            self._follows[key] = f
        self.tick()                                 # immediate catch-up when a chart starts following
        return self.describe(key)

    def unfollow(self, instrument_id: int, interval: str) -> None:
        with self._lock:
            self._follows.pop((int(instrument_id), resolve(interval).code), None)

    def list(self) -> list[dict]:
        with self._lock:
            self._expire()
            keys = list(self._follows)
        return [self.describe(k) for k in keys]

    def describe(self, key) -> dict:
        f = self._follows.get(key)
        return {"instrument_id": key[0], "interval": key[1], "active": f is not None,
                "jobs_enqueued": f.jobs_enqueued if f else 0, "last_job": f.last_job if f else None,
                "max": self._config().follow_max_series}

    def _expire(self):
        now = time.monotonic()
        for k in [k for k, f in self._follows.items() if f.expires_at < now]:
            del self._follows[k]

    # ------------------------------------------------------------------ ticking
    def start(self):
        if self._thread:
            return
        self._stop.clear()
        self._thread = start_task("follow", self.tick, lambda: self._tick_s, stop=self._stop)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(5)
            self._thread = None

    def tick(self) -> list[str]:
        with self._lock:
            self._expire()
            items = list(self._follows.items())
        if not items or not self._config().configured:
            return []
        cal, now = self._calendar(), self._clock.now()
        enqueued = []
        with self.meta.session() as s:
            insts = {i.id: i for i in s.scalars(select(Instrument).where(
                Instrument.id.in_([k[0] for k, _ in items])))}
            for key, f in items:
                inst = insts.get(key[0])
                if inst is None:
                    continue
                spec = resolve(key[1])
                dl = download_spec(spec.code, self._config())    # roll-up mode: 5m is served from 1m
                session = cal.session_on(inst.exchange, now.astimezone(IST).date())
                if session is None:
                    continue                                      # holiday/weekend: nothing to follow
                st = s.get(SyncState, {"instrument_id": inst.id, "interval_id": dl.id})
                snap = SyncSnapshot(st.first_bar_ts, st.last_bar_ts, st.last_final_ts) if st else None
                if dl.id == spec.id:
                    if incremental_window(SeriesRef(inst.id, inst.exchange, spec), snap, cal, now) is None:
                        continue                                  # no new final bar yet
                else:                                             # one 1m request per completed 5m bucket
                    possible = latest_final_possible(cal, inst.exchange, spec, now)
                    if possible is None or (snap and snap.last_final_ts is not None and
                                            snap.last_final_ts >= possible + spec.seconds - dl.seconds):
                        continue
                res = self.jobs.enqueue_download([inst.id], [spec.code], mode="incremental", origin="follow",
                                                 priority="interactive", dedupe=True,
                                                 label=f"Follow {inst.symbol} · {spec.code}")
                if res.job_id:
                    f.jobs_enqueued += 1
                    f.last_job = res.job_id
                    enqueued.append(res.job_id)
        return enqueued
