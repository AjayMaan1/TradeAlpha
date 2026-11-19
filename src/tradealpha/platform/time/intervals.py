"""Interval catalogue. IDs are stable and persisted (meta DB ``intervals`` table, bar store ``interval_id``)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IntervalSpec:
    id: int
    code: str          # canonical OpenAlgo code
    seconds: int
    is_native: bool    # False -> derived from D by resampling (W, M); never requested from the provider
    label: str

    @property
    def is_intraday(self) -> bool:
        return self.seconds < 86_400


INTERVALS: dict[str, IntervalSpec] = {
    s.code: s
    for s in (
        IntervalSpec(1, "1m", 60, True, "1 Minute"),
        IntervalSpec(2, "3m", 180, True, "3 Minutes"),
        IntervalSpec(3, "5m", 300, True, "5 Minutes"),
        IntervalSpec(4, "10m", 600, True, "10 Minutes"),
        IntervalSpec(5, "15m", 900, True, "15 Minutes"),
        IntervalSpec(6, "30m", 1_800, True, "30 Minutes"),
        IntervalSpec(7, "1h", 3_600, True, "1 Hour"),
        IntervalSpec(8, "D", 86_400, True, "Daily"),
        IntervalSpec(9, "W", 604_800, False, "Weekly (derived from D)"),
        IntervalSpec(10, "M", 2_592_000, False, "Monthly (derived from D)"),
    )
}
BY_ID = {s.id: s for s in INTERVALS.values()}

# Accepted spellings -> canonical code. Note '1m' is one minute; monthly is only 'M'.
_ALIASES = {"1d": "D", "d": "D", "day": "D", "1w": "W", "w": "W", "week": "W", "month": "M", "60m": "1h"}


class UnsupportedInterval(ValueError):
    pass


def resolve(code: str) -> IntervalSpec:
    if code in INTERVALS:
        return INTERVALS[code]
    canon = _ALIASES.get(code, _ALIASES.get(str(code).lower()))
    if canon:
        return INTERVALS[canon]
    raise UnsupportedInterval(f"Unsupported interval '{code}'. Supported: {', '.join(INTERVALS)}")


def native_codes() -> list[str]:
    return [c for c, s in INTERVALS.items() if s.is_native]
