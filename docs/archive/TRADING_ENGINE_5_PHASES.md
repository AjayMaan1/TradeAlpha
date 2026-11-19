# Trading engine: HLD and the 5-phase plan (with ONE backtest process)

| | |
|---|---|
| **Builds on** | `docs/TRADING_ENGINE_PLAN.md` (audit, contracts, risk framework, decisions) and `docs/INTEGRATION.md` (what the flagfix1 + flagfix2 merge left duplicated) |
| **Changes vs the plan** | The A-E roadmap is regrouped into **5 phases**. **Phase 1 is new**: the two backtest flows become **one backtest process**. The idea engine, paper trading and live trading are all judged by that process, so it has to exist first. |
| **Diagrams** | `docs/diagrams/trading-engine/` - `hld_target_architecture.png`, `backtest_two_flows_to_one.png`, `idea_lifecycle.png`, `roadmap_5_phases.png` (sources: `*.dot`, `roadmap.py`) |
| **Evidence** | ✅ = run in this sandbox. 🟡 = needs your broker, accounts or machine. |

---

## 1. High-level design

![HLD](diagrams/trading-engine/hld_target_architecture.png)

| # | Block | Responsibility | Built from |
|---|---|---|---|
| ① | Idea generation engine (yours) | Produces **IdeaSpec v1**: Strategy IR + ExecSpec + universe + risk + `trials_tested`, signed. Consumes **TradeEvents**. | - |
| ② | Idea Gateway | Schema, signature, IR compile, point-in-time universe, look-ahead fuzz, dedupe by IR sha. | `hbt.ir`, `hbt.fuzz` (flagfix1) |
| ② | Data platform → **lab snapshot v2** | The single frozen data source for every backtest: content-addressed partitions + point-in-time reference bundle. | flagfix2 `app/lab/data` + baseline data platform |
| ② | **Unified backtest pipeline** | One request model, registry, dispatcher, kernel, artefact format, trial registry and evaluation library (§2). | both flows, merged |
| ② | Evaluation gate → lifecycle → **your approval** | Evidence bundle; `received → validated → evaluated → shadow → paper → awaiting approval → live pilot → live`. | P4 promotion gate (flagfix2) + flagfix1 research/readiness |
| ③ | Market data | WebSocket → 1m bars (provisional → final); REST repairs finality. | P6b stream (flagfix2) |
| ③ | Strategy runtime | `hbt-core` `step_bar` per (idea, symbol) → **intents**: the same kernel and ExecSpec as the backtest. | flagfix1 core; `step_bar` new |
| ③ | Portfolio risk | Capital per idea, exposure / sector / symbol caps, correlation budget, global loss stop. | new |
| ③ | **Execution Gateway** | **One** order path: guardrails → journal-first OMS → adapters (Simulated / Analyzer / Live) → reconciliation, **one kill switch**. | flagfix1 OMS + guardrails, flagfix2 bridge + live gate |
| ③ | Trade store + outbox | TradeEvents written in the same transaction as the OMS; delivered by webhook, pull API and nightly Parquet. | new (P4 delivery code reused) |
| ④ | OpenAlgo + broker | Analyzer ON = paper, OFF = live; the mode is re-checked before every order. | existing |

The trading node is the same codebase running as `historify trader` on a VPS with a static IP. It is required for
live trading, not for paper (decision T-10 of the plan).

Lifecycle detail:

![Lifecycle](diagrams/trading-engine/idea_lifecycle.png)

---

## 2. One backtest process instead of two flows

![Two flows to one](diagrams/trading-engine/backtest_two_flows_to_one.png)

### 2.1 Why it is the first phase
After the integration, the same strategy can be backtested by **two independent pipelines** (flow A: hbt Lab,
flow B: P3). They differ at every stage:

