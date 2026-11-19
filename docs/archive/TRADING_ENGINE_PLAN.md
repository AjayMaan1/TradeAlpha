# Trading Engine: ideas in → paper → live → trade data out

| | |
|---|---|
| **Goal** | A trading engine that takes strategy **ideas** from the idea-generation engine, trades them in real time (paper first, live after approval), and streams the **trade data** back for analysis. |
| **Baseline** | Historify after the flagfix1 + flagfix2 integration (`docs/INTEGRATION.md`): ~47k lines of app Python, 7.9k lines of Rust, 1,218 automated tests green + 14 Rust tests + browser suite. |
| **Evidence** | Everything marked ✅ was run or measured in this sandbox. Items marked 🟡 need your broker, OpenAlgo or accounts. |

---

## 0. Summary

1. **What this system is and what it isn't.**
   - **It is** a **mid-frequency** engine. Decisions are made on 1m–15m and daily bars, and orders go through
     **OpenAlgo → a retail broker API** (Angel One).
   - **It isn't HFT.** Tick-to-trade in microseconds needs colocation, direct exchange feeds, kernel-bypass networking
     and a different regulatory footing. None of the current stack (Python, SQLite, REST broker API) can be tuned into that.
   - **The realistic target** is bar close → order at the broker in **3–8 s**. Today it is **≈ 2.4 min** for 200
     symbols on 15m bars (measured by P4).

2. **The contract with the idea engine is declarative, never code.** An idea arrives as an **IdeaSpec**:
   - the Strategy IR (flagfix1: JSON, 86 operators, schema-validated, content-hashed);
   - an ExecSpec;
   - a universe, risk budget, provenance and trial count.

   The engine compiles it and fuzz-tests it for look-ahead. It then runs it on the **same Rust core** in backtest, paper
   and live, so paper and live agree with the backtest by construction. Arbitrary Python from a generator would mean
   remote code execution inside the process that holds the broker key. That is not acceptable.

3. **One order path.** The integration left two paper runtimes: the Lab `lab_paper` and the signals `signals`. Their kill
   switches are now linked. Before live trading they must share a single **Execution Gateway**:
   - the journal-first OMS with idempotency keys and compare-and-set state (flagfix1);
   - pre-trade risk, the union of the 19 guardrails and the P4 limits;
   - broker adapters: simulated / OpenAlgo Analyzer / OpenAlgo live, with the Analyzer-mode check before every order;
   - reconciliation and one kill switch.

4. **The lifecycle is a gated state machine.** Each gate produces evidence; the last one is your signed approval:
   `received → validated → evaluated → shadow → paper → awaiting_approval → live_pilot → live`
   - Paper validates **operations and execution**: parity, fills, slippage, rejects. It does not prove the edge. 62 paper
     sessions detect a dead Sharpe-2 strategy only 26 % of the time (flagfix1 D-19). Statistical proof comes from the
     evaluation gate, which uses **the idea engine's own trial counts**.

5. **Trade data goes out through an outbox, so nothing is lost or duplicated.**
   - Every decision, order transition, fill, P&L mark and risk event is written as a **TradeEvent** in the same database
     transaction as the OMS change.
   - A relay ships the events: a signed webhook, a cursor pull API, and a nightly Parquet export per idea and day.

6. **Effort: ≈ 94 engineer-days in 5 phases** (`TRADING_ENGINE_5_PHASES.md`). Phase 1 first turns the two
   backtest flows into **one backtest process**.
   - **Two engineers:** first ideas on paper in week 10, live pilot from week 16.
   - **Calendar time you cannot compress:** paper trading (≥ 20 sessions and ≥ 30 round trips per idea) and the live
     pilot (≥ 20 sessions).
   - **Hard prerequisites before live:**
     - a **static IP** (SEBI retail-algo rules / the broker), so OpenAlgo and the trader run on a VPS;
     - verified broker tariffs and statutory rates;
     - the Execution Gateway;
     - 10 real OpenAlgo Analyzer sessions without reconciliation errors.

---

## 1. Scope

