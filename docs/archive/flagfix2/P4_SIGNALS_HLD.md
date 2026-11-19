# P4 · Track T to Paper Signals: High-Level Design (for review)

| | |
|---|---|
| **Phase** | P4 of `PROJECT_ROADMAP_6_PHASES.md` (≈ 20 engineer-days, critical path P1 → P3 → **P4**) |
| **Goal** | Turn strategies that passed P3's evaluation gate into **timely, reproducible trading signals** for swing and intraday trading on NSE, deliver them (UI, webhook), and **paper-trade** them through OpenAlgo's Analyzer mode until live behaviour matches the backtest 1:1 |
| **Builds on** | P3 engine (`app/lab`: DataPortal, feature library, strategies, hybrid simulator, Indian execution model, evaluation, run registry, hold-out freeze), P2 intelligence (event calendar, disclosures, pre-open), P1 reference data (calendar, corporate actions, ban list), the data platform (download engine, session ledger, scheduler, quotes) |
| **Status** | **v1.1: decisions locked** (§16) · **implemented** - see `P4_SIGNALS_REPORT.md` (what was built, deviations from this design in its §4, tests, performance) and `docs/signals.md`. You chose the replay-at-each-trigger design (R1) and the recommendations for R2–R11. The rate-limit basis is the **Angel One SmartAPI standard (free) limits** (§13, `OPENALGO_ANALYZER_AND_RATE_LIMITS.md`). |
| **Not in P4** | Live (real-money) orders; authentication for remote access; F&O option strategies; WebSocket tick data. These are P6a/P6b. |

---

