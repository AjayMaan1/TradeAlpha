"""Multi-window limiter (DL-09) with a simulated clock: never exceeds any broker window."""
import threading

import pytest

from tradealpha.platform.ratelimit.limiter import MultiWindowRateLimiter, RateLimitTimeout

ANGEL = [(3, 1.0), (180, 60.0), (5000, 3600.0)]


class FakeTime:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _run(limiter, clock, n):
    stamps = []
    for _ in range(n):
        limiter.acquire()
        stamps.append(clock.t)
    return stamps


def _max_in_window(stamps, window):
    best, j = 0, 0
    for i, t in enumerate(stamps):
        while stamps[j] <= t - window:
            j += 1
        best = max(best, i - j + 1)
    return best


def test_angel_one_windows_respected_at_80_percent_budget():
    clock = FakeTime()
    lim = MultiWindowRateLimiter(ANGEL, budget_share=0.8, monotonic=clock.now, sleep=clock.sleep)
    stamps = _run(lim, clock, 4500)
    assert _max_in_window(stamps, 1.0) <= 3
    assert _max_in_window(stamps, 60.0) <= 144
    assert _max_in_window(stamps, 3600.0) <= 4000
    # sustained: first 4000 calls at 2.4/s, then the hourly cap forces a wait
    assert stamps[3999] - stamps[0] == pytest.approx(3999 / 2.4, rel=0.01)
    assert stamps[4000] - stamps[0] >= 3600


def test_full_budget_rate():
    clock = FakeTime()
    lim = MultiWindowRateLimiter(ANGEL, budget_share=1.0, monotonic=clock.now, sleep=clock.sleep)
    stamps = _run(lim, clock, 300)
    assert _max_in_window(stamps, 1.0) <= 3
    assert _max_in_window(stamps, 60.0) <= 180


def test_block_for_retry_after():
    clock = FakeTime()
    lim = MultiWindowRateLimiter([(10, 1.0)], monotonic=clock.now, sleep=clock.sleep)
    lim.acquire()
    lim.block_for(5.0)
    start = clock.t
    lim.acquire()
    assert clock.t - start >= 5.0


def test_timeout():
    clock = FakeTime()
    lim = MultiWindowRateLimiter([(1, 10.0)], monotonic=clock.now, sleep=clock.sleep)
    lim.acquire()
    with pytest.raises(RateLimitTimeout):
        lim.acquire(timeout=1.0)


def test_thread_safety_real_clock():
    # the huge 1 h window never binds (pacing comes from the 50/s window) but keeps every grant in the limiter's
    # history: with a 1 s horizon, how many grants survive pruning depended on thread scheduling (was intermittent)
    lim = MultiWindowRateLimiter([(50, 1.0), (100_000, 3600.0)], budget_share=1.0)
    stamps, lock = [], threading.Lock()

    def worker():
        for _ in range(10):
            lim.acquire()
            import time
            with lock:
                stamps.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    stamps.sort()
    assert len(stamps) == 50
    assert _max_in_window(stamps, 1.0) <= 50
    # pacing is asserted on the limiter's own grant times: stamps taken after acquire() returns include thread
    # scheduling jitter (a descheduled thread records late), which made this test flaky under CPU load
    grants = sorted(lim._calls)
    assert lim.total_calls == 50 and len(grants) == 50
    assert _max_in_window(grants, 1.0) <= 50
    assert min(b - a for a, b in zip(grants, grants[1:], strict=False)) >= 1 / 50 - 1e-6