| In scope (v1) | Out of scope (v1) |
|---|---|
| NSE cash equities: intraday (MIS, 5m/15m bars) and swing (CNC, daily); liquid universe (NIFTY 100/200) | Sub-second strategies, market making, order-book (L2/L3) strategies |
| Ideas expressible in Strategy IR v1 (indicators, MTF, brackets, sizing, session rules) | Ideas that need arbitrary code. The IR gets new operators instead (versioned) |
| Paper: simulated fills with shadow quotes (G1) + OpenAlgo Analyzer sandbox (G2) | Multi-broker smart routing |
| Live: one broker through OpenAlgo, hard caps, human approval | F&O live trading (the research exists in P6a; live only after equities have run cleanly) |
| Trade data out: events, fills, P&L, parity, execution quality | Tax / contract-note accounting beyond what P5 already does |

---

## 2. Audit: where the integrated application stands

### 2.1 What is already strong (reuse it, do not rebuild it)

| Area | What exists | Why it matters for the trading engine |
|---|---|---|
| Data integrity | session ledger, quarantine, revision archive, checksummed snapshots, point-in-time `available_at`, calendar, corporate actions, lot/tick history | backtests that are not lying; the same bars in paper and in the backtest |
| Deterministic execution core | Rust `hbt-core`: Indian execution model (next-open fills, gap-through stops, MIS square-off, lots/ticks, costs), byte-identical re-runs, look-ahead fuzz harness, oracle parity (backtesting.py, TA-Lib) | **one** implementation of the rules for backtest, paper and live |
| Strategy IR | declarative JSON, params with bounds, constraints, canonical hash, `explain`, JSON schema | a safe, hashable, machine-writable contract for generated ideas |
| Research statistics | walk-forward, deflated Sharpe (reproduces the published example), robustness (neighbours, costs ×1.5, +1 bar latency), append-only trial registry, PBO / Monte Carlo (P3) | guards against a generator mining noise |
| OMS (flagfix1) | journal-first state machine, UNIQUE idempotency keys, compare-and-set transitions, lost-ack handling (never blind-resend), restart reconcile, kill-9 drills at 4 journal points | the hardest part of an order path, already tested |
| Guardrails | 19 (flagfix1) + P4 risk limits, daily loss stop, kill switch (now linked across both paths), market-hours guard | pre-trade and intraday risk |
| Live gate (flagfix2 P6b) | global switch with a confirmation phrase, auth required, complete limits, **Analyzer OFF re-checked before every live order**, dry-run sessions, separate live book | the release controls for real money |
| Lifecycle (flagfix2 P4) | deployments with stages `draft → signals → shadow → paper → eligible_live → live`, evidence attached to each promotion, nightly parity with classified divergences, audit log, journal, HMAC webhooks with retries and a dead letter | the skeleton of the idea lifecycle |
| Ops | Prometheus `/metrics`, notifications, backups plus verified restore, auth with API tokens, CSP, request guard | operations |

### 2.2 Gaps for the trading engine (ordered by severity)