| Stage | Flow A (flagfix1) | Flow B (flagfix2) | Consequence |
|---|---|---|---|
| Strategy | IR JSON, `lab_strategy_versions` | Python files (`exec_module`), `strategy_versions` | two registries, two freeze rules |
| Data | core snapshot / replica / live store (per series) | lab snapshot v2 (universe, content-addressed, PIT bundle) | two snapshot formats |
| Execution defaults | MIS square-off **15:10**, intrabar **pessimistic**, slippage ticks/pct | MIS square-off **15:15**, intrabar **stop_first**, size-bucketed bps | **the same idea gets two verdicts** |
| Costs | `hbt.costs` (statutory by date + broker tariffs) | `market/costs.DEFAULT_SCHEDULE` | two cost schedules to verify (D-13) |
| Jobs | `LabDispatcher` (one process per job) | `LabRunner` (process pool) | both compete for CPU; two cancel/restart paths |
| Engine | Rust `hbt-core`, one instrument | Python sim, portfolio + 1m execution | not comparable; paper (flagfix1) runs only the Rust one |
| Registry | `lab_runs`, `lab_trials` (append-only) | `backtest_runs`, `run_groups` | **trial counts split → deflated Sharpe too optimistic** |
| Evaluation | `hbt.research` (WFO, DSR, robustness) | `app/lab/eval` (WFO, DSR, PBO, Monte Carlo, regimes) | two implementations of the same statistics |
| API / UI / consumer | `/api/v2/lab/*`, Lab pages → `lab_paper` | `/api/v2/bt/*`, Backtests page → `signals` | two of everything downstream |

### 2.2 The single process (target)

```
RunRequest ─► Strategy registry ─► Lab snapshot v2 ─► ExecSpec + cost schedule ─► Compute dispatcher
(one API/CLI/UI)  (kind ir|python_tier|   (readiness, data-     (one defaults table)       (one pool, back-pressure,
                   sim_legacy; frozen;     quality policy,                                   cancel, restart-safe)
                   hold-out lock)          hbt adapter)                                              │
                                                                                                     ▼
Evidence bundle ◄─ Evaluation library ◄─ Run registry + ONE trial registry ◄─ Artefacts ◄─ Engine router
(→ lifecycle gates,  (metrics, WFO, DSR,   (every variant of every mode            (manifest +   hbt-core kernel: IR,
 paper, live)         PBO, MC, regimes,     counted)                                 Parquet,      Python tier, portfolio mode
                      robustness, capacity)                                          hashes)       sim: F&O / Track L / event study
```

**One process does not mean one engine for every product on day one.**
- **Equity strategies** (IR ideas and the Python tier) run on **one kernel, `hbt-core`**. It is the kernel paper and
  live will use, which is what makes "parity by construction" true.
- **F&O baskets, Track L rebalancing and event studies** keep the Python simulator as *engine kinds* behind the same
  pipeline (same request, data, registry, evaluation, UI) until they are ported. The router is one `if`, not a second
  flow.

### 2.3 What is kept, merged and retired

| Component | Decision | Notes |
|---|---|---|
| **Data** | **Keep lab snapshot v2** (flagfix2) as the only backtest input; add `hbt.Dataset.from_lab_snapshot` | ✅ **Spike**: the Rust core on lab-snapshot partitions vs on the core snapshot, same bars, `donchian_breakout_daily` on 3 symbols × 843 bars → **identical bars, trades, equity and `decisions_sha256`**; load 7 ms vs 28 ms. Core snapshots stay as the user-facing export; the weekly Lab snapshot (maintenance) switches format. The live-store dataset remains for exploratory runs only, flagged "not reproducible" and refused by the evaluation gate. |
| Corporate actions in the bundle | **Merge**: refdata `corporate_actions` (verified) with `instrument_corporate_actions` as manual overrides → one factor table | also the first step of the table unification listed in `INTEGRATION.md` §5 |
| **Execution model** | **ExecSpec v1 is canonical** (JSON schema, hashed); flagfix2 `ExecConfig` maps onto it; add size-bucketed slippage, session-edge widening and the ban-list filter to ExecSpec | a one-time defaults decision (§2.4) |
| **Costs** | **One versioned schedule** (statutory by trade date + broker tariff + DP charge + `contract_note` rounding option) | contract-note goldens; `verified` flag gates live |
| **Jobs** | **One ComputeDispatcher**: flagfix2's process pool (sweep fan-out, progress / cancel files) + flagfix1's back-pressure during downloads, below-normal priority and hard cancel | retire the other one |
| **Kernel** | **`hbt-core`** + a new **portfolio mode** (shared cash, max positions, entry ranking, point-in-time universe, multi-timeframe alignment; 1m magnifier already exists) | the largest task (8–10 d) |
| Python strategies | **Port** the 5 price-only built-ins (`buy_hold`, `ma_cross`, `mtf_pullback`, `orb_cpr`, `rsi2`) to IR; the 3 that read reference data (`breakout_delivery`, `event_risk`, `results_drift`) need new IR operators fed from the bundle (reference series, event flags) | user Python strategies run on the hbt **Python tier** (causal context, `LookAheadError`); `exec_module` of arbitrary files stays for your own code only |
| **Registry** | **One** `strategies / strategy_versions` (`kind`, content sha, frozen), **one** `runs` (`engine`, `mode`, `snapshot_id`, manifest, metrics), `run_groups`, **one append-only `trials`** (SQLite triggers, flagfix1) | data migration of `lab_*` and `backtest_runs` history; old tables become read-only views for one release |
| **Evaluation** | **One library** `app/lab/eval`: flagfix1's DSR (reproduces the published example) and robustness + flagfix2's PBO, Monte Carlo and regimes; DSR's N comes from the one trial registry plus the idea engine's `trials_tested` | E9 noise rejection stays a required test |
| Hold-out | both rules, one mechanism: research data must end before the cut-off (D-18) **and** the last 12 months only run frozen code | |
| **Artefacts** | flagfix1 format: `manifest.json` + Parquet `trades / fills / intents / equity`, file sha + content sha | byte-identical re-run check for both engine kinds |
| **API / CLI / UI** | `/api/v2/backtests/*`, `flask historify backtest ...`, **one Backtests page** (tabs: Strategies · Runs · Sweeps & walk-forward · Research (trials, DSR) · Snapshots · Compare) | `/api/v2/lab/runs*` and `/api/v2/bt/*` kept as aliases with a `Deprecation` header for one release |
| Consumers | `lab_paper`, `signals`, portfolios and F&O read the unified strategy versions and evidence | prepares the single order path of phase 3 |

