# flagfix1 vs flagfix2: in-depth comparison and unification plan

| | |
|---|---|
| **Question** | The two fixes overlap heavily. Before any new development: which features of which fix are better, and how do we unify them into one system? |
| **Method** | Code review of every overlapping area **plus 7 experiments run on the integrated code** (synthetic markets, the mock OpenAlgo with fault injection). The experiment scripts were temporary and were not committed; the results are reproduced below. |
| **Verdict in one line** | **flagfix1 has the stronger core**: causal strategy language, faster deterministic kernel, safer OMS, deeper guardrails, correct point-in-time rules. **flagfix2 has the broader product**: portfolio simulator, automatic exchange reference data, content-addressed snapshots, lifecycle and promotion, live gate, broker-side stops, delivery, auth. Each has **one defect that blocks real money**, and the other fix already solves it. |

![Best of both](diagrams/unification/best_of_both.png)

---

## 1. Experiments (what was measured)

| # | Experiment | Result | Verdict |
|---|---|---|---|
| X1 | **Same strategy, both engines.** SMA(20) long/flat, next-open fills, zero costs; 3 symbols × 2,408 daily bars. | Entry/exit **days identical on 100 % of the 445 compared trades**; 436 identical prices, the other 9 differ by exactly 1 tick (synthetic opens are 80 % off the ₹0.05 grid; the engines round off-grid prices differently - real NSE prices are on-grid). flagfix2 has one more trade on 2 of 3 symbols = a force-close at the end of the data (flagfix1 leaves the position open). Time: **hbt 0.012–0.020 s vs sim 0.11–0.16 s per symbol (~10×)**. | The kernels agree on the core semantics. hbt is faster; unify the two conventions (tick rounding, end-of-data). |
| X2 | **Cost models, same trades.** Same broker tariff (0.03 %, cap ₹20). | ₹2 lakh intraday round trip: flagfix2 **₹36.85 + ₹80.85**, exactly flagfix1's hand-checked Rust test (3,685 + 8,085 paise). | Identical on current rates. flagfix1 also has the **correct pre-Oct-2024 options exchange fee (0.0495 %)**; flagfix2 applies 0.03503 % to all dates. |
| X3 | **Deflated Sharpe**, published worked example (Bailey & López de Prado 2014) + edge cases. | Both: SR0 = 0.1132, **DSR = 0.9004**; identical for N = 1, 2, 10,000. | Both correct; unify on one. |
| X4 | **Look-ahead.** A strategy that trades on TOMORROW's close. | flagfix2 ran it: **+2,204 %, Sharpe 4.58, no warning, and `freeze()` accepted it** (so it could be deployed). flagfix1's IR refuses it at compile time (`E-CAUSAL: shift argument must be >= 1 … reads a FUTURE bar`; no other way to reference the future); its Python tier raises `LookAheadError`. | **flagfix1 decisively.** Blocking for machine-generated ideas. |
| X5 | **Lost acknowledgement + 10 s order-book lag** (fault-injecting mock) on flagfix2's paper order path. | **Two `BUY 10 SBIN` orders at the broker; Historify knows only one.** "Not in the order book yet" is read as "never reached the broker" and the order is resent after 5 s. The same code sends live orders. | **flagfix1's OMS decisively** (never resends blindly; a unique tag per order; passes its lost-ack / duplicate-row / partial-fill fault test with 0 duplicates). |
| X6 | **Where do protective stops live?** (code) | flagfix2 places an **SL-M stop + LIMIT target at the broker** after the entry fill (OCO managed). flagfix1 keeps stops in the engine and sends a MARKET order when they trigger → **a crash leaves open positions unprotected**. | **flagfix2 decisively** for live trading. |
| X7 | **Snapshots**: 10 symbols × 9 years daily, built twice. | flagfix1 core snapshot: 540 KB → **1,080 KB** (full copy each time). flagfix2 lab snapshot: 762 KB → **783 KB** (content-addressed; the rebuild stored 20 KB). The earlier spike: the Rust core reads lab-snapshot partitions **bit-identically** and 3–4× faster (7 ms vs 28 ms). | **flagfix2** as the backtest input; flagfix1's format stays as the human-readable export. |

---

## 2. Area by area

### 2.1 Research and backtesting