| # | Sev | Gap | Evidence | Consequence |
|---|---|---|---|---|
| G1 | **Blocker** | **No way for ideas to get in, and no trade-data contract going out.** Strategies are created by hand (IR in the Lab UI, Python files in `instance/strategies`); results are UI pages, Parquet artefacts and webhooks of *signals* only. | `grep` for any intake API: none; outbound = P4 signal webhooks | the core of your request is missing |
| G2 | **Blocker (live)** | **Two execution stacks.** Two engines (Rust IR vs Python sim), two paper runtimes, two OMS tables (`lab_orders` vs `paper_orders`), two risk layers. The kill switches were linked at the integration; everything else is still separate. | `docs/INTEGRATION.md` §5 | two places to get an order path wrong; parity between engines is not tested |
| G3 | High | **Decision latency.** Decisions run on bars *downloaded* after close plus 60 s finality: ≈ 2.4 min for 200 symbols on 15m. The WebSocket bar builder (P6b) exists but only feeds the UI. | P4 report §5; flagfix1 D-20 | 1m/5m intraday ideas on a real universe are not tradable; 15m ideas lose 2–3 min of edge |
| G4 | High | **Paper host recomputes the whole warm-up window on every bar** (23–28 ms per member-bar). `Engine::step_bar` (incremental) is not built. | flagfix1 §4.2 | 200 members × 5m ≈ 5 s per bar close. Fine for a few ideas, not for dozens. |
| G5 | High | **The order path has only met mocks.** G2 polls the books (no order-update stream) and was never run against a real OpenAlgo for 10 sessions. P4's Analyzer path is tested on the mock only. The engine lacks stop-limit, IOC and cancel-replace. | flagfix1 §4.3–4.4, P4 report | unknown behaviour with real partial fills, rejects and lag |
| G6 | High | **No portfolio-level risk across ideas.** Every deployment or paper member has its own book (decision R9). There are no caps on gross/net exposure, sector, correlation or the same symbol across ideas, and no capital allocator. | P4 HLD §7.4 | 30 generated ideas can all buy the same 5 bank stocks |
| G7 | High | **Multiple testing by the generator is invisible.** DSR counts only the trials registered in Historify. The idea engine may have tried 10,000 variants before sending one. | `hbt.research.evaluate`, `lab_trials` | generated ideas pass the deflated-Sharpe gate by construction |
| G8 | Medium | **Single process.** Downloads, backtests, scanners, UI and order paths share one Flask process (threads); the meta DB is one SQLite file; it runs on a desktop. | `Services` | a CPU-heavy job or a crash stops order handling; the desktop has no static IP (see G10) |
| G9 | Medium | **Costs unverified.** Broker tariffs and statutory rates are `verified = false` (D-13); the G3 gate is blocked until they are verified. | `lab_broker_profiles`, `statutory_nse.json` | paper P&L and gates are biased by unverified costs |
| G10 | Medium 🟡 | **Regulatory / broker readiness.** SEBI's retail-algo framework (in force since late 2025, phased) requires broker-registered algos with an exchange algo ID above the order-rate threshold (10 orders/s), **static-IP whitelisting for API orders**, and OAuth-style API access. | `OPENALGO_ANALYZER_AND_RATE_LIMITS.md` §3.3 | live orders from a home desktop may simply be refused |
| G11 | Medium | **Python strategies run in-process** (`exec_module` of files in `instance/strategies`). | `app/lab/strategy.py:276` | fine for your own code; never for generated ideas (→ IR only) |
| G12 | Low | **Performance gates of T1.6.1 not met** (795 sweeps/s vs a 3,000/s target; evaluator 107 M vs 150 M node-updates/s). | flagfix1 §3 | matters only if the idea engine delegates mass backtests to Historify |
| G13 | Low | **Operational items are manual.** Clock sync (NTP) is an owner confirmation; there is no hash-chained audit log (G-SYS-6 planned); API tokens have no scopes. | readiness checklist, `core/auth.py` | governance and least privilege |

---

## 3. Target architecture

```
        ┌─────────────────────────── Idea generation engine (yours) ───────────────────────────┐
        │  IdeaSpec v1 (IR + ExecSpec + universe + risk + provenance + trial count), signed      │
        └───────────────┬───────────────────────────────────────────────▲──────────────────────┘
                        │ POST /api/v2/ideas  (token scope ideas:write)  │ TradeEvents: webhook (HMAC) ·
                        ▼                                                │ GET /api/v2/trading/events?after= ·
  ┌──────────────────────────── RESEARCH NODE (desktop, existing Historify) ───────────┐ nightly Parquet
  │ Idea Gateway: schema → compile (hbt-ir) → look-ahead fuzz → dedupe (IR sha) → registry          │
  │ Evaluation pipeline (worker processes): snapshot backtest · WFO · DSR(N = engine trials + ours) │
  │   · robustness · capacity · gates → evidence bundle (hash)                                        │
  │ Data platform: downloads, ledger, snapshots, refdata, replica (unchanged)                         │
  └────────────────────────────────────────┬─────────────────────────────────────────────────────────┘
                                           │ frozen, approved IdeaVersion (IR sha + ExecSpec sha + limits)
  ┌──────────────────────────── TRADING NODE (VPS with static IP; same codebase, `historify trader`) ─┐
  │ Market data: OpenAlgo WebSocket → 1m bars (provisional → final) → bar bus; REST finality repair    │
  │ Strategy runtime: hbt-core Engine::step_bar per (idea, symbol); intents, never orders              │
  │ Portfolio risk: capital allocation per idea, gross/net/sector/symbol caps, correlation budget      │
  │ Execution Gateway: pre-trade guardrails → OMS (journal-first, idempotent) → broker adapter         │
  │      adapters: Simulated(G1, shadow quotes) │ OpenAlgo Analyzer(G2) │ OpenAlgo Live(G3/G4)          │
  │      reconciliation (order/trade/position books), kill switch (one), auto-demotion                  │
  │ Trade store + Outbox (same transaction) → relay → webhook / pull API / Parquet                       │
  │ Observability: /metrics, latency histograms, alerts; hash-chained audit log                          │
  └───────────────────────────────────────────┬────────────────────────────────────────────────────────┘
                                              ▼
                                OpenAlgo (same VPS) → broker (Angel One) → NSE
```