### 2.4 Defaults to decide once (recommendation in bold)

| Setting | Flow A | Flow B | Recommended |
|---|---|---|---|
| MIS engine square-off | 15:10 | 15:15 | **15:10** (before the broker's auto square-off window) |
| Stop and target inside one bar | pessimistic | stop_first | **pessimistic** (same outcome for single brackets; stricter for trails) |
| Slippage | ticks / pct | size-bucketed bps (+ wider at session edges) | **size-bucketed bps + edge widening**, calibrated from paper fills in phase 5 |
| Participation cap | 10 % of bar volume (intraday) | 5 % | **5 %** for ideas (capacity honesty), configurable |
| Fill timing | next open | next bar, 1m execution | **next open on the 1m execution bar after decision latency** (ExecSpec `decision_latency_s` = measured) |

---

## 3. The five phases

![Roadmap](diagrams/trading-engine/roadmap_5_phases.png)

Estimates are engineer-days (d). The schedule assumes **two engineers**; hatched bars are calendar time that cannot
be compressed. One engineer: about 19 weeks of engineering plus the same calendar periods.

### Phase 1 · One backtest process (≈ 34 d · W1–W5) → M1

| # | Task | d | Owner | Exit test |
|---|---|---|---|---|
| 1.1 | Defaults decision (§2.4), written as an ADR and a single `DEFAULT_EXEC` | 1 | A | reviewed |
| 1.2 | Unified schema: `strategies` / `strategy_versions` (kind), `runs`, `run_groups`, append-only `trials`; data migration of `lab_*` + `backtest_runs`; read-only views for the old names | 3 | A | upgrade on a DB with history from both flows; autogenerate diff empty; old runs readable |
| 1.3 | Data: `Dataset.from_lab_snapshot` (spike → product code), merged corporate-action factors, lot/tick history and finality buffers in the bundle, weekly Lab snapshot on v2, data-quality policy | 4 | A | the §2.3 parity on every IR fixture, **with corporate actions**; adjusted vs raw goldens |
| 1.4 | One ExecSpec + one versioned cost schedule; `ExecConfig` mapped; contract-note goldens | 3 | A | the same trade list priced identically through both old entry points |
| 1.5 | One ComputeDispatcher (pool + back-pressure + priority + hard cancel + restart); retire the other dispatcher | 3 | A | cancel within 1 s; download throughput ≥ 90 % while 3 workers are busy; restart resumes |
| 1.6 | `hbt-core` **portfolio mode** (shared cash, max positions, ranking, PIT universe, MTF) + Python bindings | 8–10 | B | golden portfolios; determinism hashes (Linux == Windows); no regression on single-instrument goldens |
| 1.7 | Port the 5 price-only built-ins to IR; IR operators for reference series and event flags; port the other 3; cross-engine parity suite (old sim vs hbt) | 4 | B | trades identical, or each difference documented as a semantics change accepted in 1.1 |
| 1.8 | One evaluation library + DSR on the one trial registry | 3 | A | DSR example 0.9004; E9 noise rejection; P3 sweep/WFO tests pass on the new library |
| 1.9 | One API / CLI / Backtests page; deprecated aliases | 3 | A | both old UI suites rewritten against the new page; Playwright, no CSP errors |
| 1.10 | Consumers switched (`lab_paper`, `signals`, portfolios, F&O) | 2 | B | the whole suite green (today 1,223 + 62 browser tests) |

**M1 exit.** Every backtest in the app goes through **one** entry point, registry, dispatcher and evaluation library.
Every equity strategy runs on `hbt-core`. The old endpoints are aliases. The sim engine is used only for F&O baskets,
Track L and event studies.

**Fallback if 1.6 slips.** Python-tier multi-symbol strategies keep running on the sim *inside the unified pipeline*
(still one process), and the kernel convergence moves into phase 3. Ideas are not affected, because single-symbol IR
ideas already run on the kernel.

### Phase 2 · Idea intake and trade-data contracts (≈ 12 d · W6–W8, engineer A) → M2

| # | Task | d | Exit test |
|---|---|---|---|
| 2.1 | IdeaSpec v1 JSON schema, canonical form, Ed25519 / HMAC signature; `ideas` / `idea_versions` | 2 | schema goldens; tampered signature refused; same IR sha deduplicated |
| 2.2 | `POST /api/v2/ideas`, lifecycle read API, **scoped API tokens** (`ideas:write`, `trades:read`, `admin`), rate limits | 2 | wrong scope → 403 |
| 2.3 | Validation + look-ahead fuzz as jobs; embargo check (`source.data_end` before the hold-out cut-off) | 2 | flagfix1 IR fixtures accepted; a peeking IR rejected with the offending node |
| 2.4 | Evaluation gate on the **phase 1 pipeline**: WFO, DSR with N = `trials_tested` + registry, PBO, robustness, capacity, claimed vs reproduced → evidence bundle | 3 | positive control accepted, noise rejected with the external N |
| 2.5 | TradeEvent v1 + **outbox** + relay (webhook) + pull API with cursor + nightly Parquet | 3 | kill-9 between the OMS write and the relay → every event delivered after restart, consumer-side exactly once |

**M2.** The idea engine posts an idea; it is validated and evaluated with an evidence bundle, and every lifecycle
event can be pulled back.

### Phase 3 · Paper trading on one order path (≈ 20 d · W6–W10, engineer B) → M3

| # | Task | d | Exit test |
|---|---|---|---|
| 3.1 | **Execution Gateway**: one OMS (flagfix1 state machine), guardrails ∪ P4 limits, adapters Simulated / OpenAlgo Analyzer (flagfix2 mode check, client ids, managed OCO), one reconciliation, one kill switch; `lab_paper` and `signals` become strategy hosts that emit intents | 6 | both fault mocks (flagfix1 matching + flagfix2 sandbox) pass through the gateway; kill-9 drills; 0 duplicate / orphan orders |
| 3.2 | Idea runtime for shadow → paper on `hbt-core` (replay for now; step_bar comes in phase 4); TradeEvents from the gateway | 3 | the flagfix1 phase-exit test (5 sessions × 3 strategies) passes with ideas submitted through the API |
| 3.3 | Portfolio risk v1: capital slots, per-symbol / sector / gross caps, global daily loss stop | 3 | 30 correlated synthetic ideas → caps hold, the excess waits |
| 3.4 | Order types in the kernel and gateway: stop-limit, IOC, cancel-replace | 3 | golden scenarios |
| 3.5 | Lifecycle orchestrator (automatic transitions, pause / retire, audit) + Ideas pages (inbox, lifecycle, evidence, paper P&L vs backtest band) | 3 | state-machine property test; Playwright |
| 3.6 | Drills extended to the gateway + outbox; retire the duplicate order tables (`lab_orders` / `paper_orders` → one, views kept) | 2 | restart drills at every journal and outbox point |

**M3.** The first ideas run in shadow and paper (simulated + OpenAlgo Analyzer) through one order path. **The paper
calendar starts** (≥ 20 sessions and ≥ 30 round trips per idea, about W11–W15).

### Phase 4 · Real-time and live readiness (≈ 18 d · W9–W14, both engineers) → M4

| # | Task | d | Exit test |
|---|---|---|---|
| 4.1 | `Engine::step_bar` (incremental), checked every night against replay | 4 | 10k random bars: step == replay, bit-exact |
| 4.2 | WebSocket bars as the decision source, REST finality repair, `data_source` parity class, latency histograms | 3 | bar close → order ≤ 8 s p95 on the mock; provisional/final divergence measured on a real session 🟡 |
| 4.3 | `historify trader`: separate process and instance, morning warm-up snapshot, outbox back to the research node | 4 | restarting the research node does not touch trading; trader restart → reconcile → continue |
| 4.4 | VPS with a static IP next to OpenAlgo: service units, backups, Prometheus alerts, NTP check | 2 | failure drills: broker down, WS drop, disk full, clock skew |
| 4.5 | Approval workflow (evidence bundle, typed sign-off, optional second approver), live adapter on the gateway with the P6b gates (global switch, auth, Analyzer OFF before every order, dry run), stepped caps in code, automatic demotion | 3 | a demotion test per trigger; live refused unless every gate passes |
| 4.6 | Hash-chained audit log + security review (token scopes, secrets, webhook keys) | 2 | a tampered chain is detected |
| 👤 | Tariffs + statutory rates verified (D-13); static IP registered with the broker; algo / SEBI obligations confirmed; **10 real OpenAlgo Analyzer sessions** with 100 % reconciliation | owner | `verified = true`; session reports |

**M4** (after the paper calendar). Readiness review (flagfix1 1.7 checklist + the new items) and your sign-off.

> **Status (Phase 4 build):** 4.2–4.6 ✅ in code and tested ([PHASE4_LIVE.md](PHASE4_LIVE.md),
> [TEST_REPORT_PHASE4.md](TEST_REPORT_PHASE4.md)). 4.1 was **replaced** by a measured full-recompute budget
> (p95 ≈ 0.05 s per decision on 1,500 bars; `tests/lab/test_perf_live.py`; ADR-004 §7) - the step API would add a
> second kernel state machine for no latency need. 4.3 ships as roles + signed bundles + HMAC trade-event ingest;
> 4.4 as `deploy/` + SNTP check + the four live drills. The 👤 row remains owner work.

### Phase 5 · Live pilot → scale (≈ 10 d + calendar · W16–W20) → M5

| # | Task | d | Exit test |
|---|---|---|---|
| 5.1 | Dry run for 1 session, then the pilot: ≥ 20 sessions at minimum caps per idea; daily reconciliation with contract notes | 3 + calendar | 0 unexplained differences |
| 5.2 | Slippage calibrated from real fills → ExecSpec updated → ideas re-evaluated in the phase 1 pipeline | 3 | median real vs model drift ≤ model + 1 tick |
| 5.3 | Stepped caps (×2 per 10 clean sessions), capital allocation by realised risk, correlation budget tuned | 2 | caps enforced in code |
| 5.4 | Retire the Python sim for equities completely; plan F&O ideas (margin, expiry) and 1m cadence (once the WS parity is proven) | 2 | no equity path left on the sim |

**M5.** Pilot review → scale-up decision.

> **Status:** 5.1's tooling ✅ (dry run → live, daily broker reconciliation + contract notes, the pilot report
> `GET /api/v2/signals/deployments/<id>/pilot-report`); 5.3's stepped caps ✅ (`signals.live.steps`, `live-step`).
> 5.2 / 5.4 need real fills (calendar).