| Area | flagfix1 | flagfix2 | Take |
|---|---|---|---|
| Strategy language | **Strategy IR**: JSON, 86 operators, schema, bounded params and constraints, canonical hash, `explain`, **causal by construction** (X4), look-ahead fuzz harness; Python tier with `CausalSeries` | Python classes loaded with `exec_module`; features are look-ahead-tested in the **test suite only** (registered features), user strategies never (X4); richer data access (index series, events, delivery %, fundamentals) | **IR + hbt Python tier.** Add the missing data access as IR operators / tier context (reference series, event flags, delivery %). Keep `exec_module` only for your own code, and put a **corrupt-the-future gate** at freeze. |
| Execution kernel | Rust, single instrument, brackets, ExecSpec, intrabar policies incl. the 1m magnifier, byte-identical re-runs, cross-platform hashes, ~10× faster (X1) | Python, **portfolio-level** (shared cash, margin, max positions, participation, circuits, split-adjusted quantities), 1m execution for 15m signals, F&O baskets, Track L, live-replay mode; differential test against a literal reference simulator | **hbt-core + a portfolio mode** ported from flagfix2's semantics (its spec in `sim/types.py` is precise and tested). The sim stays for F&O / Track L / event studies until they are ported. |
| Execution defaults | MIS square-off 15:10, intrabar pessimistic, participation 10 %, slippage ticks/pct | 15:15, stop_first, 5 %, **size-bucketed bps + session-edge widening** | 15:10 · pessimistic · 5 % · flagfix2's slippage model (more realistic) - as one ADR |
| Costs | **12 broker tariffs**, statutory by date (incl. the historical options fee), integer fixed point, refuses to guess brokerage | one broker; **contract-note rounding**; used by portfolios, tax, F&O | one schedule file: flagfix1 data + flagfix2 rounding, consumed by both kernels (X2 proves they agree) |
| Point-in-time | `available_at` = the **download planner's finality** (per broker, measured), partial last bar of a session, W/M | `pit.py`: D at close + 15 min; intraday lag **0 s by default for research** (optimistic vs live's ~60 s) | **flagfix1 rule** everywhere (one module) |
| Snapshots | per-series Parquet + manifest (export-friendly), lot/tick + corporate actions per series | **content-addressed partitions + PIT reference bundle** (calendar, CA factors, index membership, delivery, ban list, events), readiness check, GC (X7) | **flagfix2** for backtests; flagfix1 as export |
| Statistics | WFO (month-aligned, min-trades selection), DSR, robustness (neighbours, costs × 1.5, +1 bar latency), **end-to-end noise-rejection test with a positive control** | WFO (rolling / anchored, **purge gap**, parameter stability), DSR, **PBO (CSCV)**, **Monte Carlo**, **regime splits**, 1×/2×/3× slippage | **union** in one library (X3: the DSR cores are identical) |
| Trial registry, hold-out | **append-only trials (SQLite triggers)**, research projects, cohorts, D-18 cut-off | run groups, **12-month hold-out lock for unfrozen code** | one append-only registry (all modes count towards N) + both hold-out rules |
| Jobs | own process per job, **below-normal priority, download back-pressure, hard cancel** | **reused pool, sweep fan-out, memory-cap refusal, worker-death recovery**; no priority, no back-pressure | one dispatcher with all properties (today both run at once: 2 + 3 workers on 4 cores) |

### 2.2 Reference data

| Area | flagfix1 | flagfix2 | Take |
|---|---|---|---|
| Corporate actions | manual / NSE CSV import with PURPOSE parsing; **candidates detected from broker revisions** (silent adjustments) | **automatic** from the NSE feed, **verified against the bhavcopy's implied factor**, status (verified / conflict / implied_only), symbol-change lineage, PIT `announced_at` | flagfix2 master table; flagfix1 rows become manual overrides; flagfix1's revision candidates feed flagfix2's verification |
| Calendar | OpenAlgo sync: every exchange, special sessions (Muhurat, MCX evening) | NSE holiday master (official, future) + **derived from bhavcopy** (past truth) + ledger rebuild | both (source ownership fixed at the integration) |
| Lot / tick | time-versioned from the broker master (current values) | F&O lot per contract from the exchange bhavcopy (historical) | exchange history for the past, broker master for today |

### 2.3 Paper and live trading