**Why two nodes.**
- **The static IP.** Live API orders must leave from a whitelisted static IP (🟡 broker), so the order path and
  OpenAlgo must run on a server.
- **Blast radius.** A 40-minute backfill or a sweep must never share a process, or a CPU, with the OMS.
- **The data platform stays where it is.** It needs your disk and your history and has no latency requirement.

The trading node runs the same codebase with a different entry point and its own instance directory. It is
self-sufficient during the session: it keeps its own bars, and a warm-up snapshot is shipped from the research node
each morning. It reports back through the outbox.

**Phase A–C can run everything on one machine** in paper mode. The split is a prerequisite for live (phase D), not
for paper.

---

## 4. Contracts

### 4.1 IdeaSpec v1 (idea engine → trading engine)

```json
{
  "spec": "historify.idea/1",
  "idea_id": "gen-2026-11-16-000123",
  "version": 1,
  "source": {"engine": "ideagen", "engine_version": "3.2.0", "run_id": "r-8841", "seed": 17,
             "trials_tested": 4210, "data_end": "2026-09-30", "features_used": ["ema", "atr", "rvol"]},
  "hypothesis": "Large-cap momentum continuation after an opening-range breakout with above-average volume",
  "strategy": {"ir": { "ir_version": 1, "name": "ORB15 + RVOL", "params": {"...": "..."}, "rules": ["..."] },
               "params": {"orb_min": 15, "rvol_min": 1.5}},
  "exec": {"fill_timing": "next_open", "slippage_model": "ticks", "slippage_value": 1, "product": "MIS",
           "no_new_entries_after": "14:30", "squareoff_at": "15:10"},
  "universe": {"kind": "index", "index": "NIFTY 100", "point_in_time": true, "interval": "5m"},
  "risk": {"capital": 500000, "risk_per_trade_pct": 0.5, "max_positions": 5, "max_notional_per_position": 100000,
           "daily_loss_stop_pct": 2.0, "max_orders_per_day": 40},
  "evidence": {"backtest": {"start": "2021-01-01", "end": "2026-09-30", "trades": 1840, "sharpe": 1.6,
                            "max_dd_pct": 9.1, "snapshot_sha": "..."}},
  "requested_stage": "paper",
  "valid_until": "2027-03-31"
}
```

The signature goes in a header: `X-Idea-Signature: ed25519=<sig over the canonical JSON>`, with a per-engine key.

**Rules.**
- **Identity.** `idea_id + version` is immutable; a new version is a new row. The engine computes `ir_sha256` and
  `exec_sha256`. Two ideas with the same hashes are the same strategy, and the second is linked, not re-run.
- **Validation** (synchronous, < 1 s): JSON schema → IR compile with param bounds/constraints → universe resolvable
  point-in-time → data coverage for the universe and interval → product and session rules consistent with the
  interval (flagfix1 A2-H1 check).
- **Look-ahead fuzz** (async, seconds): the flagfix1 harness on every signal and rule of the IR. Failure rejects the idea.
- **Trial count.** `trials_tested` is **mandatory** and feeds the deflated Sharpe. An idea without it is evaluated
  with a conservative default (N = 1,000).
- **No embargo breach.** `source.data_end` must lie before the evaluation hold-out cut-off (flagfix1 D-18). Ideas
  built on data Historify wants to test them on are refused.

### 4.2 Idea lifecycle (state machine)

```
received ─► validated ─► evaluated ─► shadow ─► paper ─► awaiting_approval ─► live_pilot ─► live
   │            │            │          │         │              │                 │          │
   └► rejected  └► rejected  └► failed  └────────┴──────────────┴───── paused ◄───┴──────────┘
                                                        (auto-demotion / kill)      ► retired (any)
```