### Totals

| Phase | Engineering | Calendar (cannot be compressed) |
|---|---|---|
| 1 One backtest process | ≈ 34 d | - |
| 2 Idea intake + contracts | ≈ 12 d | - |
| 3 Paper on one order path | ≈ 20 d | paper period ≥ 20 sessions / ≥ 30 round trips per idea |
| 4 Real-time + live readiness | ≈ 18 d | 10 real Analyzer sessions; owner items |
| 5 Live pilot → scale | ≈ 10 d | ≥ 20 pilot sessions |
| **Total** | **≈ 94 d** | two engineers: **first ideas on paper in W10, live pilot from W16, pilot review W20** |

The earlier estimate (55–70 d) had no engine unification in it. Phase 1 adds ≈ 30 d; in return, paper and live
inherit one kernel, one cost model and one statistics library, and every later phase gets smaller and safer.

## 4. Risks specific to this phasing

| Risk | Mitigation |
|---|---|
| Portfolio mode in the Rust kernel takes longer than 8–10 d | fallback in phase 1 (sim inside the unified pipeline); ideas are unaffected |
| Unifying defaults changes historical results | ADR 1.1 first; every change shows in the cross-engine parity report; old runs stay readable with their original manifests |
| Migrating two registries loses history | read-only views + a migration test on a DB that has history from both flows |
| The paper calendar slips because of broker / OpenAlgo issues | the 10 Analyzer sessions start at M3 in parallel with phase 4, not after it |
