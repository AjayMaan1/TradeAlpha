# Phase 4 (live readiness) + Phase 5 (pilot report) - what was built, how to use it

Decisions: [ADR-004](adr/ADR-004-live-route-and-two-nodes.md) · Operations: [OPERATIONS_LIVE.md](OPERATIONS_LIVE.md) ·
Security: [SECURITY_LIVE.md](SECURITY_LIVE.md) · Tests: [TEST_REPORT_PHASE4.md](TEST_REPORT_PHASE4.md)

## 1. The flow, end to end

```
idea engine ──signed IdeaSpec──▶ RESEARCH node (HISTORIFY_ROLE=research)
                                   validate → evaluate (backtest, WFO, sweep, DSR/PBO) → deploy
                                   signals → shadow   (simulated, the research gates; orchestrator)
                                   export bundle ?promote_to=paper  (runs the shadow→paper gate, signs)
                                         │
                                         ▼
                                 TRADER node (HISTORIFY_ROLE=trader)
                                   import bundle (hash, HMAC, IR hash) → paper on the OpenAlgo Analyzer
                                   nightly parity + broker reconciliation · drills · T0 sign-off
                                   paper → eligible_live (gate + explicit approval)
                                   eligible_live → live (live gate, typed name; never forced)
                                      dry_run session(s): journaled, DRY-<id>, never sent
                                      live session: OpenAlgo, Analyzer OFF, margin/funds per entry, step caps
                                   EOD reconciliation (books) + contract note (charges) → pilot report
                                   automatic demotion on differences / rejects / loss stops / kills
                                         │ TradeEvents (HMAC relay, exactly once, chain re-verified)
                                         ▼
                                 RESEARCH node: /api/v2/trade-events (origin = trader node) → analysis
```

`scripts/e2e_trading_flow.py` runs exactly this on a mock OpenAlgo (22 steps; `bash scripts/run_tests.sh e2e-flow`).

## 2. What each must-have became

| # | Built | Where |
|---|---|---|
| L1 | `LiveRoute` (Analyzer OFF at every order), `DryRunRoute`; the live stage of a hosted deployment: dry_run → live by the host watch; live gate = P6b gate + live drills + NTP + tariffs + reconciliation record (unforceable); stepped caps (`signals.live.steps`, G-PRE-8 orders/day); `live_step` (one up after clean sessions, down any time); automatic + manual demotion | `app/lab/paper/live_route.py`, `app/lab/live/host.py`, `live.py`, `promote.py`, `paper/guardrails.py` |
| L2 | `decision_source: "ws"` sessions: final WebSocket 1m bars → interval buckets (session-open aligned) → decision on the bucket's last minute; partial buckets left to REST; `lab_paper_bars.source`; parity class `data_source`; decision-latency + feed-lag histograms (`/metrics`); full recompute kept (ADR-004 §7, perf budget test) | `app/lab/paper/service.py` (`on_stream_bar`), `latency.py`, `parity.py`, `core/stream/service.py`, `tests/lab/test_perf_live.py` |
| L3 | `HISTORIFY_ROLE` (all / research / trader) with enforced refusals; signed deployment bundles (`historify trader export/import`, API); trade-event ingest with origin / remote id (unique), chain re-verification, gap refusal; node id | `app/core/roles.py`, `app/lab/live/bundle.py`, `app/lab/ideas/events.py` (`ingest`), migration 0021 |
| L4 | SNTP clock check (`ntp_server`) → `PaperService.clock_offset_fn` → G-SYS-3; live drills `broker_down`, `ws_drop`, `disk_full`, `clock_skew` on the production OpenAlgo route against an in-process account (`fake_broker.FakeOpenAlgo`); deploy files (systemd × 2, env examples, nginx, chrony, cron, Prometheus alert rules) | `app/core/clock_check.py`, `app/lab/paper/drills.py`, `app/lab/paper/fake_broker.py`, `deploy/` |
| L5 | broker margin (`/margin`) + funds (`/funds`) asked before every live entry (G-PRE-6, 20 % headroom); unknown = refused | `live_route.py`, `service.py` (`_order_ctx`), `guardrails.py` |
| L6 | EOD reconciliation per broker-routed session-day (trade book qty/avg price per order id, unknown tagged orders, session net position, funds snapshot); contract-note CSV import (aliases, idempotent per trade no.); charges vs the cost model (warning); live fees = note charges; `broker_reconciliations` + TradeEvent `reconciliation` + Data Health + notification; nightly in the paper loop | `app/lab/live/reconcile.py`, migration 0021 |
| L7 | audit-log hash chain (one sealer, verify, daily anchor TradeEvent, truncation detection); secret rotation with grace (intake accepts old+new; relay sends both signatures); API-token rotation (expiry); order-route token rotation | `app/lab/live/security.py`, `app/core/auth.py`, `app/lab/live/service.py` |
| S1 | TradeEvent retention (`ideas.retention_days`): prune relayed + old events, chain anchor kept, `verify_chain` continues from it | `events.apply_retention` |
| S3 | idea verdict notifications (invalid / rejected / accepted / deployed / retired) on the one bus (`ideas.notify`) | `IdeaService._notify_state` |
| P5 | pilot report: routes (paper / dry_run / live) days, orders, fills, rejects, fees, realised net, daily P&L, guardrail events; live vs paper; execution quality; reconciliation record; step history; verdict | `HostMixin.pilot_report` |

