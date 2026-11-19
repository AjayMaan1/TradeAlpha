"""
Decision latency histograms (trading-engine Phase 4, L2) - in memory, per bar source (rest | ws):

    decide_s     wall time from a bar's arrival at the host to its decision journaled and its orders progressed
                 (hbt recomputes the member's as-seen history on every bar - see docs/archive/PHASE4_LIVE.md on why a full
                 recompute is kept instead of an incremental step API: parity by construction, and the measured cost
                 is far below the bar interval; ``tests/perf`` holds the budget)
    feed_lag_s   bar end -> seen by the host (REST download or WebSocket-built bar)

Exposed as Prometheus histograms on /metrics (``historify_decision_seconds``, ``historify_bar_feed_lag_seconds``)
and as quantiles in the paper status.
"""
from __future__ import annotations

import threading

DECIDE_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
LAG_BUCKETS = (0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0, 300.0)


class Histogram:
    def __init__(self, buckets):
        self.buckets = tuple(buckets)
        self.counts = [0] * (len(self.buckets) + 1)
        self.sum = 0.0
        self.n = 0
        self.recent: list[float] = []

    def observe(self, v: float) -> None:
        v = max(0.0, float(v))
        i = next((k for k, b in enumerate(self.buckets) if v <= b), len(self.buckets))
        self.counts[i] += 1
        self.sum += v
        self.n += 1
        self.recent.append(v)
        if len(self.recent) > 2000:
            del self.recent[:1000]

    def quantiles(self) -> dict:
        if not self.recent:
            return {"n": self.n, "p50": None, "p95": None, "p99": None, "max": None}
        xs = sorted(self.recent)
        pick = lambda q: round(xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))], 4)   # noqa: E731
        return {"n": self.n, "p50": pick(0.5), "p95": pick(0.95), "p99": pick(0.99), "max": round(xs[-1], 4)}


class LatencyBook:
    def __init__(self):
        self._lock = threading.Lock()
        self.decide: dict[str, Histogram] = {}
        self.lag: dict[str, Histogram] = {}

    def observe(self, source: str, decide_s: float | None = None, feed_lag_s: float | None = None) -> None:
        with self._lock:
            if decide_s is not None:
                self.decide.setdefault(source, Histogram(DECIDE_BUCKETS)).observe(decide_s)
            if feed_lag_s is not None:
                self.lag.setdefault(source, Histogram(LAG_BUCKETS)).observe(feed_lag_s)

    def snapshot(self) -> dict:
        with self._lock:
            return {"decide_s": {k: h.quantiles() for k, h in self.decide.items()},
                    "feed_lag_s": {k: h.quantiles() for k, h in self.lag.items()}}

    def prometheus(self) -> list[str]:
        out = []
        with self._lock:
            for metric, book, helptext in (("historify_decision_seconds", self.decide,
                                            "bar arrival -> decision journaled (per bar source)"),
                                           ("historify_bar_feed_lag_seconds", self.lag,
                                            "bar end -> seen by the paper / live host (per bar source)")):
                out += [f"# HELP {metric} {helptext}", f"# TYPE {metric} histogram"]
                for src, h in sorted(book.items()):
                    acc = 0
                    for b, c in zip(h.buckets, h.counts, strict=False):
                        acc += c
                        out.append(f'{metric}_bucket{{source="{src}",le="{b}"}} {acc}')
                    out.append(f'{metric}_bucket{{source="{src}",le="+Inf"}} {h.n}')
                    out.append(f'{metric}_sum{{source="{src}"}} {round(h.sum, 6)}')
                    out.append(f'{metric}_count{{source="{src}"}} {h.n}')
        return out
