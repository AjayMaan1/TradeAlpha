"""Phase 4 L2 latency budget: the per-bar host recomputes a member's as-seen history on every bar (hbt full
recompute - parity with the backtest by construction). This pins the measured cost of that choice: on 20 warm-up
sessions of 5m bars (1,500 bars) plus the replayed days, the decision latency (bar arrival -> decision journaled and
orders progressed) must stay far below the bar interval. Budget: p95 <= 0.5 s, p99 <= 1.0 s for a 300 s bar.
See docs/archive/PHASE4_LIVE.md ("why not an incremental step API")."""
from __future__ import annotations

import pytest

from tradealpha.lifecycle.gates.drills import scenario

pytestmark = [pytest.mark.perf, pytest.mark.trading_mock_matching]


def test_decision_latency_budget_full_recompute(make_services):
    sv = make_services(now="2025-01-01T10:00:00+05:30", name="lat")
    sid, replay, _, _ = scenario(sv, warm_days=20, replay_days=2, session_name="latency")
    sv.lab_paper.replay(sid, replay)
    q = sv.lab_paper.latency_book.snapshot()["decide_s"]["rest"]
    print("decision latency (s):", q)
    assert q["n"] == sum(len(df) for df in replay.values())
    assert q["p95"] <= 0.5 and q["p99"] <= 1.0, q
    lag = sv.lab_paper.latency_book.snapshot()["feed_lag_s"]["rest"]
    assert lag["p50"] == pytest.approx(2.0, abs=0.01)          # the replay harness delivers bars 2 s after the close
