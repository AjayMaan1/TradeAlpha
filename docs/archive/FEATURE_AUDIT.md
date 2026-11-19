# Feature audit: what the platform has, what it MUST still have, and the nice-to-haves

Audited against the mission: *ideas from the generation engine trade automatically on paper, the trade data flows
back for analysis, and - after approval - the same ideas trade live.* Date of audit: after the Phase 2 + 3 build
(`PHASE2` commit). Legend: ✅ built and tested · 🟥 **must-have before live money** · 🟨 must-have to scale / operate
comfortably · 🟦 nice-to-have (nit).

## 1. What is in place (the short inventory)

| Area | State |
|---|---|
| Data platform | ✅ downloads with rate budgets / retries / finality, bar store + roll-ups, integrity audits, quarantine, calendar (incl. special sessions), corporate actions (auto NSE + bhavcopy-verified + manual), PIT reference bundle (delivery %, index membership, events, ban lists), one lot/tick history, backups/replica/retention |
| Research | ✅ ONE backtest process: Strategy IR (causal by construction) + Python tier, Rust kernel with portfolio mode, ADR-001/003 execution model (slippage, participation, leverage, latency), order types incl. stop-limit, snapshots (content-addressed, PIT), WFO/purge, sweeps, DSR (one append-only trial registry + declared trials), PBO, Monte Carlo, regimes, hold-out lock, look-ahead gates |
| Idea intake | ✅ signed IdeaSpec v1, scoped tokens, rate limits, idempotency, lineage; validation (compile, fuzz, data catalogue, **embargo**); evaluation → hash-linked evidence bundle; claimed-vs-reproduced; capacity probe |
| Lifecycle | ✅ one deployment lifecycle for IR + Python, orchestrator (auto deploy → signals → shadow → paper; auto demote on parity/loss), drills + readiness sign-off gates, pinned versions, audit trail |
| Paper execution | ✅ per-bar host with as-seen journal, one OMS (journal-first, idempotent, no blind resend, cancel-replace), one orders table, one guardrail set (19 + portfolio caps), resting broker stops, OpenAlgo Analyzer route, nightly parity with classes, execution-quality report, one kill switch |
| Portfolio risk | ✅ v1: capital slots, per-symbol / gross caps across ideas (excess waits), global daily loss stop (pause + flatten) |
| Trade data out | ✅ TradeEvent v1 outbox (same-transaction, hash-chained), pull cursor API, signed webhook relay, nightly Parquet, restart-safe |
| Ops | ✅ one notification bus (Telegram/OpenAlgo), one follow service (REST record + WS wake), jobs/scheduler, settings registry + audit, users/sessions/tokens, kill drills, one test runner (1,300+ tests, browser, perf, Rust) |

## 2. 🟥 Must-have before live money (≈ Phase 4, ~4–5 weeks + the paper calendar)

> **Update - Phase 4 built** (see [PHASE4_LIVE.md](PHASE4_LIVE.md), [ADR-004](adr/ADR-004-live-route-and-two-nodes.md),
> [TEST_REPORT_PHASE4.md](TEST_REPORT_PHASE4.md)): L1–L7 are ✅ in code and tested (unit, service, the 22-step
> e2e trading flow, perf budget); L2's `step_bar` was replaced by a measured full-recompute budget (ADR-004 §7).
> L8 remains owner work. The Phase 5 pilot report exists (`GET .../pilot-report`).