| Area | flagfix1 (`lab_paper`) | flagfix2 (`signals`) | Take |
|---|---|---|---|
| OMS | **journal-first state machine, compare-and-set, UNIQUE idempotency key, per-order broker tag, never blind resend, restart reconcile, kill-9 drills** | rows first ("placing" before the call) and order-book adoption, but a per-deployment tag, symbol/qty matching and **resend when not yet visible → duplicate (X5)** | **flagfix1** |
| Protective orders | engine-held, MARKET when triggered (**unprotected on a crash**, X6) | **SL-M + LIMIT at the broker, OCO managed**, exits cancel protectives | **flagfix2** protectives on the flagfix1 OMS; engine-triggered exits only for what the broker cannot hold (trails, time exits) |
| Risk | **19 guardrails**: whitelist, notional / lots, price collar, fat finger, exposure, margin, order rate, trading window, protective stop required, daily loss → flatten + pause, drawdown → kill, consecutive losses, staleness, bad tick, heartbeat, clock | **global caps across deployments**, event-day blocks, quantity reduced to fit, **exits never blocked (except kill)** | union |
| Strategy host | per-bar host on the Rust core; **decision journal with the as-seen bars**; replay equality by causality | replay at each trigger (EOD / evening / 15m); completeness gate; catch-up / missed triggers | triggers and the completeness gate from flagfix2; per-bar IR host and the as-seen journal from flagfix1 (then `step_bar`) |
| Books | simulated (G1, shadow quotes with depth) / Analyzer (G2) | shadow / Analyzer / live books; execution-quality report | G1 shadow quotes + flagfix2 books and report |
| Parity | nightly replay equality, as-seen hash | **divergence classes** (data_revision, late_data, timing, logic), auto-pause on logic | both |
| Lifecycle | cohorts → paper session; **T0 readiness checklist + sign-off; drills** (kill-9, hung kill switch, feed stop, auth expiry) | **deployments with stages, versions, evidence-based promotion, journal, delivery (SSE, HMAC webhook, Telegram, digest), audit log** | flagfix2 lifecycle; flagfix1 readiness and drills become gate inputs |
| Live | none (G3 planned) | **live gate**: global switch + phrase, auth, complete limits, Analyzer OFF before every order, dry run, live caps, live book | flagfix2, on the unified gateway |
| Kill switch | DB-only, works with a hung host (< 2 s) | cancels at the broker, optional flatten, live included | one switch (linked at the integration → one implementation) |

### 2.4 Operations and platform

| Area | flagfix1 | flagfix2 | Take |
|---|---|---|---|
| Notifications | system alerts (jobs, broker session, disk, issues), throttling, OpenAlgo relay, Telegram | signal delivery: HMAC webhook + retries + dead letter, SSE, Telegram, digest | one bus; **one** Telegram configuration (today there are two settings) |
| Live bars for paper | `bar_feeds` (persistent REST follow, latency measured, instrument cap) | UniverseFollow (per bar close) + **WebSocket bar builder** | one market-data service |
| Security | WSGI host/CSRF guard, strict broker-host policy, legacy secret scrub | users, sessions, API tokens, lock-out, forced auth off-loopback, order-route token, audit log | both (host policy and error model already unified) |
| Backup / restore | hardened backup (disk check, no rotation of a failed backup), lock-guarded restore | broader content (refdata, news, strategies) | done at the integration |
| Hardening | disk guard, purge, replica, instrument refresh, notified_at watcher | retention, pre-upgrade backup, metrics | both |
| Tests | Rust build + clippy, determinism across processes, fault-injecting mock, drills, bootstrap with offline fallbacks | parallel runs, instance templates, sandbox demo, legacy layer | both in one runner |

### 2.5 Only in one fix (keep as is)
- **flagfix1:** replica for notebooks, readiness checklist, drills, Lab research UI.
- **flagfix2:** News & Filings archive, market intelligence, scanner + regime + E17, portfolios (Track L with tax), F&O research, WebSocket stream, metrics.

---

## 3. Defects to fix first (found by this analysis)

| # | Sev | Fix | Defect | Evidence | Remedy (from the other fix) |
|---|---|---|---|---|---|
| D1 | **Critical** (real money) | flagfix2 | Duplicate order after a lost acknowledgement when the order book lags | X5 | per-order unique tag; never resend an unknown-outcome order; resolve only by tag, or after a lag window + position check (flagfix1 OMS) |
| D2 | **Critical** (false results) | flagfix2 | User strategies are never checked for look-ahead; a peeking strategy can be frozen and deployed | X4 | corrupt-the-future check at freeze and on every run of unfrozen code; IR / Python tier for new strategies |
| D3 | **High** (real money) | flagfix1 | Protective stops live only in the engine | X6 | broker-side SL-M / LIMIT with OCO (flagfix2) |
| D4 | High | flagfix2 | Backtests assume intraday bars are usable the instant they close (lag 0) while live sees them ~60 s later | code | flagfix1 `available_at` |
| D5 | Medium | both | Trial counts split across two registries → deflated Sharpe too optimistic | code | one append-only registry |
| D6 | Medium | flagfix2 | Backtest pool has no priority or download back-pressure; both dispatchers oversubscribe the CPU | code | flagfix1 dispatcher properties |
| D7 | Low | flagfix2 | Options exchange fee before Oct 2024 understated (0.03503 % instead of 0.0495 %) | X2 | flagfix1 schedule |
| D8 | Low | both | Different execution defaults → the same idea gets two verdicts | X1, code | one defaults ADR |

