"""Injectable clock so that market-hours, session-expiry and scheduler logic can be tested with time travel."""
from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta


class SystemClock:
    """Real wall clock (timezone-aware UTC)."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """Manually controlled clock for tests."""

    def __init__(self, start: datetime):
        if start.tzinfo is None:
            raise ValueError("FrozenClock needs a timezone-aware datetime")
        self._now = start.astimezone(UTC)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def set(self, when: datetime) -> None:
        with self._lock:
            self._now = when.astimezone(UTC)

    def advance(self, **kwargs) -> None:
        with self._lock:
            self._now += timedelta(**kwargs)


_clock = SystemClock()


def get_clock():
    return _clock


def set_clock(clock) -> None:
    """Replace the process-wide clock (tests only)."""
    global _clock
    _clock = clock