| # | Gap | Why it blocks live | Where it lands |
|---|---|---|---|
| L1 ✅ | **Live adapter on the gateway for hosted (idea) deployments.** Today `live` is refused for them by design; the P6b live path exists only for Python replay deployments. Needs: the OpenAlgo live route behind the existing gates (global switch, auth, Analyzer-OFF check before every order, typed approval, dry-run window), stepped caps in code, automatic demotion triggers wired to it. | There is literally no way to send a live order for an idea. | `app/lab/live/live.py` + host (4–5 d) |
| L2 ✅ | **Incremental engine step (`step_bar`) + WebSocket decisions.** Paper decides on downloaded bars (~1 min cadence). Live intraday ideas need bar-close → order in seconds, with REST finality repair and a `data_source` parity class. | Latency and data-path honesty for intraday live. | Rust + host (5–6 d) |
| L3 ✅ | **Separate trader process / instance** (`historify trader`): research load (sweeps, snapshots) must not share a process, SQLite file or CPU budget with live order placement; morning warm-up; outbox back to the research node. | Isolation: a research OOM must not stall a live stop order. | new entrypoint (4 d) |
| L4 ✅ | **Production deployment & failure drills**: VPS next to OpenAlgo, static IP registered with the broker, service units, TLS/reverse proxy guide, NTP check wired into G-SYS-3 (`clock_offset_fn` is currently unset in production), drills for broker-down / WS-drop / disk-full / clock-skew. | The existing guardrail needs its data source; ops safety. | infra + docs (2–3 d) |
| L5 ✅ | **Real margin/funds checks.** G-PRE-6 exists but defaults off and uses a 20 % approximation; live needs the broker margin API per order and a funds reconciliation at open. | Rejected-at-broker orders are a live failure mode. | gateway (2 d) |
| L6 ✅ | **Contract-note reconciliation.** Daily true-up of fills, charges and positions against the broker's contract note / tradebook, with a hard alert on any unexplained difference; verify the cost schedules (D-13 is still `verified: false`). | The pilot's exit criterion is "0 unexplained differences". | new job + owner task (3 d + owner) |
| L7 ✅ | **Hash-chained audit log + security review.** The TradeEvent chain exists; the admin/audit log is not chained; token rotation and webhook-secret rotation procedures; secrets currently in the file backend on dev. | Tamper-evidence and key hygiene before money. | (2 d) |
| L8 | **Owner/regulatory tasks**: broker tariffs verified on a real contract note, SEBI algo/registration obligations confirmed, 10 real Analyzer sessions with 100 % reconciliation, the ≥ 20-session paper calendar per idea. | Non-code gates in the readiness checklist. | owner |

## 3. 🟨 Must-have to scale or operate comfortably

| # | Item | Note |
|---|---|---|
| S1 ✅ | **TradeEvent retention** (archive to Parquet + prune, keep the chain head) | an intraday idea fleet will grow `trade_events` quickly; the table is append-only today (~1 d) |
| S2 | **Evaluation queue management**: per-idea concurrency cap and priority so a burst of 50 submissions cannot starve paper-host parity jobs (the compute budget protects downloads, not job ordering) | (~1 d) |
| S3 ✅ | **Idea-level notifications** (accepted / rejected / demoted through the one bus, not only TradeEvents) | (~0.5 d) |
| S4 | **Funds/exposure dashboard** across ideas (the data exists in `/api/v2/ideas/status` + books; no single ops view) | (~1 d) |
| S5 | **Sector caps in portfolio risk** - needs a sector reference table (index membership exists; GICS-like sectors do not) | (~2 d incl. data) |
| S6 | **Token management UI** (create/scope/revoke is API/CLI only) | (~0.5 d) |
| S7 | **Backpressure on the intake** per generator (global 30/min exists; per-token quotas + queue depth limit) | (~0.5 d) |

## 4. 🟦 Nice-to-have (nits)

- Ideas page: equity-vs-band sparkline, state filters, re-evaluate button, diff between versions of an idea.
- IR `montecarlo` / `regimes` extras (Python engine has them; IR runs skip them).
- First-class IOC/validity flag at the gateway (kernel has `expiry_bars`; the host cancels at session end).
- Python strategies on the per-bar host (`step_bar`; deliberate deferral).
- One trading-mock *engine semantics* test matrix run against both calibrations (the engine is one already).
- TradeEvent replay tool (rebuild the outbox from the journals) and a consumer SDK snippet (Python client with cursor persistence).
- Multi-account / sub-book support at the gateway.
- Prometheus dashboards + alert rules shipped as files (a `/metrics` endpoint exists).
- Idea similarity detection (same idea resubmitted under a new name inflates no statistics - the registry handles N - but a lineage hint would help the UI).
- Archived-idea GC (strategy registry rows and snapshots of dead ideas).
- Webhook mTLS option; IP allow-list for `/api/v2/ideas`.

## 5. Honest limitations to keep in mind (documented by design)
- Backtest ≠ paper ≠ live will never be bit-identical: entries only when flat at the decision, MIS orders never cross sessions, limit fills are pessimistic (ADR-003 §2).
- The portfolio-risk context marks positions at the last *seen* close; a fast market can overshoot a cap by one order.
- DSR honesty depends on the generator's `trials_tested`; the registry floor only ratchets within this platform.
- SQLite WAL is single-writer: fine for one trader + one generator; a fleet of intraday ideas pushes toward the separate trader process (L3) sooner - now available (`HISTORIFY_ROLE=trader`, deploy/).
