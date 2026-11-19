"""
One compute budget for every research worker (unification U0 / D6).

Two dispatchers came from the two fixes: the hbt Lab's ``LabDispatcher`` (one process per job) and the P3
``LabRunner`` (a process pool with sweep fan-out). Each sized itself on its own, so together they ran 2 + 3 workers
on a 4-core machine, and the P3 pool had neither a lower priority nor back-pressure while downloads run. Both now
take their worker slots from this budget:

* slots: setting ``lab_workers`` (default cores - 1, at least 1) - the ONE worker setting (unification U5: the P3
  ``backtest.workers`` is an alias of it);
* while a download job is running: ``lab_workers_during_downloads`` (default 1) - downloads keep their CPU share;
* every worker process runs at below-normal priority (``lower_priority``, POSIX nice 10 / Windows BELOW_NORMAL).

The limit is evaluated on every acquire, so a download that starts throttles new work immediately; running work is
never interrupted.
"""
from __future__ import annotations

import os
import threading
import time

from sqlalchemy import select


def lower_priority() -> None:
    """Process initializer: below-normal CPU priority (downloads, the UI and the paper host come first)."""
    try:
        os.nice(10)
        return
    except (AttributeError, OSError):
        pass
    try:                                                     # Windows
        import ctypes
        k32 = ctypes.windll.kernel32                         # type: ignore[attr-defined]
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00004000)   # BELOW_NORMAL_PRIORITY_CLASS
    except Exception:                                        # pragma: no cover - best effort
        pass


class ComputeBudget:
    def __init__(self, services):
        self.sv = services
        self._cv = threading.Condition()
        self.in_use = 0
        self.peak = 0

    def _setting(self, key: str, default):
        try:
            v = self.sv.settings.get(key, None)
        except Exception:                                    # pragma: no cover - settings unavailable at shutdown
            v = None
        return default if v in (None, "") else v

    def downloading(self) -> bool:
        from tradealpha.platform.db.models import Job
        try:
            with self.sv.meta.session() as s:
                return s.scalar(select(Job.id).where(Job.kind == "download", Job.status == "running").limit(1)) \
                    is not None
        except Exception:                                    # pragma: no cover
            return False

    def limit(self) -> int:
        total = int(self._setting("lab_workers", max((os.cpu_count() or 2) - 1, 1)))
        if self.downloading():
            total = min(total, int(self._setting("lab_workers_during_downloads", 1)))
        return max(1, total)

    def try_acquire(self) -> bool:
        with self._cv:
            if self.in_use < self.limit():
                self.in_use += 1
                self.peak = max(self.peak, self.in_use)
                return True
            return False

    def acquire(self, stop=None, timeout: float | None = None) -> bool:
        """Block until a slot is free. ``stop()`` -> True aborts (returns False)."""
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            if self.try_acquire():
                return True
            if stop is not None and stop():
                return False
            if end is not None and time.monotonic() >= end:
                return False
            with self._cv:
                self._cv.wait(0.25)

    def release(self) -> None:
        with self._cv:
            self.in_use = max(0, self.in_use - 1)
            self._cv.notify_all()

    def status(self) -> dict:
        return {"in_use": self.in_use, "limit": self.limit(), "peak": self.peak}