## 3. Configuration (settings)

| Key | Meaning |
|---|---|
| `signals.live` | `enabled`, `max_total_notional`, `dry_run_sessions` (1), `steps` ([{max_order_notional, max_gross, max_orders_per_day}] ×3), `step_min_sessions` (5), `max_rejects_per_day` (3), `demote_flatten` (true) |
| `ntp_server` | `host[:port]` for the clock check; unset = not measured (live gate fails) |
| `ideas.retention_days` | null = keep every TradeEvent; 30..3650 = prune behind an anchor |
| `ideas.notify` | idea verdict notifications (true) |
| `trade_events_webhook_secret` | the node-link secret: signs bundles, relay batches, ingest (the same value on both nodes) |
| `node_id` | generated once; the origin of relayed events |
| env `HISTORIFY_ROLE` | `all` (default) · `research` · `trader` |

## 4. API and CLI

API (`X-Historify-Token` = order-route token where it can cause orders or reveals secrets):

```
POST /api/v2/signals/deployments/<id>/live-step {step, confirm}        token
POST /api/v2/signals/deployments/<id>/demote {reason}                  token
GET  /api/v2/signals/deployments/<id>/pilot-report
GET  /api/v2/signals/deployments/<id>/bundle[?promote_to=paper]        token
POST /api/v2/signals/bundles/import {bundle}                           token
POST /api/v2/signals/token/rotate {grace_h}                            token
GET  /api/v2/trading/reconciliations?deployment=&session=
POST /api/v2/trading/reconciliations/run {day?, session?}              token
POST /api/v2/trading/contract-notes   (text/csv | multipart file | {csv})   token
GET  /api/v2/security/audit/verify · POST /api/v2/security/audit/anchor (token)
GET  /api/v2/security/secrets · POST /api/v2/security/secrets/rotate {name, value, grace_h}   token
POST /api/v2/auth/tokens/<id>/rotate {grace_h}                         (your own token)
POST /api/v2/trade-events/ingest                                       HMAC (X-Historify-Signature)
GET  /api/v2/system/clock · POST /api/v2/system/clock/check · GET /api/v2/system/role
POST /api/v2/lab/paper/drills {"live": true}                           all drills incl. the live ones
```

CLI (`flask --app run.py historify ...`, server stopped - DuckDB is single-writer):

```
trader export <dep> --out b.json [--promote-to paper] · trader import b.json · trader role
live reconcile [--day D] [--session S] · live contract-note note.csv · live audit-verify
live rotate-secret <name> [--grace-h 24] · live rotate-token [--grace-h 1] · live clock · live pilot-report <dep>
lab drills run [--live] [--only broker_down ...]
```

## 5. Schema (migration 0021)
`api_tokens.expires_at` · `audit_log.chain_sha` · `lab_paper_bars.source` · `trade_events.origin`, `remote_id`
(unique together) · `broker_reconciliations` (session × day) · `contract_note_trades` (day × order × trade no.).

## 6. Broker pacing of the order route
Every route to the same account shares one provider, the profile's per-kind limiters (orders: the profile's order
windows capped at SEBI's 10/s; books - order / trade / position book, funds: the profile's book windows, Angel One
1 req/s; everything else: quote windows) and a book cache valid for one book-limiter interval (invalidated by any
order call). N running sessions therefore read the account's books once per interval, not 3N times.

## 7. Known limits (honest)
- The broker's books are the current day's: a past day can only be reconciled against its contract note.
- Position checks are per session (its own broker trades); one live session per symbol and account is the
  supported topology (portfolio caps keep ideas from stacking the same symbol).
- `refused` broker calls leave the order `sent` (unknown) - never re-sent blindly; the operator resolves it
  (reconciliation shows it). This is deliberate (duplicate orders are worse than a missed one).
- The T0 sign-off is the flagfix1 checklist; the e2e records it directly (its replica / snapshot / cohort items are
  research-programme items outside the flow).
- WebSocket-built bars can miss a middle minute (a partial bar that is not the last minute): the decision is made and
  parity flags it as `data_source`.