---

## 4. Unification plan (before any new development)

> **Status:** U0-U5 implemented with the recommended decisions and every open item closed (ADR-003) - see
> `UNIFICATION_STATUS.md`. Next: `NEXT_PHASE_HLD.md`.

Each step ends with **both test suites green**, now 1,223 + 62 browser tests, plus the new tests listed.

| Step | What | d | Exit test |
|---|---|---|---|
| **U0 Safety fixes** (first, small) | D1: per-order tag + no blind resend in the signals path · D2: corrupt-the-future gate at `freeze` and for unfrozen runs · D4: `pit.py` delegates to `available_at` · D6: priority + back-pressure in the P3 runner · D7 · one Telegram setting | **3** | X5 rerun → 1 order at the broker; X4 rerun → freeze refused with the offending feature; download throughput ≥ 90 % with a sweep running |
| **U1 Shared foundations** | one cost schedule file (both kernels) · one ExecSpec defaults ADR (D8) · one corporate-actions table (refdata master + manual overrides + revision candidates) · one lot/tick history · one PIT module | **7** | X2 / X1 conventions identical; adjusted-price goldens; CA migration keeps every existing row |
| **U2 One backtest process** | = Phase 1 of `TRADING_ENGINE_5_PHASES.md`: lab snapshot as the only input, one registry + append-only trials (D5), one dispatcher, hbt-core portfolio mode, strategies ported, one evaluation library, one API / UI | **34** | M1 exit criteria; E9 noise rejection; cross-engine parity report |
| **U3 One order path** | Execution Gateway: flagfix1 OMS + journal + drills · flagfix2 broker-side protectives (OCO), books, execution-quality report, live gate and dry run · guardrails ∪ P4 limits · one reconciliation · one kill switch · one orders table | **12** | both fault mocks (lost ack, book lag, duplicate rows, partial fills, rejects) → 0 duplicate / orphan orders; kill-9 at every journal point; a crash with an open position leaves a resting stop at the broker |
| **U4 One paper lifecycle** | flagfix2 deployments / stages / promotion / journal / delivery as the lifecycle; the flagfix1 per-bar IR host and as-seen journal as the runtime; parity = flagfix2 classes + flagfix1 as-seen hashes; readiness checklist and drills as gate inputs; cohorts become deployment sources | **8** | the P4 time-travel day test and the flagfix1 phase-exit test both pass on the unified lifecycle |
| **U5 Ops and UI** | one notification bus (system alerts + signal delivery) · one market-data follow service (bar_feeds + UniverseFollow, WS as an optional source) · one mock OpenAlgo (matching engine + sandbox admin) · one nav (Research / Lab pages merged) · one test runner | **6** | UI suite; no duplicate settings left (a settings audit test) |
| **Total** | | **≈ 70 d** | two engineers ≈ 7–8 weeks (U0 + U1 in week 1–2; U2 ∥ U3 after U1) |

**Effect on the trading-engine roadmap.** U2 is Phase 1 of the 5-phase plan, and U3 + U4 replace the gateway and
lifecycle work of its Phase 3.
- **Phases 2 and 4** (idea intake, real-time, live readiness) start after U3.
- **New development after the unification:** ≈ 12 d (phase 2) + ≈ 8 d (the rest of phase 3: idea runtime, portfolio
  risk, ideas UI) + ≈ 18 d (phase 4) + ≈ 10 d (phase 5).

**Order of work (risk first).**
1. **U0 immediately.** D1 and D2 are real-money and false-result defects, and each fix is a few hundred lines.
2. **Then U1**, because every later step depends on one cost model, one set of defaults and one corporate-action source.
3. **Then U2 and U3 in parallel** (different engineers, no shared files except the registry schema, which lands in U1/U2 first).
4. **Then U4 and U5.**

**Decisions needed from you before U1:**
1. The execution-defaults ADR: recommended 15:10, pessimistic, 5 % participation, flagfix2 slippage model.
2. Agree that new strategies are written as IR or on the hbt Python tier. Existing flagfix2 strategies get ported; arbitrary Python stays for your own experiments behind the look-ahead gate.
3. Agree that live trading is allowed only through the unified gateway (U3). The current P6b live path stays disabled until then, because of D1.