## Contents
1. [Summary](#1-summary)
2. [Requirements](#2-requirements)
3. [Architecture](#3-architecture)
4. [Core idea: the live signal is a replay of the backtest](#4-core-idea-the-live-signal-is-a-replay-of-the-backtest)
5. [Triggers and data freshness](#5-triggers-and-data-freshness)
6. [Completeness gate](#6-completeness-gate)
7. [Deployments, books and signals](#7-deployments-books-and-signals)
8. [Delivery: UI, webhook, notifications](#8-delivery-ui-webhook-notifications)
9. [Paper trading: shadow book and OpenAlgo Analyzer](#9-paper-trading-shadow-book-and-openalgo-analyzer)
10. [Risk controls and safety](#10-risk-controls-and-safety)
11. [Parity and the promotion gate](#11-parity-and-the-promotion-gate)
12. [Journal and Signals UI](#12-journal-and-signals-ui)
13. [Capacity and performance](#13-capacity-and-performance)
14. [Failure modes and recovery](#14-failure-modes-and-recovery)
15. [Schema, API, testing, plan](#15-schema-api-testing-plan)
16. [Decisions for review](#16-decisions-for-review)
17. [Risks and verification items](#17-risks-and-verification-items)

---

## 1. Summary

- A **deployment** takes a *frozen* strategy version (P3 code hash), adds parameters, a universe, a capital book, a mode
  (`signals` → `shadow` → `paper`) and risk limits, and attaches to **triggers**:
  - after the post-close download (EOD, ≈ 15:50 IST);
  - after the evening bhavcopy (≈ 20:30, for delivery-% / OI strategies);
  - after each intraday bar close (15m strategies; ≈ 2–3 min after the bar closes for 200 symbols).
- **No separate live engine.** At every trigger the Signal Service **replays the deployment's backtest** from its start
  date to *now*, using the same P3 code. The data comes from a **LivePortal**, which has the DataPortal interface but
  reads the current store with the same `available_at` rules. The replay's intended positions and orders at *now*
  are compared with the book's last recorded state, and the difference becomes the new signals.
- Live and backtest therefore agree **by construction**: the nightly parity check only has to detect data revisions
  and timing effects, not logic drift.
- A **completeness gate** blocks a run when a required bar is missing or provisional. It never trades on holes; it
  raises a Data Health issue and queues a repair.
- **Paper trading** happens in two books side by side. The **shadow book** holds fills simulated with P3's execution
  model on live bars; it always works and needs no broker. The **Analyzer book** holds orders sent to OpenAlgo in
  Analyzer (sandbox) mode, with fills reconciled from its order and trade books. Comparing the two measures real
  execution against the model.
- **Safety before any order call.**
  - Analyzer-mode check before every order, and idempotent client order ids.
  - Limits per deployment and globally; a kill switch; a market-hours guard; a daily loss stop.
  - An API token (custom header) required on every order-capable endpoint.
  - No live orders in P4.
- **Promotion gate:** P3 evaluation → shadow (≥ 10 sessions) → paper (20–40 sessions with 1:1 signal parity and
  execution slippage within the model) → eligible for live in P6b.

---

## 2. Requirements

### 2.1 Functional
| # | Requirement |
|---|---|
| F1 | Create, edit, pause and retire deployments. Only frozen strategy versions are allowed. Parameters are validated against the strategy schema. |
| F2 | EOD triggers for daily strategies; evening triggers for strategies that use bhavcopy features; intraday triggers at signal-bar closes (≥ 15m in v1, ≥ 5m for universes ≤ 50 symbols). |
| F3 | Signals are persisted **exactly once**. Each has an action (enter / exit / adjust stop), quantity, reference price, stop and target (raw prices), validity window, reason (feature values) and a data fingerprint. |
| F4 | Completeness gate with the policies `block` (default), `skip_instrument` and `warn`. The outcome is visible per run. |
| F5 | Delivery: the Signals page (live updates), a browser notification, and a webhook (HMAC-signed, with retries). A Telegram adapter is optional. |
| F6 | Shadow book for every deployment (model fills on live bars); Analyzer book for `paper` mode (OpenAlgo sandbox orders and fill reconciliation). |
| F7 | Risk limits: max positions, max notional per position and in total, max orders per day, daily loss stop (auto-pause), event-day rules, F&O ban filter. There is a global kill switch. |
| F8 | Nightly parity check: the re-run backtest must equal the signals persisted live. Divergences are classified and raised as issues. |
| F9 | Trade journal (signals, orders, fills, P&L per trade, your notes and tags) and deployment analytics (live vs backtest). |
| F10 | Promotion workflow with an auditable history: who promoted, when, and on which evidence. |

### 2.2 Non-functional
| Area | Target |
|---|---|
| Timeliness | EOD signals ≤ 20 min after the close (by ≈ 15:50 on normal days). Intraday 15m signals ≤ 3 min after the bar close for 200 symbols, ≤ 90 s for 50. |
| Correctness | Signals are identical to the backtest replayed on the same data (**100 %** at the nightly parity check; every divergence has a classified cause). |
| Reliability | Restart-safe. Missed triggers are caught up inside their validity window and marked `missed` outside it. Duplicate triggers produce no duplicate signals or orders. |
| Safety | No order leaves Historify unless: mode = `paper`, Analyzer confirmed ON, inside market hours, within limits, kill switch off, and the request carries the API token. |
| Isolation | Signal runs never starve downloads. Broker history calls for signals come only from the shared download engine, within the 80 % budget. |
| Observability | Per run: trigger time, data-ready time, compute time, signals, gate outcome. Latency percentiles on the status page. |

### 2.3 Non-goals (P4)
- Live orders.
- Multi-user use.
- Remote access without a reverse proxy (`SECURITY.md`).
- 1m/5m signals on large universes (these need WebSocket bars, P6b).
- Options strategies (P6a).
- Portfolio rebalancing signals for Track L (P5 adds them on the same deployment model).

---

## 3. Architecture

![P4 architecture](diagrams/p4_architecture.png)

```
                 ┌──────────────────────── app process (existing) ───────────────────────────────┐
 Scheduler ──►   │ Download engine ──(job finished event)──► SignalService                       │
 15:45 D+1m      │      ▲                                      │  trigger router (EOD / evening /   │
 20:30 refdata   │      │ incremental jobs (universe follow)   │  bar-close) · dedupe · catch-up    │
 bar closes      │ UniverseFollow (15m) ────────────────────────┤                                   │
                 │                                             ▼                                   │
                 │  CompletenessGate ◄── series_sessions (ledger)    LivePortal (DataPortal API on   │
                 │        │ ok                                      the live BarStore + refdata +    │
                 │        ▼                                         intel, same available_at rules) │
                 │  ReplayRunner ── P3 strategy.features/signals → build_inputs → engine.simulate   │
                 │        │ intended state @ now  (in-process for D; worker pool for big 15m sets)   │
                 │        ▼                                                                         │
                 │  Differ: intended vs book → Signals (idempotent) ──► RiskGuard ──► Delivery      │
                 │                                   │                       │            (UI, push,│
                 │                                   ▼                       ▼             webhook) │
                 │                          Shadow book (model fills)   OrderBridge (paper) ───────►│── OpenAlgo
                 │                                                     reconcile order/tradebook ◄─│   Analyzer
                 │  ParityChecker (nightly): P3 backtest re-run  ==  persisted live signals          │
                 └─────────────────────────────────────────────────────────────────────────────────┘
```

| Component | Responsibility | New or reused |
|---|---|---|
| **DeploymentRegistry** | CRUD, state machine, frozen-version check, promotion history | new (`app/lab/live/deployments.py`) |
| **Job-finished events** | `JobService` emits `job_finished(job_id, kind, status, instruments, intervals)` to subscribers (in-process, thread-safe callbacks) | small new hook (HLD B-gap "nothing subscribes") |
| **UniverseFollow** | For intraday deployments: at each signal-bar close + 60 s (finality), enqueue one deduplicated incremental job for the union of universes (priority `interactive`) | new; generalises `FollowService` (chart follow, 5 series) |
| **TriggerRouter** | Maps events to deployments: EOD (post-close job), evening (refdata import finished), bar-close (follow job finished); deduplicates on (deployment, decision_time); catches up after downtime | new |
| **CompletenessGate** | Required series × sessions × decision bar: present, final (not provisional), ledger `complete`/`confirmed` | new, reads the ledger (reused) |
| **LivePortal** | Same interface as the P3 DataPortal. Frames come from the live BarStore (1m/D + roll-up), factors from refdata, events and disclosures from intel. Every read is cut at `available_at ≤ now`. | new, thin, built on P3 code |
| **ReplayRunner** | Runs the P3 signal pass + engine from the book's start to now. Caches warm-up frames per trading day. | reuses P3 `build_inputs` + `simulate` |
| **Differ** | Intended orders and positions at `now` vs the book's recorded state → signals (enter / exit / stop change) | new |
| **RiskGuard** | Limits, kill switch, market-hours guard, event-day rules, ban list | new |
| **Delivery** | Signals page (polling + SSE), browser notifications, HMAC webhook with retry and dead letter, optional Telegram | new |
| **Shadow book** | Applies P3 fills to live bars as they arrive (the replay already produces them) | reuses the engine |
| **OrderBridge** | OpenAlgo order API in Analyzer mode; idempotent client ids; fill reconciliation | new (`app/lab/live/bridge.py`) |
| **ParityChecker** | Nightly re-run of each deployment's backtest over the last N sessions on frozen data; 1:1 compare; classification | new, reuses the P3 runner |
| **Journal** | Trades from both books, notes, tags, attachments (chart snapshot links) | new |

---

## 4. Core idea: the live signal is a replay of the backtest

### 4.1 Why replay instead of a stateful live engine
A typical live engine keeps its own position state and updates it incrementally. That creates a second
implementation of every rule: sizing, stops, time exits, MIS square-off, max-hold, participation, costs. Live
behaviour then drifts from the backtest. That drift is the main reason paper results disappoint.

The replay approach keeps **one implementation**:

| At trigger time *now* | Step |
|---|---|
| 1 | Build a LivePortal view cut at *now* (every frame filtered to `available_at ≤ now`). |
| 2 | Run `strategy.features → signals → intents` over the deployment window `[book.start − warm-up, now]`. |
| 3 | Run `engine.simulate` on those intents and the live execution bars up to *now*. This gives the **intended book**: open positions, pending orders (entries whose fill bar is in the future), their stops and targets, and today's exits. |
| 4 | **Differ:** compare the intended book with the **recorded book** (signals already issued, fills recorded), and emit only the difference. Examples: a new entry intent whose fill bar is the next bar becomes `ENTER`; a position the replay has closed becomes `EXIT`; a changed stop becomes `MODIFY_STOP`. |
| 5 | Persist the signals with idempotency key `(deployment, instrument, decision_t, action)`, then deliver them. |

### 4.2 Properties
- **Parity by construction.** The nightly check re-runs the same code on the same data, so it can only diverge
  through data changes (revisions, late bars) or clock effects. That is exactly what it should catch.
- **Idempotent and restart-safe.** Re-running a trigger produces the same intended book and therefore no new
  difference. After a crash, the next trigger reconstructs everything.
- **Cost.**
  - *D strategies:* a replay over 1–5 years for 200 symbols takes ≈ 3–13 s (P3 benchmark A), well inside the EOD
    window.
  - *15m intraday strategies:* positions are MIS (flat every day), so the replay window is today's session plus
    the feature warm-up. Warm-up features are computed once at 09:15 and cached; each bar-close trigger recomputes
    only today's rows. The target is ≤ 10 s for 200 symbols. The worker pool is used above 100 symbols.
- **Checkpoints bound the replay window.** The P3 engine gains one input: an **initial state** (cash plus open
  positions with their entry bar, stop, target and time rules). A weekly checkpoint of the book lets a replay start
  there instead of at the book's start, so a swing deployment's daily cost does not grow with its age. A test asserts
  that a checkpoint replay gives the same result as a full replay.
- **Sizing honesty.** Capital comes from the book's recorded equity (shadow fills, or Analyzer fills in paper mode).
  It is injected as the replay's starting equity at the last reconciled point, so sizing follows what actually
  happened, not an idealised path.

### 4.3 LivePortal rules (same as P3)
| Data | Visible at *now* when |
|---|---|
| intraday bar | its `available_at` (close + 60 s finality in live, equal to P3 `latency_s = 60`) ≤ now and its ledger session is not `provisional` for that bar |
| D bar (today) | after the post-close download **and** ledger `complete` (never intraday) |
| forming D | computed from today's 1m bars ≤ now (as in P3) |
| bhavcopy / flows | trade date 20:00 → next session |
| events / disclosures | their `available_at` |

Deployments run backtests in **live-parity mode**, which is the P3 setting `latency_s = 60` plus the measured
trigger delay from §13. The evaluation gate therefore judges the strategy under the timing it will actually get.

---

## 5. Triggers and data freshness

![Trading-day timeline](diagrams/p4_day_timeline.png)

| Trigger | Fires when | Data it waits for | Typical signal time |
|---|---|---|---|
| **Pre-market** (optional) | 09:10 after the pre-open capture (P2) | previous D, pre-open | 09:10 (gap strategies, informational) |
| **Intraday bar close** | each signal-bar close + 60 s for 15m deployments (09:30, 09:45, … 15:15) | incremental 1m/15m job for the union universe (UniverseFollow) | bar close + 1.5–3 min |
| **EOD** | the post-close download job (15:45 schedule) finishes (`succeeded` or `partial`) | today's D for the universe + NIFTY/VIX; ledger complete | ≈ 15:50–16:00 |
| **Evening** | refdata import of today's bhavcopy finished (18:45 / 20:15 / 22:15 slots) | delivery %, F&O OI, participant OI, ban list (tomorrow) | ≈ 19:00–20:30 |
| **Manual** | "Run now" in the UI / API | as configured | immediately (marked `manual`) |

**Validity windows.**
- An EOD signal is valid until the next session's 09:15 open; entries are intended for that open.
- An intraday signal is valid until its intended fill bar's close: normally the next bar, so 1–15 min.
- A trigger caught up after its validity window ends is recorded as `missed` and produces no signal.

**UniverseFollow.** One follow job per bar close covers all intraday deployments (union of universes). It uses the
existing engine, planner (`incremental_window`) and rate limiter. With `intraday_source = rollup_1m`, only 1m is
downloaded and 15m is rolled up, the same way the backtests were built.

---

## 6. Completeness gate

For every deployment run, the gate checks each (instrument, required series) for the sessions the replay needs
today:

| Check | Rule | On failure |
|---|---|---|
| Decision bar exists | the last bar with `available_at ≤ now` is the **expected** bar for this trigger (e.g. the 10:00–10:15 bar at the 10:16 trigger) | `block` (default) · `skip_instrument` · `warn` |
| Final | ledger session not `provisional`; for intraday, now ≥ bar close + 60 s | retry once after 30 s, then apply the policy |
| Complete | today's ledger status `complete` or `*_confirmed` (a known market fact, e.g. suspension) | policy |
| Reference series | NIFTY / INDIAVIX / sector index needed by the strategy are complete | always `block`: a regime filter on stale data is not a filter |
| Corporate action today | an ex-date today for a held or candidate symbol is known in the P1 table | `warn` plus adjusted stops (automatic via the adjusted view) |

**Outcomes:**
- `blocked_data_incomplete`: no signals. A repair job is queued and a Data Health issue is raised.
- `partial`: signals only for complete instruments, with the skipped instruments listed on the run.

---

## 7. Deployments, books and signals

### 7.1 Deployment state machine
![Lifecycle](diagrams/p4_lifecycle.png)

`draft → signals → shadow → paper → (eligible_live, in P6b)`, and `paused` / `retired` from any state.

- Every promotion needs the gate evidence of §11, and every promotion is recorded.
- **Editing a deployment's parameters or code version creates a new deployment version.** Books and signals are
  never rewritten.

### 7.2 What a deployment contains
| Field | Example |
|---|---|
| strategy + code_sha (frozen) + params | `mtf_pullback` @ `3f9a…`, `{fast: 20, slow: 50, atr_mult: 1.5}` |
| universe | `{"kind": "index", "index": "NIFTY 200"}` (point-in-time each day) or a watchlist |
| triggers | `intraday_15m` / `eod` / `evening` |
| book | capital ₹5,00,000, start date, product MIS/CNC, cost schedule id |
| limits | max positions 5, max notional per position ₹1,00,000, max orders per day 20, daily loss stop 2 % |
| gate policy | `block` |
| delivery | UI + webhook url (secret stored in SecretBox) |
| mode | `signals` · `shadow` · `paper` |

### 7.3 Signal
`(id, deployment_version, instrument, decision_t, action ∈ {ENTER_LONG, ENTER_SHORT, EXIT, MODIFY_STOP, MODIFY_TARGET},
qty, ref_price, limit?, stop?, target?, product, valid_until, reason_json (feature values), data_fingerprint, status)`

| Field | Detail |
|---|---|
| **data_fingerprint** | Hash of the decision inputs: the last bars' checksums (session ledger) and the code sha. The parity check uses it to prove which data produced the signal. |
| **status** | `new → delivered → acknowledged / dismissed`<br>in `paper` mode: `ordered → filled / partially_filled / rejected / expired`<br>from a block: `blocked_risk` |
| **Idempotency** | A unique constraint on `(deployment_version, instrument, decision_t, action)`. |

### 7.4 Multiple deployments on one symbol
- Books are separate: each deployment has its own positions and P&L.
- RiskGuard also enforces global limits: total notional, and optionally a single net position per symbol in paper
  mode. Two deployments can disagree on the same symbol; netting them would hide one strategy's results.

---

## 8. Delivery: UI, webhook, notifications

| Channel | Behaviour |
|---|---|
| Signals page | Today's signals per deployment, live updates through Server-Sent Events (fallback: 5 s polling). Acknowledge / dismiss. |
| Browser notification | Web Notifications API, opt-in, while the tab is open. |
| Webhook | JSON POST: `X-Historify-Signature: sha256=<HMAC(body, secret)>`, `X-Historify-Delivery: <uuid>`.<br>Retries at 2 s, 10 s, 60 s, 5 min; then dead letter (visible on the page). Delivery is at-least-once: receivers dedupe by signal id. |
| Telegram (optional adapter) | Bot token + chat id (SecretBox). A short message with a deep link to the signal. |
| Daily digest | An evening Markdown and JSON summary per deployment: signals, fills, P&L, parity. |

---

## 9. Paper trading: shadow book and OpenAlgo Analyzer

### 9.1 Shadow book (always on from `shadow` mode)
- The replay's simulated fills on live bars are recorded as the shadow book.
- It is the P3 execution model applied to real-time data: no broker, no risk.
- It shows how the strategy *would* have filled with the modelled slippage and costs.

### 9.2 Analyzer book (`paper` mode)
| Step | Design |
|---|---|
| Mode check | Before every order: confirm OpenAlgo reports **Analyzer mode ON**. If not, refuse, pause the deployment and alert. A live order can never leave Historify by accident. |
| Order | OpenAlgo order API. `strategy` tag = deployment id. Client order id = `hfy-<signal_id>-<n>`, which gives idempotency across retries. Types: MARKET for next-bar-open entries, SL-M for stops, LIMIT for limit signals. Product MIS/CNC. |
| Brackets | Target and stop are placed as separate orders after the entry fill (OCO is managed by Historify: when one fills, the other is cancelled). |
| Reconciliation | Poll the order book and trade book every 15 s in market hours, plus at 15:35. Map fills to signals. Record slippage vs the signal `ref_price` and vs the shadow fill. |
| Square-off | MIS: Historify sends exits at the strategy's time exit (e.g. 15:10), before the broker's auto square-off. |
| Failures | Rejection: record the reason and alert; the signal becomes `rejected`, and the replay's book keeps the shadow position so the difference is visible. OpenAlgo unreachable: retry with backoff within the validity window, then `expired`. |

**Endpoints** (full reference with request and response fields in `OPENALGO_ANALYZER_AND_RATE_LIMITS.md` §2):

| Purpose | Endpoints |
|---|---|
| Mode check | `analyzer` |
| Orders | `placeorder` / `modifyorder` / `cancelorder` |
| Kill switch | `cancelallorder` / `closeposition` |
| Reconciliation | `orderstatus` / `orderbook` / `tradebook` / `positionbook` / `funds` |

All calls are tagged `strategy = hfy-<deployment>`.

**Version contingency:** older OpenAlgo versions only log Analyzer orders, with no simulated fills or books. In that
case the Analyzer book records the *order path* (accepted / rejected) and the shadow book provides the fills.
`flask historify openalgo-probe --place-test-order NSE:SBIN --fill-test` (Analyzer mode only) tells which case
applies.

### 9.3 Execution-quality report
Per deployment and week, compare Analyzer fill prices with the shadow (model) fills. This gives the mean and P95
slippage in bps, the share of fills worse than the model, and rejections. If real slippage exceeds the model, P3's
slippage parameters are recalibrated from these data. This closes the loop that HLD §10.3 left open.

---

## 10. Risk controls and safety

| Control | Rule | Effect |
|---|---|---|
| Kill switch (global) | One toggle (UI, API, CLI) | Stops all order sending immediately and cancels open Analyzer orders. Signals still compute and are marked `blocked_risk`. |
| Market-hours guard | Orders only within the exchange session from the calendar (special sessions included) | Orders outside are refused |
| Per-deployment limits | max positions, max notional per position and total, max orders per day | The order is refused and the signal marked `blocked_risk` |
| Daily loss stop | Realised + unrealised P&L of the book < −x % of capital | Auto-pause the deployment for the day, exit positions (MIS), alert |
| Event rules | Optional per deployment: no new entries on high-impact days (P2 calendar: RBI, Budget, election results), or half size | Applied in the replay, so it also appears in the backtest (E16) |
| Ban list | No new positions in F&O-ban stocks (for futures later; optional for cash) | Replay filter |
| Corporate actions | Stops and targets of held positions are re-based on ex-dates (the adjusted view does this automatically; broker orders are modified) | Modify orders |
| **API token for order-capable endpoints** | Endpoints that can cause an order (promote to paper, kill switch off, manual order retry) need an `X-Historify-Token` header (token in `instance/`, mode 0600, shown once) | A malicious web page cannot drive the local API. JSON-only bodies already block simple-form CSRF, and the token closes the rest. |
| Audit log | Every order, modification, cancel, promotion and kill-switch change | Append-only table |

Live trading stays **out of scope** until P6b, which adds authentication, a dry-run window, per-deployment live
enable and stricter limits.

---

## 11. Parity and the promotion gate

### 11.1 Nightly parity check (22:45 IST, after the last refdata slot)
For each active deployment:
1. Freeze a snapshot of the last N sessions (incremental: content addressing makes this cheap).
2. Re-run the deployment's backtest on it in live-parity mode.
3. Compare signals 1:1 with the persisted live signals: same instrument, decision bar, action, quantity (±1 share
   after rounding), and stop/target (±1 tick).
4. Classify each divergence:

| Class | Detection | Typical cause |
|---|---|---|
| `data_revision` | The decision bars' checksums differ from the signal's `data_fingerprint` | The broker revised a bar after the trigger |
| `late_data` | The live run was `partial` or blocked for that instrument | Feed delay |
| `timing` | The live decision happened at a different bar | Trigger lag beyond a bar length |
| `logic` | The same fingerprint but a different signal | **A bug: must be zero.** A `logic` divergence auto-pauses the deployment. |

Results are stored in `parity_checks`. Divergences raise Data Health issues of kind `signal_parity`.

### 11.2 Promotion gate
| Stage | Entry criteria (recommended defaults, configurable) |
|---|---|
| `draft → signals` | Strategy version frozen. The evidence (the P3 run group id and the walk-forward / sweep run ids) is attached to the promotion. The P3 evaluation of the same configuration shows:<br>• walk-forward out-of-sample Sharpe > 0.5;<br>• deflated Sharpe > 0.9 (for the trials in its group);<br>• PBO < 0.5;<br>• profit factor > 1.2 at **2× slippage**;<br>• ≥ 100 trades (swing) or ≥ 300 (intraday);<br>• live-parity mode. |
| `signals → shadow` | ≥ 5 sessions with zero `logic` divergence and a gate pass rate ≥ 95 % |
| `shadow → paper` | ≥ 10 shadow sessions; shadow P&L within the backtest's 90 % Monte Carlo band |
| `paper → eligible_live` (P6b) | 20–40 paper sessions:<br>• **1:1 signal parity** (no `logic` divergence; other divergences explained);<br>• Analyzer slippage ≤ model 1.5×;<br>• no risk-limit breaches;<br>• your explicit approval. |

The gate report for each promotion is saved, so you can see why a deployment was promoted.

---

## 12. Journal and Signals UI

| Page / tab | Content |
|---|---|
| **Signals › Today** | Per deployment: trigger timeline (fired, data ready, computed, delivered), signals with their reasons, status, acknowledge / dismiss, risk blocks. The kill switch is always visible. |
| **Signals › Deployments** | List and detail: configuration, version history, mode, limits, books (shadow / Analyzer equity), promotion panel with the gate evidence, pause / resume. |
| **Signals › Journal** | Trades from both books: entry, exit, P&L, R, slippage vs model, tags, notes. A chart link opens the Charts page with trade markers (reused from P3). Filters and CSV export. |
| **Signals › Parity** | Nightly results, divergence list with classification, trend of parity rate and latency |
| Dashboard widget | Today's signals count, blocked runs, kill-switch state, paper P&L |

Same stack as the rest of the app: Jinja shells, Alpine CSP components, Lightweight Charts, no `innerHTML`.

---

## 13. Capacity and performance

### 13.1 Broker budget: Angel One SmartAPI standard (free) limits, 80 % share
Angel One's standard limits are history 3/s · 180/min · 5,000/h, quotes 10/s, orders 20/s (capped at the SEBI 10/s
threshold) · 500/min · 1,000/h, and books 1/s. The full tables, OpenAlgo's own server limits and the allocation are in
`OPENALGO_ANALYZER_AND_RATE_LIMITS.md` §3–§4. Two points for P4:
- In Analyzer mode, **orders and books never reach Angel One**.
- The **sandbox prices fills with Angel One quote calls**, so P4 reserves ≥ 3/s of the quote budget while any
  deployment is in paper mode (the QuoteService is capped at 5/s).
| Universe × signal TF | Calls per bar close | Share of budget | Data ready after bar close |
|---|---|---|---|
| 50 × 15m | 50 | 2 % of the hourly budget | ≈ 60 s finality + 21 s fetch ≈ **1.4 min** |
| 200 × 15m | 200 | 20 % hourly (800 of 4,000) | ≈ 60 s + 83 s ≈ **2.4 min** |
| 200 × 5m | 200 every 5 min | 60 % hourly | ≈ 2.4 min (tight: chart follows and scans share the budget) |

Intraday signals for large universes stay at **≥ 15m** in P4. Scheduled backfills are automatically deprioritised
during market hours (the engine's existing priorities: interactive > scheduled > backfill).

### 13.2 Compute
| Run | Target | How |
|---|---|---|
| EOD replay, 200 symbols, 3-year book | ≤ 15 s | P3 benchmark A scale; in-process |
| Intraday replay, 200 symbols, bar close | ≤ 10 s | cached warm-up features (computed at 09:15), today's rows only, worker pool above 100 symbols |
| Nightly parity, all deployments | ≤ 10 min | worker pool; incremental snapshots |

---

## 14. Failure modes and recovery

| Failure | Behaviour |
|---|---|
| App down at a trigger | On start, catch up the triggers of the last 24 h: run each if still inside its validity window, otherwise record `missed` (never trade late) |
| Download job `partial` / failed | Completeness gate → `partial` / `blocked`; a repair job is queued; one retry after the repair finishes, if still valid |
| Duplicate triggers (job retry, manual run) | Deduplicated on (deployment, decision_t); the idempotent signal key prevents duplicates |
| Broker auth expired (OpenAlgo) | The engine's existing auth circuit pauses downloads. Signals are blocked with the reason "data source down"; paper orders are refused. |
| OpenAlgo Analyzer switched off | Mode check fails: no orders, the deployment is paused, alert |
| Webhook receiver down | Retries, then dead letter. Signals are still in the UI. |
| Data revision after a signal | Shown by the parity check as `data_revision`. No automatic re-trading. |
| Clock skew | The server clock drives triggers. The status page warns if the OpenAlgo server time differs by more than 5 s. |
| Special sessions (Muhurat, budget Saturday) | Calendar-driven triggers; square-off times from the session, not constants |
| Strategy exception | The run fails for that deployment only. Alert; other deployments continue. |

---

## 15. Schema, API, testing, plan

### 15.1 Meta schema (Alembic `0008_signals`)
| Table | Key columns |
|---|---|
| `deployments` | id, name, version, strategy, code_sha, params_json, universe_json, triggers_json, book_json (capital, start, product, costs), limits_json, gate_policy, delivery_json, mode, status, created_at |
| `deployment_events` | deployment_id, at, kind (created, promoted, paused, resumed, retired, version), actor, evidence_json |
| `deployment_runs` | id, deployment_id, trigger (eod / evening / bar / manual / catch_up), decision_time, fired_at, data_ready_at, computed_at, gate_outcome, skipped_json, signals, error |
| `signals` | id, deployment_id, instrument_id, decision_t, action, qty, ref_price, limit, stop, target, product, valid_until, reason_json, fingerprint, status, created_at · **unique** (deployment_id, instrument_id, decision_t, action) |
| `book_positions`, `book_fills` | book (shadow / analyzer), deployment_id, instrument_id, qty, avg_price, … / fill time, price, qty, costs, signal_id |
| `paper_orders` | client_id (unique), signal_id, broker_order_id, type, price, trigger, qty, status, broker_status_json, timestamps |
| `deliveries` | signal_id, channel, attempt, status, response_code, next_retry_at |
| `parity_checks` | deployment_id, session, compared, matched, divergences_json, classes |
| `journal_notes` | trade ref, text, tags |
| `audit_log` | at, actor, action, object, detail_json (append-only) |

### 15.2 API (`/api/v2/signals/...`; order-capable routes need `X-Historify-Token`)
`GET/POST /deployments` · `GET/PATCH /deployments/{id}` · `POST /deployments/{id}/promote|pause|resume|retire|run-now` ·
`GET /today` · `GET /signals?deployment=&date=&status=` · `POST /signals/{id}/ack|dismiss` · `GET /stream` (SSE) ·
`GET /books/{deployment}?book=shadow|analyzer` · `GET /journal` · `POST /journal/{trade}/notes` ·
`GET /parity?deployment=&from=` · `POST /kill-switch {on}` · `GET /status` (latencies, budget, analyzer mode) ·
CLI `flask historify signals deploy | list | run-now | promote | kill | parity`.

### 15.3 How P4 will be tested
| Layer | What | Oracle |
|---|---|---|
| Unit | Differ (intended vs recorded book → signals), gate rules, risk rules, HMAC signing, idempotency keys, validity windows | hand-worked cases |
| **Time-travel day simulation** | A frozen clock steps through a whole trading day (09:00 → 23:00). The mock broker releases 1m bars as time passes; triggers fire; signals are produced. | **The signals must equal a P3 backtest of the same day on the same bars** (parity by construction, asserted) |
| Property | Re-running any trigger (any number of times, any order within validity) produces no new signals or orders. A crash at any step followed by a restart gives the same final state. | invariants |
| Gate | A missing, provisional or late bar blocks, skips or warns per policy. The reference series is always `block`. | scenarios |
| Mock OpenAlgo Analyzer | Mode on/off, accept/reject, partial fills, slow responses, restart between order and fill; client-id idempotency | scripted mock (extends `tests/mock_provider`) |
| Safety | No order without the token; no order with Analyzer off, with the kill switch on, outside hours, or over limits (every path) | negative tests |
| Parity checker | Injected bar revision → `data_revision`; injected code change → `logic`, which auto-pauses | classification |
| Capacity | 200-symbol 15m day: data-ready and signal latency percentiles within §2.2 using the mock broker's rate limits | benchmark |
| UI E2E | Today, deployments, promotion with evidence, kill switch, journal, parity; no CSP errors | Playwright |

### 15.4 Work breakdown (≈ 20 d, as in the roadmap)
| Work package | Effort |
|---|---|
| W1 Job-finished events, TriggerRouter, UniverseFollow, catch-up | 2.5 d |
| W2 LivePortal, completeness gate, ReplayRunner with warm-up cache and checkpoints (engine initial state), Differ | 3.5 d |
| W3 Signals store, delivery (SSE, webhook with HMAC and retries, notifications), digest | 1.5 d |
| W4 Shadow book, OrderBridge (Analyzer), reconciliation, execution-quality report | 2.5 d |
| W5 RiskGuard, kill switch, API token, audit log | 1 d |
| W6 Parity checker, promotion gate, deployment versions | 1.5 d |
| W7 Signals UI (Today, Deployments, Journal, Parity) + dashboard widget | 3 d |
| W8 Strategy and experiment work:<br>• S1/S2/S4 swing;<br>• the D + 15m bridge (daily setup, intraday timing);<br>• I1/I2/I4 15m intraday;<br>• E9–E14, E16, E18–E21 re-run in live-parity mode;<br>• deploy the ones that pass. | 4.5 d |
| **Total** | **20 d** |

**Milestones:**
- **M4a (day 7):** EOD signals for one frozen swing strategy in `signals` mode, with time-travel parity tests green.
- **M4b (day 12):** intraday 15m signals with UniverseFollow, shadow book and webhook.
- **M4c (day 16):** Analyzer paper trading with reconciliation, risk controls, the parity checker and the UI.
- **M4 (day 20):** first deployments in `paper`. The 20–40-session paper period then runs in calendar time.

---

## 16. Decisions (locked in v1.1)

| # | Decision | Options | **Decided** |
|---|---|---|---|
| R1 | **How live state is computed** | (a) stateful live engine; (b) replay of the backtest at each trigger + differ | **(b) Replay at each trigger** (your choice): one implementation and parity by construction. Weekly checkpoints bound the cost (§4.2). |
| R2 | Paper trading | shadow only · Analyzer only · both | **Both**: the shadow book is the model; OpenAlgo Analyzer rehearses the order path and measures slippage. If your OpenAlgo version does not simulate fills, the shadow book is the paper record (§9). |
| R3 | Intraday signal timeframe | 5m · 15m | **15m** for ≤ 200 symbols (5m only for ≤ 50), from the free-tier history budget (§13) |
| R4 | Delivery channels | UI · webhook · Telegram · email | **UI + HMAC webhook**; Telegram as an optional adapter |
| R5 | Order-endpoint protection | none · API token header · full auth | **API token header** now; full auth in P6b |
| R6 | Promotion thresholds | §11.2 | **Defaults of §11.2**, tuned after the first two deployments |
| R7 | Capital model | shared book · separate books + global limits | **Separate book per deployment** + global limits |
| R8 | Missed triggers | run late · mark missed | **Mark missed** outside the validity window |
| R9 | Conflicting deployments on one symbol | net · independent | **Independent books** in shadow/paper |
| R10 | Where replays run | in-process · pool · hybrid | **In-process for EOD; worker pool for intraday > 100 symbols** |
| R11 | First deployments | — | **E9 (`mtf_pullback`, 15m) and E11 (`rsi2`, EOD)** if they pass the gate on your data; otherwise the best of E1/E14 |
| R12 | Rate-limit basis | vendor / paid data · broker standard limits | **Angel One SmartAPI standard (free) limits**, 80 % share. P4 order defaults are 2/s · 30/min · 200/h, well under the 10/s SEBI / OpenAlgo ceilings (`OPENALGO_ANALYZER_AND_RATE_LIMITS.md`). |

## 17. Risks and verification items

| Risk | Impact | Mitigation |
|---|---|---|
| OpenAlgo Analyzer API details differ from assumptions | Paper trading delayed | An adapter layer with a contract test; verify the endpoints first (🟡 below); the shadow book keeps P4 useful without it |
| Broker bar revisions after the trigger | Parity noise | Fingerprints classify them as `data_revision`, not a bug. The revision rate is measured in P1's ledger. |
| Trigger latency on busy days (many follows, backfills) | Late intraday signals | Priorities, a reserved budget share for UniverseFollow, latency alerts, and a live-parity latency in the backtest |
| Strategy passes the gate but fails in paper | Expected sometimes | That is what paper is for: the promotion gate blocks it; the execution-quality report tells you why |
| Security: local API driven by a malicious page | Unwanted paper orders | API token on order-capable routes; JSON-only bodies; localhost binding; no live orders in P4 |
| Scope creep into Track L signals | Delay | Track L uses the same deployment model in P5 (rebalance signals) |

**Verify on your machine (🟡)** with `flask historify openalgo-probe` (read-only), then `--place-test-order NSE:SBIN
--fill-test` (Analyzer mode only). The redacted report goes in `instance/probe/`.
- OpenAlgo version and Analyzer behaviour: does it simulate fills, and do the books work in Analyzer mode?
- OpenAlgo `.env` rate limits, if changed from the defaults; Angel One's current published limits.
- Whether Angel One requires a static IP for API orders (matters for P6b live, not for paper).
- MIS square-off cut-off and auto square-off time on your Angel One account (default 15:15 exits at 15:10).
- Typical 1m bar availability delay from Angel One during market hours. The design assumes ≤ 60 s finality plus
  fetch; measure it with `flask historify rollup-audit` on a live day.
- Whether you want Telegram delivery (needs a bot token).