| Transition | Automatic? | Gate (defaults; configurable) |
|---|---|---|
| received → validated | yes | §4.1 validation + look-ahead fuzz |
| validated → evaluated | yes | snapshot backtest; walk-forward OOS Sharpe > 0.5 over ≥ 8 folds; **DSR > 0.95 with N = trials_tested + registry**; PBO < 0.5; profit factor > 1.2 at 2× slippage and +1 bar latency; ≥ 300 trades (intraday) or ≥ 100 (swing); capacity: position ≤ 1 % of the 20-day median traded value. The idea engine's claimed metrics must be reproduced within tolerance, otherwise the idea is flagged `irreproducible`. |
| evaluated → shadow | yes | portfolio risk has room (§6); a capital slot is allocated |
| shadow → paper | yes | ≥ 5 sessions, 0 `logic` parity divergences, data-gate pass rate ≥ 95 % |
| paper → awaiting_approval | yes | ≥ 20 sessions **and** ≥ 30 round trips (whichever comes later); evaluator parity 100 %; 0 duplicate or orphan orders; reconciliation 100 %; Analyzer slippage ≤ 1.5× the model; reject rate < 2 %; paper P&L inside the backtest's 5–95 % bootstrap band for the same number of trades |
| awaiting_approval → live_pilot | **no: your signed approval** | evidence bundle (all gate reports, hashes); typed confirmation; auth on; tariffs verified; static-IP node healthy |
| live_pilot → live | semi | dry run for 1 session, then ≥ 20 sessions at pilot caps; real slippage ≤ 1.5× the model; caps double at most once per 10 clean sessions (enforced in code) |
| any → paused | yes | daily loss stop, drawdown > 1.5× the backtest max DD, any `logic` divergence, slippage > 2× the model over 20 fills, reject spike, repeated data-gate blocks, broker/Analyzer mode mismatch |

This maps directly onto the flagfix2 `deployments.stage` machine. `evaluated` and `awaiting_approval` are new; the
others exist. The promotion evidence reuses `evidence_json` plus the gate reports.

### 4.3 TradeEvent v1 (trading engine → analysis)

Append-only, one sequence per trading node, at-least-once delivery. Consumers de-duplicate by `event_id`.

| `type` | Emitted when | Key fields |
|---|---|---|
| `idea.state` | every lifecycle transition | from, to, gate_report_sha, actor |
| `decision` | each evaluated bar with a non-empty intent | bar_ts, available_at, decided_at, as-seen data hash, feature values, intents |
| `order.state` | every OMS transition | client_id, broker_order_id, side, qty, type, limit/trigger, state, reason, latency_ms since decision |
| `fill` | each (partial) fill | price, qty, fees breakdown, **model price**, **arrival quote (bid/ask)**, slippage bps vs model and vs arrival |
| `position` | after each fill and at EOD | qty, avg price, realised / unrealised P&L |
| `risk` | guardrail or limit events | rule id, severity, action (block / pause / flatten / kill) |
| `parity` | nightly | compared, matched, divergences by class |
| `session.summary` | EOD | P&L (gross / costs / net), trades, hit rate, max intraday DD, latency p50/p95, data completeness |

Envelope: `{event_id (uuid7), seq, ts, node, idea_id, idea_version, ir_sha, mode (paper_sim | paper_analyzer |
live), type, payload}`.

**Delivery.**
1. **Outbox table.** The outbox row is written in the *same transaction* as the OMS journal change, so no event can
   exist without its state change, or the reverse.
2. **Relay.** A relay thread pushes to the idea engine's webhook (HMAC signed; retries at 2 s / 10 s / 60 s / 5 min;
   then a dead letter, reusing the P4 delivery code).
3. **Pull.** `GET /api/v2/trading/events?after=<seq>&limit=` with scope `trades:read`. This is the recovery path and
   makes the webhook optional.
4. **Nightly Parquet.** One file per day and idea (`events/`, `fills/`, `orders/`, `equity/`), checksummed, plus a
   manifest. This is for the idea engine's analytics and retraining.

---

## 5. Key design decisions

