# ADR-004: The live route for ideas, two nodes, and the broker as the truth

Status: accepted (trading-engine Phase 4). Supersedes decision 3 of ADR-002 ("hosted deployments: live refused").

## Context
After Phases 2–3 an idea flowed from the idea engine to paper trading on the OpenAlgo Analyzer, and its trade data
flowed back as TradeEvents. Nothing could send a real order for an idea (FEATURE_AUDIT L1), the engine could not
prove it agreed with the broker (L6), the order path shared a process with research (L3), guardrail G-SYS-3 had no
clock source (L4), and the audit log was not tamper-evident (L7).

## Decisions

1. **One live route, the same host.** A hosted (IR) deployment promoted to `live` keeps running on the hbt per-bar
   host; only the route behind the OMS changes: `dry_run` (journaled, acknowledged locally as `DRY-<id>`, never sent)
   for the first `signals.live.dry_run_sessions` sessions, then `LiveRoute` (OpenAlgo, **Analyzer must be OFF at the
   moment of every order** - the mirror of the Analyzer route's rule). No second live engine, no second OMS.
2. **The live gate is the P6b gate plus the hbt Lab's operations, never forceable**: global switch (typed phrase,
   auth, global cap), complete deployment limits, Analyzer OFF, typed deployment name, the paper drills + a T0
   sign-off, the **live drills** (broker down, WebSocket drop, disk full, clock skew) within 30 days, a **measured
   clock** (SNTP, |offset| ≤ 1 s), verified tariffs (D-13), and a clean broker reconciliation record (≥ 1 ok, 0
   diverged) from the paper stage.
3. **Stepped caps are session guardrails.** `signals.live.steps` (max order notional, max gross, max orders per day -
   new guardrail G-PRE-8) start at step 1; up one step only after `step_min_sessions` clean live sessions and no
   reconciliation difference, down any time; typed confirmation; audited; TradeEvent.
4. **Automatic demotion** (live → eligible_live, paused, session killed and flattened unless `demote_flatten` is
   off, notification, TradeEvent, audit) on: a logic parity divergence, a diverged broker reconciliation,
   `max_rejects_per_day` broker rejects, a G-IN-2 / G-IN-3 loss stop, a killed live session. Re-promotion goes through
   the live gate again.
5. **Margin is asked, not estimated.** On the live route every entry asks the broker's margin for that order and the
   available funds (G-PRE-6, 20 % headroom); when the broker cannot tell, the entry is refused (`margin_unknown`).
6. **The broker is the truth.** After the close every broker-routed session-day is reconciled against the trade
   book, order book (orders carrying the session's tag) and the session's net position; an imported contract note is
   the legal record of prices and charges (live fills take its charges as their fees; a charges difference is a
   tariff WARNING, a quantity/price difference is a DIFFERENCE). One row per session-day, a TradeEvent, a Data Health
   issue and a notification on differences; a diverged live day demotes.
7. **Full recompute per bar, not an incremental step API.** The host re-runs hbt on the member's as-seen history on
   every bar. Measured: p95 46–65 ms, p99 < 0.1 s for 1,500 bars of history on a 300 s bar (tests/lab/
   test_perf_live.py pins p95 ≤ 0.5 s). A `step_bar` API would duplicate the kernel's state machine in a second
   code path and break parity-by-construction for a latency win the bar interval does not need. Revisit only if the
   measured p95 approaches 10 % of the shortest traded interval.
8. **WebSocket decisions, REST record.** A session may decide on interval bars aggregated from the WebSocket's final
   1m bars (`decision_source: "ws"`, seconds after the close). A bucket whose last minute did not stream is never
   decided from the stream (no partial bars): the downloaded bar decides it. The downloaded bar stays the record;
   nightly parity classes a tick-built vs downloaded difference as `data_source` (reported, not blocking).
9. **Two nodes, one code base.** `HISTORIFY_ROLE=research` evaluates ideas and runs the simulated stages and can
   never route an order (order stages refused, broker routes refused); `HISTORIFY_ROLE=trader` runs the order path
   only and refuses ideas. A deployment crosses as a **signed bundle** (sha256 + HMAC with the node-link secret; the
   IR re-compiled and its hash re-checked on import; same deployment id; stage capped at eligible_live - live is
   always promoted on the trader, on its own evidence). The research node can run the shadow → paper gate itself
   and ship the deployment in `paper`. Trade data returns through the existing relay to
   `POST /api/v2/trade-events/ingest` (HMAC, exactly once per (origin, remote id), the trader's chain re-verified
   event by event; a gap refuses the batch).
10. **Tamper evidence everywhere.** The audit log is hash-chained by ONE sealer (no forks under concurrent writers),
    its head is published daily as a TradeEvent `audit.anchor` (copied off-box by the research node), and
    `verify` detects edits, deletions and truncation below the anchor. Shared HMAC secrets, API tokens and the
    order-route token rotate with a grace window.

## Consequences
- Live orders for ideas exist, behind more gates than paper, with automatic retreat.
- The pilot's exit criterion ("0 unexplained differences") is measured by code, daily.
- The order path is isolatable on its own VM (deploy/); research load cannot stall it.
- Still owner work (FEATURE_AUDIT L8): verify the tariffs on a real contract note, SEBI algo obligations, ≥ 20 paper
  sessions per idea, 10 real Analyzer sessions with 100 % reconciliation.