| # | Decision | Alternatives | Why |
|---|---|---|---|
| T-1 | **Ideas are IR + ExecSpec (data), never code** | Python strategy files from the generator | no RCE next to the broker key; hashable and deduplicated; look-ahead fuzzable; one Rust core in all modes → parity by construction. Missing expressiveness is added as IR operators (versioned). |
| T-2 | **The Rust `hbt-core` is the execution semantics for ideas**; the Python P3 engine stays for Track L, F&O and event studies | make the Python engine canonical | hbt-core is deterministic, look-ahead fuzzed and oracle-checked. Each branch's own benchmark shows about two orders of magnitude more throughput (hbt sweeps ≈ 31 M bars/s vs P3 ≈ 0.2 M bars/s incl. 1m execution). The workloads differ, so this is indicative, not like-for-like. The IR targets it. A cross-engine parity suite (same strategy in both) is added so the two never drift silently. |
| T-3 | **One Execution Gateway** (flagfix1 OMS + guardrails ∪ P4 limits + flagfix2 bridge/live gate as broker adapters) | keep both paper runtimes | one order path to verify, one kill switch, one reconciliation. `lab_paper` and `signals` become *strategy hosts* that emit intents. |
| T-4 | **Incremental `Engine::step_bar` for live**; replay is kept as the nightly parity oracle | replay-at-trigger for everything (current) | replay costs grow with warm-up and universe (G4); step_bar is O(1) per bar. Parity with replay is asserted every night, so correctness keeps its oracle. |
| T-5 | **WebSocket bars are the live decision source**; REST history is the finality/repair path; parity classifies `data_source` divergences | keep deciding on downloaded bars | takes latency from minutes to seconds (G3). The provisional/final divergence rate is measured before any 1m idea is allowed. |
| T-6 | **Portfolio risk layer between the ideas and the gateway** | independent books per deployment (R9) | many generated ideas are correlated; capital must be allocated and exposure capped globally (G6) |
| T-7 | **Transactional outbox** for trade data | publish from application code after commit | no lost or phantom events on a crash; the kill-9 drills extend naturally |
| T-8 | **Paper = two layers**: simulated with shadow quotes (always on) + OpenAlgo Analyzer (rehearses the real order path) | Analyzer only | the simulation is the model; the Analyzer tests the plumbing; the difference is measured execution risk |
| T-9 | **Promotion to live is human; demotion is automatic** | fully automatic promotion | real money plus a regulator; automatic demotion is the safe direction |
| T-10 | **Trading node on a VPS with a static IP** (Mumbai region, same host as OpenAlgo) before live | desktop | static-IP rule (🟡), uptime, blast radius |

---

## 6. Risk framework

| Layer | Controls | Source |
|---|---|---|
| Per order (pre-trade) | instrument whitelist, notional and lots, price collar vs LTP, fat finger, order rate (≤ 2/s default; ≤ 10/s hard, SEBI threshold), trading window, margin check (OpenAlgo `margin`), MIS cut-off, ban list, circuit proximity | flagfix1 G-PRE-1…9, P4 RiskGuard |
| Per idea (intraday) | protective stop mandatory, daily loss stop → flatten + pause, drawdown → kill + flatten, consecutive losses → pause, staleness / bad tick, max orders per day | flagfix1 G-IN-*, P4 limits |
| **Portfolio (new)** | capital per idea (equal-risk or volatility-targeted budget), gross and net exposure caps, per-symbol cap across ideas (net-position check), sector cap, correlation budget (rolling 60-day correlation of idea P&L streams; a new idea that is too correlated waits for a slot), global daily loss stop | new (G6) |
| System | one kill switch (UI / API / CLI / hotkey), heartbeat, clock check (NTP offset < 250 ms), broker health, Analyzer/live mode check before every order, auth and scoped tokens, hash-chained audit log | flagfix1 G-SYS-*, P6b, new |
| Release | evidence-based promotion, typed approval, stepped caps, dry run, automatic demotion | P4/P6b + §4.2 |

---

## 7. Latency and capacity budget (intraday, 200 symbols, 5m bars)

| Step | Today | Target (phase C) | How |
|---|---|---|---|
| bar close → bar final in Historify | 60 s finality + REST fetch ≈ 0.4 s/symbol → **≈ 80–140 s** | **2–5 s** (WS bar close + grace for late ticks) | P6b stream as the decision source (T-5) |
| bar → intents (all ideas × symbols) | 23–28 ms per member-bar by replay → **≈ 5 s** for 200 | **< 200 ms** | `Engine::step_bar` (T-4) |
| risk + OMS journal | ~ms (SQLite WAL) | < 20 ms | unchanged |
| order → broker ack (OpenAlgo REST) | not measured on a real broker 🟡 | p95 < 1 s | measured in the 10 Analyzer sessions |
| **bar close → order at broker** | **≈ 2.4 min** | **3–8 s** | |

At these volumes the order rate stays far below the 10 orders/s threshold: 20 ideas × 5 positions × (entry + 2
protective orders + exit) ≈ 400 orders/day. The P4 limiter (2/s · 30/min · 200/h) has to be raised deliberately and
per broker profile.

---

## 8. Roadmap

**Superseded by [`TRADING_ENGINE_5_PHASES.md`](TRADING_ENGINE_5_PHASES.md)** (HLD diagram, 5 phases, and the
streamlining of the two backtest flows into ONE backtest process). The earlier phases map as follows:

| Earlier (A–E) | Now |
|---|---|
| - (only a cross-engine parity suite, A5) | **Phase 1 · One backtest process** (new, ≈ 34 d) |
| A contracts and intake · B1 evaluation | **Phase 2 · Idea intake + trade-data contracts** |
| B2–B5 lifecycle, paper host, portfolio risk, UI · C1–C2 Execution Gateway, order types | **Phase 3 · Paper trading on one order path** |
| C3–C5 step_bar, WebSocket bars, real Analyzer sessions · D trader node, VPS, approval, audit | **Phase 4 · Real-time + live readiness** |
| E pilot → scale | **Phase 5 · Live pilot → scale** |

Total ≈ 94 engineer-days. With two engineers: first ideas on paper in week 10, live pilot from week 16, pilot review
in week 20.

## 9. Testing strategy (what "done" means)

| Layer | What | Where |
|---|---|---|
| Contract | IdeaSpec / TradeEvent JSON schemas, versioned goldens, backward compatibility | new `tests/contract/test_ideas_contract.py` |
| Property | lifecycle state machine; OMS transitions (hypothesis `RuleBasedStateMachine`); step_bar == replay | extend flagfix1 suites |
| Time-travel | a frozen clock drives a full trading day: ideas in → signals → orders → fills → events out; **events must equal a backtest of the same day** | extend P4 `test_signals` time-travel |
| Fault injection | mock OpenAlgo: rejects, partial fills, lost acks, book lag, auth expiry, WS drops (flagfix1 `trading.py` + P6b `ws_server`) | `tests/lab`, `tests/integration` |
| Drills | kill-9 at every journal *and outbox* point; kill switch with a hung host; feed stop; clock skew | `lab drills run` on the real machine |
| Parity | nightly replay vs live (logic = 0), cross-engine parity, WS vs REST bars | scheduled + tests |
| Real broker 🟡 | 10 Analyzer sessions, then live dry run | owner, with reports |

---

## 10. Decisions needed from you

1. **Broker and hosting**: Angel One through OpenAlgo (current), or another broker from
   `docs/reports/flagfix1/BROKER_COST_AND_DATA_COMPARISON.md`? Which VPS (static IP) for the trading node?
2. **Idea engine interface**: push (the engine calls `POST /ideas`) or pull (Historify polls the engine)? Webhook for
   TradeEvents, pull API, or both? Which signature scheme (Ed25519 recommended)?
3. **Can the idea engine emit Strategy IR?** If not, what does it emit today? An adapter from its format to IR is
   cheaper than a second engine.
4. **Trial counts**: can the idea engine report how many variants it tested for each idea? (This is required for an
   honest deflated Sharpe.)
5. **Capital and caps**: total paper capital; pilot capital per idea; global daily loss stop.
6. **Approval authority**: you alone, or a second approver for live promotions?
7. **Scope order**: intraday MIS first, or swing CNC first? (Swing is more forgiving on latency and ready sooner.)

## 11. Top risks

| Risk | Mitigation |
|---|---|
| The generator overfits; ideas die in paper | DSR with external trial counts, hold-out embargo, robustness gates, cheap shadow stage for many ideas |
| Live diverges from the backtest (data, latency, fills) | one engine, as-seen data hashes, parity classes, measured latency fed back into the ExecSpec, WS/REST divergence monitoring |
| Order-path failure (duplicates, orphans, stuck orders) | journal-first OMS, idempotency keys, no blind resend, reconciliation, drills, single gateway |
| Regulatory / broker change (algo IDs, static IP, rate thresholds) 🟡 | confirmed with the broker before phase D; order rate far below the threshold; OpenAlgo kept current |
| Correlated ideas amplify a drawdown | portfolio risk layer, correlation budget, global loss stop |
| Key or secret leak | SecretBox, scoped tokens, https-only remote OpenAlgo (integration policy), auth forced on non-loopback, VPS hardening |
