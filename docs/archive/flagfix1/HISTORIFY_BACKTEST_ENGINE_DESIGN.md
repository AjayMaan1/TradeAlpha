# Historify Lab: design of Historify's own backtesting and strategy-research engine

| | |
|---|---|
| Status | **v1.2 (+ Phase 1.2 / 1.3 implementation notes in §8.5, Appendices B, C, F): core (Rust) and guardrails (§16) approved; plan = 1 year of data + 3-month paper phase (§0, D-17…D-26).** Remaining choices decided in §0. Broker choice is pending the owner's review of [BROKER_COST_AND_DATA_COMPARISON.md](BROKER_COST_AND_DATA_COMPARISON.md). |
| Scope | Backtesting, strategy research/generation, paper trading; later live execution through OpenAlgo |
| Builds on | [BACKTESTING_ENGINE_EVALUATION.md](BACKTESTING_ENGINE_EVALUATION.md) (engine review), the Historify v2 data layer |
| Evidence | Every performance or compatibility claim is tagged **[E#]** or **[T#]** and measured by a script in [`backtest_evaluation/`](backtest_evaluation/README.md). Appendix A lists them all. |
| Decision asked | Build **Historify Lab**: a Rust execution core with a Python research layer, on Historify's data. Open decisions are in §0 and §24. |

---

## 0. Decision record (v1.1)

The owner approved the Rust core and asked for the most suitable option to be picked for every other open point.
Trading goes live in stages: first behind guardrails, then live.

| # | Topic | Decision | Why (evidence) |
|---|---|---|---|
| D-01 | Execution core | **Rust**: PyO3 0.30 + maturin, one `abi3-py312` wheel per OS, rayon; arrow 59 + pyo3-arrow 0.20 pinned in lockstep | Approved. [T1][T8][T10] |
| D-02 | Strategy model | **IR-first** (JSON, evaluated natively in Rust). numba `@cfunc` and Python tiers are opt-in, trusted code. | [T2][T5] |
| D-03 | Process model | Lab **worker processes** reading snapshots / the research replica; never the live DuckDB file | [E1] |
| D-04 | Money | i64 paise / ticks, per-charge contract-note rounding; f64 only for analytics | ADR-3 |
| D-05 | Calendar source | **OpenAlgo `market/holidays` + `market/timings`** (years 2020–2050 per the SDK, per exchange, including partial MCX sessions) for 2020 onwards. **`exchange_calendars` XBOM** for 2006–2019. The `market_calendar` table keeps manual overrides (special/mock sessions). Content hash in every manifest. | Verified in `openalgo 2.0.5` `utilities.holidays/timings`; comes through the connection Historify already has. XBOM checked in the engine review. |
| D-06 | Corporate actions | **Primary: NSE/BSE corporate-action files** (official, broker-independent; imported in Settings, scheduled monthly). **Secondary: Upstox corporate-actions API** (by ISIN) where an Upstox login exists. **Safety net:** the CA-candidate audit (overnight gaps matching split/bonus ratios). | Exchange files do not depend on the broker choice. The Upstox API is verified in `upstox-python-sdk 2.30` (`fundamentals/{isin}/corporate-actions`: ratio, amount, event details). |
| D-07 | Index membership | **NSE Indices constituent files + reconstitution announcements**, imported with `valid_from` / `valid_to`; NIFTY 50/100/500 first | Only official source; avoids survivorship bias |
| D-08 | Margin | Backtest: L0 (segment %) for screening, L1 SPAN-approximation for validation, **calibrated against the OpenAlgo `margin` API** (SPAN + exposure from the broker). Live: **pre-trade check through the OpenAlgo `margin` API** plus a buffer. NSE SPAN files (L2) only if F&O portfolio fidelity demands it. | `openalgo 2.0.5` `margin(positions=[…])` returns `span_margin`, `exposure_margin` |
| D-09 | Default ExecSpec | `next_open`; bracket live on the entry bar; `pessimistic` intrabar (the 1m magnifier is the default for *validation* runs with brackets ≤ 0.3 % or bars ≥ 15m); 10 % participation cap intraday; `no_entry_after 14:55`; **engine square-off at 15:10 (NSE/NFO/BSE), before the broker's auto square-off window**, avoiding the ₹20–50 + GST auto-square-off fee and its slippage | [E5][T9]; fees in the broker comparison |
| D-10 | Indicators | Native Rust library. Default definitions = **TA-Lib**. Optional `tv` flavour matching TradingView, cross-checked against the **OpenAlgo indicator library** (MIT, compiled backend), because OpenAlgo users validate on TradingView charts. | [T4]; `openalgo 2.0.5` indicators (TradingView Pine v6 semantics) |
| D-11 | Search | optuna 5 (TPE default; NSGA-II for multi-objective) + our own grid/random sweep driver; GP operators on the IR | ADR-11 |
| D-12 | Data vs execution broker | **Supported and recommended where it helps:** one OpenAlgo connection can be the *data* provider and another the *execution* broker. Lab validates that they agree with `broker_data_compare.py` and reconciles live fills against modelled fills. | Broker comparison: the broker with the best data is not the cheapest for execution |
| D-13 | Broker profiles | Seed Lab broker/cost profiles from `broker_evaluation/broker_tariffs.toml` with `verified = false`. **Live promotion is blocked while the active broker profile or statutory schedule is unverified.** | Guardrail G-SYS-7 |
| D-14 | Live scope | **Staged: paper → OpenAlgo Analyzer (sandbox) → guarded pilot with real orders → scaled live**, with automatic demotion (§16) | Owner instruction; Analyzer mode verified in `openalgo 2.0.5` (`analyzertoggle`, sandbox order ids `SB-…`) |
| D-15 | Platforms | Windows x64 + Linux x64 supported (CI). **macOS best-effort** (built, not gated). | Matches Historify's CI matrix |
| D-16 | Live prerequisites | SEBI retail-algo framework items (static IP, broker API registration, any algo-ID / order-rate thresholds) are handled per broker before stage G3 (§16.5) | Regulatory; verify with the chosen broker |
| D-17 | Data scope (phase 1) | **1m: the last 12 months; D: 10 years** (D is ~100 requests, and it gives daily strategies and regime context). Accumulate forward from day 1 with the post-close schedule. Extend 1m depth later only if a strategy needs it. | Backfill 5–21 min for 200 symbols at any mainstream broker; ~300 MB incl. 3 forward months [P4] |
| D-18 | Research data cut-off | **T0 = paper start.** Research, selection and tuning use data **before T0 only**. Data after T0 is the out-of-sample test of the paper cohort. Changing a strategy creates a new version and restarts its paper clock. New candidates join in monthly cohorts. | Protects the only true out-of-sample evidence |
| D-19 | Paper-phase success criteria | **Operational and execution validation**, not proof of edge: evaluator parity 100 %, bar completeness ≥ 98 %, 0 duplicate/orphan orders, G2 reconciliation 100 %, measured latency and shadow spread cost fed back into the ExecSpec. P&L is judged as *consistency* with the backtest's bootstrap band, pooled across the cohort. | 62 sessions detect a dead Sharpe-2 strategy only 26 % of the time; the significance floor is Sharpe 3.3 [P1] |
| D-20 | Strategy cadence in phase 1 | **≥ 5m bar cadence** (5m/15m intraday, daily/swing) on a liquid universe (NIFTY 100). 1m-cadence strategies wait for WebSocket bars (D-22). | Polling delivers 5m bars at close + 60–143 s for 50–200 symbols; 1m is infeasible by polling [P3] |
| D-21 | Market-data service | A **system-owned Follow**: persistent (no browser heartbeat), batched per bar close for the whole paper universe, priority above backfills, budget-aware. Replaces the chart Follow limits (5 series, 120 s TTL) for Lab use. | `follow.py` limits [P3] |
| D-22 | WebSocket bars (phase 2) | OpenAlgo `feed` (LTP/quote) → locally built bars for 1m strategies. Parity with the next-day historical bars checked nightly (revision machinery). | `openalgo 2.0.5` `feed.subscribe_quote`, reconnect re-subscribe |
| D-23 | Decision latency in backtests | The ExecSpec gains `decision_latency_s`. Fills are simulated at the **1m bar open ≥ signal close + latency** (Historify 1m data). The default equals the latency measured in paper (initially 90 s). | Method validated; on synthetic data the effect is noise (±1 bp/trade), on real data it must be measured [P2] |
| D-24 | Shadow quotes in paper | Every paper order captures `quotes` (bid/ask/LTP), or `depth` for larger orders, at decision time. The simulated fill crosses the spread (buy at ask, sell at bid). This gives an implementation-shortfall estimate before real money. | OpenAlgo quotes include bid/ask; depth top-5 |
| D-25 | Decision journal | Paper/live decisions store a hash of the *as-seen* bars. Nightly replay on the final stored bars flags any revision that would have changed a decision. | Revision detection (existing) |
| D-26 | Snapshots | An automatic **weekly research snapshot** (Saturday maintenance) of the Lab universe (D + 1m). Backtests bind to it. | Reproducibility while data grows |

---

## Contents
0. [Decision record (v1.1)](#0-decision-record-v11)
1. [Summary](#1-summary)
2. [Why build, and what "better" means](#2-why-build-and-what-better-means)
3. [Goals, non-goals, principles](#3-goals-non-goals-principles)
4. [Users and use cases](#4-users-and-use-cases)
5. [Requirements](#5-requirements)
6. [Technology decisions (ADRs)](#6-technology-decisions-adrs)
7. [Architecture](#7-architecture)
8. [Data layer](#8-data-layer)
9. [Strategy layer](#9-strategy-layer)
10. [Execution engine](#10-execution-engine)
11. [Indian market model](#11-indian-market-model)
12. [Costs, taxes and charges](#12-costs-taxes-and-charges)
13. [Portfolio, sizing and risk](#13-portfolio-sizing-and-risk)
14. [Metrics and analytics](#14-metrics-and-analytics)
15. [Research and strategy generation](#15-research-and-strategy-generation)
16. [Paper and live trading: guardrails first, then live](#16-paper-and-live-trading-guardrails-first-then-live)
17. [Reproducibility and provenance](#17-reproducibility-and-provenance)
18. [Persistence: schema and artefacts](#18-persistence-schema-and-artefacts)
19. [Jobs, APIs, CLI and SDK](#19-jobs-apis-cli-and-sdk)
20. [User interface](#20-user-interface)
21. [Performance engineering](#21-performance-engineering)
22. [Verification and quality](#22-verification-and-quality)
23. [Security, packaging, operations](#23-security-packaging-operations)
24. [Roadmap, risks, decisions](#24-roadmap-risks-decisions)
- Appendices: [A Evidence](#appendix-a-evidence-register) · [B ExecSpec](#appendix-b-execspec-schema) · [C Strategy IR](#appendix-c-strategy-ir) · [D DDL](#appendix-d-meta-db-ddl) · [E API](#appendix-e-api-catalogue-v1) · [F Crate layout](#appendix-f-code-layout) · [G Glossary](#appendix-g-glossary)

---

## 1. Summary

**Yes, build our own.** The engine review showed that no existing engine combines the four properties Historify needs:
1. India-correct semantics;
2. licence compatibility with AGPL-3.0;
3. speed at the scale of Historify's data;
4. integration with Historify's integrity and provenance model.

Two further rounds of hands-on experiments (T1–T10) show that a purpose-built engine on current technology is both
feasible and measurably better.

**What we build.** *Historify Lab* is an optional component (`historify[lab]`) made of:
- **`hbt-core`**: a Rust execution engine (PyO3 + rayon). Deterministic, exact money arithmetic, India market rules.
  A 293 KB wheel; one abi3 wheel covers Python 3.12–3.14.
- **`hbt`**: a Python layer for data access, the strategy API, research workflows and metrics. Data moves zero-copy
  through **Arrow PyCapsules** (DuckDB → Rust, no pyarrow).
- **A versioned Strategy IR** (JSON). Strategies are data: they can be generated, hashed, diffed and shared safely, and
  are evaluated natively and fused inside the Rust loop. Custom logic can be compiled (numba `@cfunc` called from
  Rust) or written as plain Python.
- **A research pipeline**: massive parallel search (grid, Bayesian, genetic over the IR), then walk-forward, CPCV,
  PBO, deflated Sharpe and robustness tests. A trial registry makes it impossible to "forget" how many things were
  tried.
- **Integration with Historify**: backtests are jobs, results go to the meta DB plus Parquet, there are Backtests and
  Research pages, and every run is bound to a checksummed snapshot.
- **Later**: paper and live modes that run the *same* strategy object on Historify's live bars and route orders to
  OpenAlgo.

**Why this is better, measured on the same data and specs as the engine review:**

| | Best off-the-shelf | Historify Lab (prototype measurements) |
|---|---|---|
| Intraday correctness (bracket stop, MIS square-off, next-open fills) | backtesting.py 49/50 exact; vectorbt 10–11 % median deviation **[E5]** | **50/50 exact** against the independent reference (1e-13), in numba *and* Rust **[T1]** |
| 12,200 intraday backtests (39k bars each) | vectorbt: 50–60 GB projected (7 GB at 1,600, stopped); backtesting.py ~54 min **[E4]** | **3.0–3.2 s at 387 MB** (4 threads) **[T1][E9]** |
| Target scale: 200 symbols × 10 years of 1m (187.5 M bars), 4 variants | not attempted | **10.3 s at 227 MB**, streaming, single thread **[T6]** |
| Shared-cash portfolio, 50 symbols × 195k 1m bars | backtesting.py cannot; others are slow | **0.09 s (9.3 ns per symbol-bar)** **[T7]** |
| Cold start | vectorbt 9–24 s; Nautilus 2.4–4.7 s import | **0.29 s** (Rust, no JIT) **[T1]** |
| Loading 9.8 M bars into the engine | 0.7–2.9 s via numpy conversion | **0.175 s**, zero-copy Arrow stream **[T10]** |
| Indian costs (side-aware, time-versioned, capped brokerage) | only Nautilus fully; backtesting.py cannot (exit side lost) **[E8]** | built in, versioned by trade date |
| Overfitting control | none built in | walk-forward + DSR rejected the best of 12,200 noise strategies (IS +1.33 → OOS −0.82) **[E9]** |
| Licence / footprint | Commons Clause (vectorbt), +761 MB (Nautilus) | AGPL-compatible; core +0.3 MB, optional numba +186 MB |

**Before any backtest result can be trusted, the data layer needs to be made "backtest-grade"** (§8.2, phase M0):
a historical calendar, corporate actions, `available_at` timestamps, instrument history, a research replica and
manifest v2. These were the top findings of the engine review and they apply to *any* engine.

---

## 2. Why build, and what "better" means

### 2.1 The decisive finding

With semantics held identical, every engine computes the same numbers (errors around 1e-15) **[E3]**. Results differ
only because of *semantics*: when a fill happens, whether a stop is live on the entry bar, which event wins inside a
bar, how costs apply, how returns are annualised. **[E5]** showed these choices move a realistic NSE intraday result
by a median of ~10 %, and a 2.4× Sharpe inflation came from one annualisation default.

**Whoever owns the semantics owns the result.** Adopting an engine means adopting its semantics, licence, memory
model and release cadence. Building lets Historify:
- define the semantics once (the *ExecSpec*), version them and prove them with tests;
- use the same semantics in backtest, paper and live.

### 2.2 "Better" is defined by measurable properties, not features

| Property | Definition | How it is enforced |
|---|---|---|
| **Correct** | Matches hand-computed golden scenarios and independent reference implementations bit-for-bit (money) or to ≤1e-12 (analytics) | Oracle suite in CI (§22) |
| **Honest** | No look-ahead; calendar-correct annualisation; costs on by default; multiple testing accounted for | Look-ahead fuzzing, `available_at` joins, trial registry, DSR gate |
| **Reproducible** | Same inputs give byte-identical results, forever | Snapshot binding, run manifest, deterministic kernel |
| **Fast** | Interactive: < 0.5 s cold start, < 50 ms for one 10-year 1m symbol. Research: ≥ 3,000 intraday backtests/s on 4 cores | Benchmarks with CI regression gates (§21) |
| **Efficient** | Memory bounded by the working set, not by data size × variants | Streaming layouts, stats on the fly |
| **India-native** | Sessions, special sessions, MIS, tick/lot/band rules, F&O expiry and settlement, versioned statutory charges | Market model (§11), cost schedules (§12) |
| **Safe** | Shared strategies cannot execute code; live trading is impossible by accident | IR-first sharing, sandboxed workers, live guardrails |

---

## 3. Goals, non-goals, principles

**Goals**
- G1: Backtest single instruments and portfolios on every Historify interval (1m–M) and every segment (NSE/BSE cash,
  NFO/BFO, MCX, CDS), with India-correct execution and costs.
- G2: Research at scale: millions of variants per session on a desktop, with statistically sound selection.
- G3: Make every result reproducible and explainable: which data, which rules, which code, why each fill happened.
- G4: One strategy definition for backtest, paper and live.
- G5: First-class UX inside Historify; equally usable from notebooks and the CLI.

**Non-goals (for now)**
- Tick, L2 or order-book simulation. Historify stores OHLCV(+OI), so HFT-grade queue modelling is out of scope.
  hftbacktest covers that niche.
- Multi-user or cloud service. Historify is a local, single-user desktop app.
- A GPU backend. The kernels are branchy, path-dependent state machines and gain little from GPUs; desktops rarely
  have CUDA (§6, ADR-12).
- Bundling any non-commercial / Commons-Clause software.

**Principles**
1. **Correctness before speed.** Every optimisation must keep the oracle suite green.
2. **Explicit over implicit.** Every semantic choice is a named field in the ExecSpec and is written into each run
   record.
3. **Data is evidence.** A run refuses unverifiable data and surfaces data-quality caveats with its results.
4. **Compile the hot path, not the user.** Users write IR, Python or numba. The loop is always compiled.
5. **Strategies are data.** Shareable artefacts are declarative. Executable code needs explicit trust.
6. **No new runtime burden for non-users.** Lab is an optional extra. Historify's core lock stays lean.

---

## 4. Users and use cases

| ID | Use case | Entry point | Key needs |
|---|---|---|---|
| U1 | "Does this idea work on SBIN 5m?" | Charts page, then "Backtest" | Seconds to a result; trades plotted on the chart |
| U2 | Parameter study across a watchlist | Research page / notebook | Heat maps, stability, out-of-sample checks |
| U3 | Strategy generation (search over rules) | Research page | Massive search plus overfitting control; ranked, validated candidates |
| U4 | Positional portfolio (momentum, rebalancing) | Backtests page | Shared capital, sizing, rebalancing, benchmark comparison |
| U5 | F&O strategies (futures, option legs, expiry) | Backtests page | Lot history, expiry calendar, margin, settlement, STT on exercise |
| U6 | Reproduce a result from months ago | Run page, then "Re-run" | Byte-identical output, or an explained diff |
| U7 | Paper-trade a validated strategy | Paper page | Same object, live bars, reconciliation with expected fills |
| U8 | Import a strategy from a colleague | Strategies page | No code execution on import; explicit diffs |
| U9 | Research in Python | `import hbt` | Snapshots, Universe, run/search APIs, Polars/pandas interop |

---

## 5. Requirements

### 5.1 Functional (abridged; full list maintained as issues)

| ID | Requirement |
|---|---|
| FR-D1 | Load any set of series from a **snapshot**, the **research replica**, or the **live store** (in-process jobs only), as a time-aligned Universe with session metadata and provenance. |
| FR-D2 | Raw or **adjusted** prices (splits/bonus; optional dividends for total return), selectable per run. |
| FR-D3 | Multi-timeframe access through `available_at` only (no look-ahead by construction). |
| FR-D4 | Intervals derived on the fly (3m…1h from 1m, W/M from D), identical to Historify's roll-up. |
| FR-S1 | Strategies as **IR** (declarative rules), **compiled callbacks** (numba cfunc / Rust plugin) or **Python** (event-driven). |
| FR-S2 | Built-in indicator library (≥ 60 indicators) matching TA-Lib definitions where one exists. |
| FR-E1 | Order types: market, limit, stop, stop-limit, bracket (stop + target), trailing stop, time stop, MOC-at-official-close (opt-in), cancel/replace. |
| FR-E2 | Fill models: OHLC path policies, 1m "bar magnifier", slippage (ticks / % / volume impact), volume participation cap with partial fills. |
| FR-E3 | Session rules: MIS square-off, no-entry-after, pre-open/closing session, special sessions, expiry day. |
| FR-E4 | Instrument rules: tick rounding, lot multiples, price bands/circuits, short-selling permissions per product. |
| FR-P1 | Portfolio: shared cash, margin (equity and F&O), position/exposure limits, sizing methods, rebalancing. |
| FR-C1 | Costs: versioned schedules per segment and broker profile; per-charge breakdown; contract-note rounding. |
| FR-M1 | Calendar-correct metrics; trade, exposure and cost analytics; benchmark-relative statistics; monthly and time-of-day breakdowns. |
| FR-R1 | Search: grid, random, Bayesian (TPE/CMA-ES, multi-objective), genetic over the IR; resumable; parallel. |
| FR-R2 | Validation: walk-forward (anchored/rolling), CPCV with purge and embargo, PBO, DSR, robustness suite; a locked hold-out vault. |
| FR-X1 | Run manifest; deterministic re-run; diff between runs. |
| FR-L1 | Paper mode on Historify live bars; order routing to OpenAlgo (sandbox first); reconciliation; guardrails. |
| FR-U1 | UI: Strategies, Backtests, Research, Compare and Paper pages; trades overlaid on Charts. |

### 5.2 Non-functional targets

Reference machine: 4 cores / 16 GB laptop. Each target is set with headroom against what was measured in the sandbox.

| ID | Target | Measured |
|---|---|---|
| NFR-1 | Cold start (import + first backtest) **< 0.5 s** | 0.29 s [T1] |
| NFR-2 | Single intraday backtest, 39k 5m bars, full fill model **< 5 ms** | 0.35 ms (17.3 ms / 50) [T1] |
| NFR-3 | One symbol, 10 years of 1m (~937k bars) **< 50 ms** | ~13 ms per variant (10.3 s / 800) [T6] |
| NFR-4 | Search throughput, 5m intraday, 4 cores **≥ 3,000 backtests/s** | 3,860–4,010 /s [T1] |
| NFR-5 | Portfolio, 200 symbols × 10 years of 1m (187.5 M cells), single strategy **< 5 s** | 9.3 ns/cell ⇒ ~1.7 s [T7] |
| NFR-6 | Peak memory for the full-universe stream **< 1 GB** | 227 MB [T6] |
| NFR-7 | Data load, 10 M bars into the engine **< 0.5 s** | 0.175 s [T10] |
| NFR-8 | Determinism: re-run byte-identical on the same platform; cross-platform money exact, analytics ≤ 1e-12 | by design (§10.9) |
| NFR-9 | Footprint: core < 5 MB; full Lab extra < 300 MB | 293 KB core [T8] |
| NFR-10 | Platforms: Windows x64, Linux x64 (macOS arm64 best-effort); CPython 3.12–3.14 | abi3 verified on 3.12–3.14 [T8] |
| NFR-11 | Never degrade Historify: downloads and UI stay responsive while research runs | separate worker processes, CPU budget (§19.1) |

---

## 6. Technology decisions (ADRs)

Each ADR states the decision, the evidence and the rejected alternatives.

**ADR-1: The execution core is Rust (PyO3 + maturin + rayon); numba is optional and for users only.**
- *Evidence [T1][T8]:* the Rust port of the kernel is **bit-identical** to numba across 12,200 backtests and exact
  against the reference.
  - Speed is equal: single-thread 17.3 vs 21.8 ms for 50 runs; sweep 3.16 vs 3.04 s.
  - Cold start is 3× faster (0.29 vs 0.85 s), because there is no JIT.
  - The wheel is **293 KB**, against +186 MB for numba/llvmlite.
  - One **abi3** wheel runs on 3.12–3.14.
- Rust also brings memory safety, `proptest`/`criterion`/`cargo-fuzz`, and a core that paper/live mode can reuse
  without Python in the loop.
- *Rejected:*
  - pure numba core: footprint, JIT warm-up, the global numba cache, weaker static guarantees;
  - C++: safety and build complexity;
  - Julia: packaging and embedding;
  - Cython: hot loops are harder to optimise, no rayon-class parallelism.

**ADR-2: The data plane is Apache Arrow through PyCapsules; DuckDB stays the store; no pyarrow dependency.**
- *Evidence:* DuckDB 2.0 relations expose `__arrow_c_stream__`.
  - `pyo3-arrow` consumes the stream in Rust: 9.79 M rows in 80 batches in **0.175 s**, 4× faster than `fetchnumpy`,
    **with pyarrow absent** [T10].
  - Polars consumes the same capsule [T3].
  - Historify's existing no-pyarrow policy is preserved.
- *Constraint:* `pyo3-arrow 0.20` pins `pyo3 ^0.30` and `arrow ^59`. Upgrade them in lockstep (a Renovate group).
- *Engine cache:* Arrow IPC LZ4 (9.8 M bars: 233 MB, 0.145 s to load [T3]) keyed by dataset hash. Parquet zstd stays
  the interchange and snapshot format (133 MB; lazy predicate-pushdown scan of one symbol-quarter: 11 ms [T3]).

**ADR-3: Money is exact; analytics are float.**
- Prices are held as **i64 ticks of ₹0.01** (paise) and quantities as i64 units. Cash, fees and P&L are i64 paise,
  with explicit per-charge rounding (contract-note style, §12.4). Returns and statistics are f64.
- Historify stores f64 prices. Conversion to ticks happens on load and is validated (`|p·100 − round(p·100)| < 1e-6`,
  or quarantine).
- *Why:* cross-platform determinism; exact reconciliation with broker contract notes; no drift over millions of fills.
  Nautilus takes the same approach. The prototype used f64. Production moves to fixed point, with f64 kept for the
  analytics path.

**ADR-4: Strategies are a versioned JSON IR evaluated natively in Rust; Python and compiled callbacks are escape
hatches.**
- *Evidence:*
  - [T5] Polars expressions serialise, but Polars documents that the format is **not stable across versions** and
    **may unpickle Python** (UDFs). It cannot be our storage format.
  - [T3/T8] Polars also adds 174 MB.
  - [T4] Indicator maths is cheap everywhere (29 M values: numba 0.11 s, TA-Lib 0.25, Polars 0.44, pandas 1.59).
    The gain comes from **fusing** signal evaluation into the parallel simulation loop (what the sweep kernel does),
    and that needs our own evaluator.
- So: IR → type-checked plan → Rust evaluator (batch *and* streaming, for live). An optional adapter compiles the IR to
  Polars for notebook users.

**ADR-5: Three strategy tiers with measured costs [T2]** (identical results across tiers):

| Tier | How the user writes it | Cost per bar | Use |
|---|---|---|---|
| Rules (IR) | declarative JSON / form builder / Python builder API | fused; vectorised ≈ 37 ns incl. signal generation | 90 % of strategies, search, sharing |
| Compiled callback | numba `@cfunc` (or a Rust plugin) called by the core through a C function pointer | **25 ns** | custom stateful logic at full speed |
| Python event | `on_bar()` / `on_fill()` methods | **1,594 ns** (64×) | prototyping, ML inference, complex portfolio logic; single runs |

**ADR-6: The ExecSpec is a first-class, versioned, hashed object.** Every semantic choice is a field (Appendix B).
Defaults are conservative: next-open fills, stop live on the entry bar, stop first on ambiguous bars. The ExecSpec hash
is part of every result.

**ADR-7: Process model.** Research runs in **Lab worker processes**, never in the Flask/engine process:
- *Why a separate process is required:* Historify's DuckDB file cannot be opened even read-only by another process
  while the app runs [E1]. CPU-heavy work must not starve downloads or the UI.
- *Where workers read from:* snapshots or the research replica. Only short in-process jobs (quick chart backtests) may
  use `store._reading()`.
- *Parallelism:* rayon inside the core; a process pool for the Python tier.
- *Future:* PyO3 supports free-threaded CPython. We keep the core `gil_used = false`-ready, so Python-tier strategies
  can later run in threads. (3.14t could not be benchmarked here; see Appendix A.)

**ADR-8: Verification by oracles.** Three things keep the core honest:
- independent reference implementations (already written for E3/E5);
- backtesting.py (bundled as a dev/test dependency; AGPL-compatible) for bracket semantics, and backtrader (test-only)
  for signed-cost semantics;
- hand-computed golden scenarios.

These are CI gates, not one-off checks (§22).

**ADR-9: The UI stays in Historify's stack:** Jinja shells, Alpine CSP components, Tailwind/daisyUI and the vendored
Lightweight Charts. Heat maps and distributions are drawn on canvas by a small in-house module. No SPA framework and
no CSP relaxations.

**ADR-10: Licensing.**
- All runtime dependencies are permissive or (L)GPL. Bundled: PyO3, rust-numpy, rayon, arrow-rs and pyo3-arrow
  (Apache/MIT); optuna (MIT); optional numba (BSD); TA-Lib (BSD) only for the parity tests.
- Fix the CI licence gate as recommended in the engine review (allow GPL/AGPL; deny Commons Clause / non-commercial).

**ADR-11: Search orchestration.**
- Our own parallel sweep driver handles grids and random search (fused, memory-flat).
- **optuna 5** handles Bayesian and multi-objective search (TPE, CMA-ES, NSGA-II), with Lab as the objective.
- Our own GP operators work on the IR (DEAP 1.4 is LGPL and usable, but IR-native operators keep trees valid by
  construction).

**ADR-12: Rejected platform options.**
- GPU (JAX/CuPy): branchy sequential kernels, no CUDA on typical desktops, huge wheels.
- Ray/Dask: a single-user desktop needs no cluster; rayon plus processes suffice.
- A browser/WASM engine: interesting later for client-side what-if; not needed now.

---

## 7. Architecture

### 7.1 Context

```
 ┌──────────── User ─────────────┐
 │ Browser UI    Notebook / CLI  │
 └──────┬──────────────┬─────────┘
        │ /api/v2/lab  │ import hbt
 ┌──────▼──────────────▼───────────────────────────────────────────────────────────────────────────┐
 │ Historify process (Flask + Services)                    │  Lab worker processes (N ≤ cores-1)  │
 │  JobService ─ LabDispatcher ─────────── spawn/IPC ─────►│   hbt (Python)  ──►  hbt-core (Rust) │
 │  DownloadEngine · Scheduler · FollowService (live bars) │   reads: snapshots / research replica │
 │  meta SQLite (lab_* tables)  ◄──── results, progress ───│   writes: run artefacts (Parquet)     │
 │  bars.duckdb (single writer) ── publish ──► replica ────┤                                       │
 │  SnapshotService ───────────── build ─────► snapshots ──┘                                       │
 └───────────────────────────┬─────────────────────────────────────────────────────────────────────┘
                             │ paper/live (M7+): order intents
                        ┌────▼────┐
                        │ OpenAlgo│ ──► broker
                        └─────────┘
```

### 7.2 Components

| Component | Language | Responsibility |
|---|---|---|
| `hbt-core` | Rust | Money/tick types; Universe buffers; IR evaluator; indicator library (batch and streaming); order/fill engine; market rules; costs; portfolio/margin; metrics accumulators; sweep driver; GP operators |
| `hbt.data` | Python | DatasetRef resolution (snapshot / replica / store), manifest verification, Arrow streams into the core, adjustment factors, calendar and `available_at` |
| `hbt.strategy` | Python | IR builder API, IR (de)serialisation and validation, Python `Strategy` base class, compiled-callback registration |
| `hbt.run` | Python | Run orchestration: spec → core → artefacts; progress; cancellation |
| `hbt.research` | Python | Search (grid / random / optuna / GP), validation (WFO, CPCV, PBO, DSR, robustness), trial registry |
| `hbt.report` | Python | Metrics tables, tear sheet, monthly and time-of-day breakdowns, comparisons |
| `app/lab/*` | Python | Historify integration: job kinds, LabDispatcher, API blueprint, pages, migrations |
| `hbt.live` (M7) | Python + Rust | Streaming evaluation on Follow bars, OMS, OpenAlgo adapter, reconciliation, guardrails |

### 7.3 Main flows

**Run a backtest (UI)**
1. `POST /api/v2/lab/runs` with {strategy_version, dataset (snapshot id, or "replica@as_of"), exec_profile,
   cost_profile, params}.
2. JobService creates a `lab_run` job. LabDispatcher hands it to a free worker, passing the run spec only, never data.
3. The worker verifies the dataset (manifest sha256, file hashes), resolves adjustments and the calendar, and streams
   Arrow batches into `hbt-core`.
4. The core simulates and streams progress (percent, bars/s) back to the meta DB at most 4×/s.
5. The worker writes artefacts (trades, orders, equity, positions, logs) to `instance/lab/runs/<id>/`, plus metrics
   and the manifest in the meta DB. The job succeeds.
6. The UI shows results: equity/drawdown, trades on the chart, metrics, costs and data caveats.

**Search + validate (Research page)**
1. Create a project (universe, period, locked hold-out, objective).
2. Define the search space on IR parameters.
3. Screening: the fused sweep in the core (cheap fill model), every trial logged to the registry.
4. The top K go to full-fidelity re-evaluation (1m magnifier, full costs).
5. Walk-forward / CPCV.
6. PBO and DSR over the registry.
7. Robustness suite.
8. Candidates that pass the gates get a "validated" badge and are eligible for paper trading.
9. The hold-out vault opens **once**, on explicit user action, and the opening is recorded.

**Paper trading (M7)**
1. FollowService delivers closed bars.
2. `hbt-core` evaluates the IR in streaming mode: the *same* evaluator and ExecSpec.
3. Order intents go to the OMS, which applies guardrails and sends them to OpenAlgo (sandbox), or to the simulated
   broker for pure paper trading.
4. Fills come back and are reconciled against the engine's expected fills.
5. The drift report is shown on the Paper page.

---

## 8. Data layer

### 8.1 Sources

| Source | Who may use it | Consistency | Notes |
|---|---|---|---|
| **Snapshot** (`instance/snapshots/<id>`) | all workers, notebooks, CI | immutable, sha256-verified | The default for anything that must be reproducible. Manifest v2 (§8.6). |
| **Research replica** (`instance/lab/replica/bars.<as_of>.duckdb`) | workers, notebooks | point-in-time (as of the last publish) | Published after each post-close sync, and on demand. Avoids the single-process lock [E1]. The run records `replica@as_of` plus the file hash. |
| **Live store** (`store._reading()`) | in-process quick runs only (U1) | MVCC, moving | The run records `live@<max ts per series>`. Flagged "not reproducible" unless later promoted to a snapshot. |

**Replica publishing.**
- Publishing runs under the store's existing writer, as a copy-and-swap like `rewrite_compact` (same verification of
  per-series counts and fingerprints).
- Retention: keep the last 2 replicas. About 2.5 GB for 200 symbols × 10 years of 1m.
- A run pins the replica file it opened. A newer publish never changes a running job.

### 8.2 Data readiness (prerequisite M0, applies to every engine)

| Item | Specification |
|---|---|
| **Historical calendar** | Import `exchange_calendars` XBOM (BSE, since 2006) as the baseline equity calendar. Overlay official NSE/BSE/MCX circulars: special sessions (Muhurat, Budget Saturdays, DR drills), ad-hoc closures, MCX sessions with the US-DST close. The table stays `market_calendar` (already present). Add a **content hash** over the (exchange, date, session) rows. Snapshot manifests and runs record the hash. Re-run ledger validation for history after import. |
| **Corporate actions** | New table `corporate_actions(instrument_id, ex_date, kind, ratio_num, ratio_den, cash_amount, source, confirmed)` (kind: split, bonus, rights, dividend, demerger, symbol_change). Import from the NSE/BSE CA files, or manually. A new audit check flags overnight gaps that match common ratios (1:2, 1:5, 1:10, bonus 1:1…) as *CA candidates*. Raw bars stay immutable; adjustment is a read-time view (§8.4). |
| **`available_at`** | Computed, not stored: intraday `ts + interval_seconds + INTRADAY_FINAL_BUFFER`; daily `session_close(date) + DAILY_FINAL_BUFFER`. Exposed in snapshot Parquet (manifest v2), the Lab data API and `/api/v2/bars?fields=available_at`. |
| **Instrument history** | `instrument_attributes(instrument_id, valid_from, valid_to, lot_size, tick_size, price_band_pct, fo_eligible, series)`, filled from OpenAlgo master downloads kept daily (diffed), plus backfill from exchange archives where available. |
| **Universe membership** | `universe_membership(universe, instrument_id, valid_from, valid_to)` for indices (NIFTY 50/100/500, sector indices), from index reconstitution files. Removes survivorship bias from index-based universes. |
| **Derivatives master** | `contracts(instrument_id, underlying_id, kind FUT/CE/PE, expiry, strike, lot_size, settlement cash/physical, exercise European)`, with an **expiry calendar as data** (weekday rules changed over time and must never be inferred). |
| **Manifest v2** | Adds the calendar hash, instrument attributes over the range, CA events over the range, an adjusted/raw flag, `available_at` columns, cost-schedule hints and universe membership. Drops the redundant `datetime_ist` string column. |

### 8.3 In-memory model: the Universe

```
Universe
  symbols[S], instrument_ids[S], exchange[S], segment[S]
  time axis: ts[T] (i64, bar open, UTC), available_at[T], session_id[T], bar_in_session[T], minute_of_day[T]
  fields (per symbol, contiguous): open/high/low/close (i64 ticks), volume (i64), oi (i64|null),
                                   valid (bit: bar exists), flags (provisional, incomplete_session, adjusted)
  attributes over time: lot_size, tick_size, price_band, adj_factor (piecewise-constant, event lists)
  provenance: dataset ref + hashes, calendar hash, adjustment mode
```

**Layouts.**
- **Symbol-major** (each symbol's column contiguous) for single-asset runs and sweeps. Each job then streams one
  symbol, so memory stays flat: 227 MB at 187.5 M bars [T6].
- **Time-blocked** for portfolio runs: blocks of B sessions × S symbols, double-buffered, with state carried across
  blocks. The measured portfolio kernel does 9.3 ns per symbol-bar [T7].

**Missing bars.**
- Bars the calendar expects but which are absent (no trades) are `valid = 0`.
- Orders cannot fill on invalid bars.
- Indicators follow a documented policy per function: skip (default) or forward-fill.

### 8.4 Adjustments

| Mode | Definition | Default use |
|---|---|---|
| `raw` | Stored broker bars. CA events are applied to **positions** (quantity × ratio, price ÷ ratio) on the ex-date. | Execution realism; required for F&O |
| `adjusted` | Prices multiplied by the cumulative backward factor; volumes divided. Fills are mapped back to raw for costs and ticks. | Indicator continuity for equity signals (default for cash equities) |
| `total_return` | `adjusted` plus dividends reinvested | Long-horizon portfolio research |

A run records the mode and the CA events it applied. If a symbol has unconfirmed CA candidates in range, the run
carries a **data caveat**.

### 8.5 Multi-timeframe

- `hbt` exposes higher frames through `available_at` only: `{"col": "close", "tf": "D"}` in the IR, `ctx.frame("D")`
  in the Python tier (Phase 1.3).
- **Visibility (revised in Phase 1.3, supersedes the v1.2 "≤ ts[t]" wording):** the value visible at bar *t* is the
  latest higher-timeframe bar with `available_at(h) ≤ available_at(t)`, i.e. final no later than the current bar is
  final. A 15m bar ending with the current 5m bar is visible (both carry the same finality buffer); the daily bar of the
  current session becomes visible on the next session. This is exactly what the paper runtime can know.
- The IR has no other way to reference another timeframe. This removes the 100 % leak measured in the engine review
  [E-LA].

### 8.6 Data-quality surfacing

Every run lists:
- incomplete or short sessions in range (from the ledger / manifest);
- quarantined bars in range;
- revisions in range;
- CA candidates;
- provisional bars.

Policy per ExecSpec: `warn` (default), `exclude_sessions` or `fail`.

---

## 9. Strategy layer

### 9.1 Concepts

- **Strategy**: a named, versioned definition. Each version is immutable and content-hashed.
- **Parameters**: typed (int, float, choice, bool), with a range/step, a default and a search prior.
- **Signals**: boolean or numeric series derived from data by IR expressions.
- **Rules**: map signals to **intents**: `enter_long`, `exit_long`, `enter_short`, `exit_short`, `target_weight`,
  `target_qty`, or an `order` (type, price expression, protective legs).
- **Risk block**: stops, targets, trailing, time stops, daily loss limit, max positions.
- **Universe selector**: a static list, a watchlist, or an index membership as of each date, plus filters (price,
  turnover, F&O eligibility).

### 9.2 IR example (abridged; grammar in Appendix C)

```json
{
  "ir_version": 1,
  "name": "EMA cross with bracket, MIS",
  "params": {"fast": {"type": "int", "default": 9, "min": 3, "max": 50},
             "slow": {"type": "int", "default": 21, "min": 10, "max": 200},
             "sl":   {"type": "float", "default": 0.005, "min": 0.001, "max": 0.03}},
  "universe": {"watchlist": "NIFTY-50-EQ", "interval": "5m"},
  "signals": {
    "f": {"fn": "ema", "args": [{"col": "close"}, {"param": "fast"}]},
    "s": {"fn": "ema", "args": [{"col": "close"}, {"param": "slow"}]},
    "trend_d": {"fn": "gt", "args": [{"col": "close", "tf": "D"}, {"fn": "sma", "args": [{"col": "close", "tf": "D"}, 50]}]}
  },
  "rules": [
    {"when": {"fn": "and", "args": [{"fn": "cross_above", "args": [{"sig": "f"}, {"sig": "s"}]}, {"sig": "trend_d"},
                                    {"fn": "le", "args": [{"fn": "minute_of_day"}, "14:55"]}]},
     "do": {"enter_long": {"size": {"pct_equity": 1.0},
                           "bracket": {"stop_pct": {"param": "sl"}, "target_pct": null}}}},
    {"when": {"fn": "cross_below", "args": [{"sig": "f"}, {"sig": "s"}]}, "do": {"exit_long": {}}}
  ],
  "product": "MIS"
}
```

**Compilation.**
1. JSON Schema validation.
2. Name resolution and type checking (series/scalar, bool/num, timeframe).
3. **Causality check**: every series operator is causal; `tf` references resolve only through `available_at`.
4. Constant folding and common-subexpression elimination: `ema(close, fast)` is shared across rules and across sweep
   variants with the same `fast`.
5. A plan of fused kernels: per symbol, indicators update incrementally in the same pass as the simulation.

Because the same plan also supports streaming evaluation, live mode reuses it unchanged.

**Authoring surfaces.**
- A form builder in the UI for the common blocks.
- A Python builder (`ema(close, P.fast) > ema(close, P.slow)`) that emits IR.
- Raw JSON.
- The UI shows a read-only "pseudo-code" rendering of the IR.

### 9.3 Python tier (event-driven)

```python
from hbt import Strategy, param, Order

class MeanRevert(Strategy):
    lookback = param(int, 20, 5, 100)
    z_entry  = param(float, 2.0, 1.0, 3.5)

    def init(self, ctx):                       # vectorised pre-compute allowed (numpy/Polars), causal only
        self.z = ctx.indicator("zscore", ctx.close, self.lookback)   # computed by hbt-core

    def on_bar(self, ctx, bar):                # called at bar close; orders execute per ExecSpec
        if not ctx.position and self.z[bar.i] < -self.z_entry and bar.minute <= 14*60+55:
            ctx.submit(Order.market_buy(pct_equity=1.0).bracket(stop_pct=0.006))
        elif ctx.position and self.z[bar.i] > 0:
            ctx.close_position()

    def on_fill(self, ctx, fill): ...
```

**Guarantees of the `ctx` API.**
- Series accessors expose only data up to `bar.i`.
- `ctx.close[bar.i + 1]` raises an error.
- Access to other timeframes goes through `ctx.frame(tf)`, which uses `available_at`.

**Cost.** About 1.6 µs per bar of Python overhead [T2]: about 1.5 s per 10-year 1m symbol. Fine for single runs and
ML inference; search over Python strategies runs on the process pool.

### 9.4 Compiled-callback tier

- A numba `@cfunc` with the signature `i8 decide(const Ctx*, i64 t)` is registered by address.
- The core calls it per bar: 25 ns per bar [T2].
- The `Ctx` struct exposes read-only pointers to the Universe columns and indicator buffers up to `t`, plus a small
  writable state scratchpad.
- The ABI is versioned: `HBT_CTX_ABI = 1`.
- Rust plugins (cdylib) implement the same ABI for users who want it.
- Compiled callbacks are **code**: they need explicit trust and are never imported implicitly (§23.1).

### 9.5 Indicator library

- About 60 indicators in `hbt-core` ship at M2, in both batch and streaming variants:
  - trend: SMA, EMA, WMA, HMA, KAMA, DEMA/TEMA;
  - momentum: RSI, Stoch, MACD, ROC, CCI, Williams %R, MFI;
  - volatility: ATR, BBands, Keltner, Donchian, stdev, Parkinson/Garman-Klass;
  - volume: OBV, VWAP (session-anchored), AD, CMF;
  - structure: pivots, opening range, prior-day H/L/C, session high/low so far;
  - statistics: z-score, rolling correlation/beta, rolling quantile.
- Definitions follow TA-Lib where it exists. Parity tests run against TA-Lib 0.8 (BSD, wheels for 3.14). For comparison,
  the Polars and numba EMA implementations agreed to 1.8e-11 [T4].
- **Session-aware** variants (VWAP, opening range, "since session open") use the calendar's session boundaries.
  Most engines get these wrong on Indian data.

---

## 10. Execution engine

### 10.1 Bar event model (normative)

For each bar *t* and each instrument, the phases below run in this order. Portfolio runs apply each phase across all
instruments in deterministic order (instrument_id ascending) before moving to the next phase.

```
P0  Session boundary     (first bar of a session) corporate actions at ex-date (raw mode), expiry settlement of the
                          previous session, margin re-computation, daily counters reset
P1  Open auction         MIS square-off if squareoff_at ≤ bar open time; pending exits; pending entries;
                          MOO orders; gap-through of resting stops/targets/limits (fill at open)
P2  Intrabar             resting orders (stop, limit, bracket legs, trailing) by the intrabar path policy (§10.3);
                          bracket legs are live on the entry bar; liquidity cap; partial fills carry over
P3  Close                strategy decision on bar t (IR evaluation / callback / Python on_bar), using data ≤ t only;
                          new orders are queued for P1 of t+1 (fill_timing=next_open) or filled at the close
                          (close, opt-in, flagged); MOC-at-official-close (opt-in, daily)
P4  Mark-to-market       equity, margin utilisation, drawdown, statistics accumulators, risk limits (may queue
                          forced exits for P1 of t+1)
```

**Prototype coverage.** The prototype kernel implements P1–P4 for long-only market, bracket and MIS cases. It matches
the independent reference exactly [T1].

### 10.2 Orders

| Type | Parameters | Notes |
|---|---|---|
| Market | qty / sizing | fills at the P1 open (next_open) |
| Limit | price expr | fills if the path touches the price; at the open if it gapped through favourably |
| Stop / stop-limit | trigger, limit | stop-limit can fail to fill on gaps (realistic) |
| Bracket | entry + stop (+ target) | legs live from the entry bar (default) or from the next bar (vectorbt-compatible, opt-in) |
| Trailing stop | pct / ATR / ticks | ratchets at P4 using the bar close (default) or intrabar extremes (opt-in) |
| Time stop | N bars / clock time | evaluated at P1 |
| MOC-official | – | fills at the official close (daily bar close); only for `CNC`/`NRML` |
| Cancel/replace | order id | from Python or compiled tiers |

**Time-in-force.** `DAY` (cancelled at session end) is the default. `GTC` is allowed only for CNC/NRML. `IOC` is
supported in P1 only.

### 10.3 Intrabar path and the 1m bar magnifier

OHLC bars do not record whether the high or the low came first.

| Policy | Rule |
|---|---|
| `pessimistic` (default) | Adverse leg first: stop before target |
| `ohlc_direction` | If close ≥ open, assume O→L→H→C; otherwise O→H→L→C |
| `magnifier_1m` | For bars coarser than 1m, replay the stored 1m constituents in order. Historify's roll-up guarantees exact alignment (verified: 5 × 1m per 5m bar, identical bucket starts). Fall back to `pessimistic` inside an ambiguous 1m bar. |

**Measured impact [T9]** (5m, 20 symbols, 2 years):
- brackets of 0.5 %/0.75 % or wider: ambiguity occurred on **0 %** of bracket exits;
- 0.3 %/0.45 %: **0.06 %** of exits;
- a scalping 0.2 %/0.3 % bracket: **1.3 %** of exits, and optimistic vs pessimistic equity then differ by a 2.0 %
  median. The magnifier resolved them at ~35 % of the way from pessimistic to optimistic.

Real NSE bars have fatter tails than the synthetic data, so expect more ambiguity in practice. The magnifier is the
**default for validation runs** on tight brackets or ≥ 15m bars, and optional during screening.

### 10.4 Slippage and liquidity

- **Slippage models:**
  - `ticks(n)`;
  - `pct(x)`;
  - `spread_proxy` (k × (high − low) / close);
  - `impact(participation)` (square-root model: `σ_bar · c · sqrt(qty / bar_volume)`).
  All are applied adversely and rounded to a tick.
- **Participation cap:** at most p % of bar volume per fill (Nautilus uses 25 % per OHLC point by default [E3]; we
  default to 10 % of bar volume for intraday, off for daily). The remainder carries to the next bar or is cancelled
  (configurable).
- **Zero-volume bars** cannot fill market orders (they queue) unless `allow_zero_volume_fills`.

### 10.5 Fill price rules

- A fill price always lies in [low, high] of the bar in which it occurs.
- It is rounded to the instrument's tick on that date (the adverse direction for market orders).
- It respects the price band (§11.3).

Property tests enforce all three (§22).

### 10.6 Position accounting

- **Netting** per (instrument, product) is the default, matching Indian broker position books.
- Average-price accounting, with realised P&L on reductions.
- Instruments in hedge mode keep separate long and short legs; this is needed only for specific F&O strategy
  constructs.

### 10.7 Portfolio-level execution

- Orders from all instruments within a phase are processed in deterministic priority: exits first, then entries in a
  configurable rank (signal strength, instrument order, random with a seed).
- Cash and margin are checked per order. A rejection gets a reason code (`INSUFFICIENT_FUNDS`, `MARGIN`,
  `BAND_LOCKED`, `LOT`, `NO_SHORT_CNC`, `MAX_POSITIONS`, …), and rejections are counted in the run log.

### 10.8 Money and precision

- Prices are i64 ticks of ₹0.01, quantities i64, cash i64 paise.
- Charges are computed in i128 fixed-point (basis-point-scaled rates) and rounded **per charge per order** to paise
  with `round_half_up`, like contract notes (§12.4).
- Analytics (returns, ratios) use f64 with a fixed summation order.

### 10.9 Determinism

- No fast-math.
- No parallel reductions whose order depends on the thread schedule. Per-job results are reduced in job-index order.
- Random components (Monte Carlo, random search, tie-breaks) use a seeded counter-based RNG, Philox keyed by
  (run_seed, job_index).
- Byte-identical artefacts on the same platform is a CI gate. Across platforms, money must be identical and analytics
  within 1e-12.

---

## 11. Indian market model

> Exchange rules change. Everything below is **data with validity dates**, not code constants, and each item must be
> verified against current NSE/BSE/MCX/SEBI circulars when the tables are seeded.

### 11.1 Sessions

- Normal sessions come from the calendar: NSE/BSE 09:15–15:30; MCX 09:00–23:30/23:55 depending on US DST; CDS
  09:00–17:00.
- **Pre-open call auction** (equities, 09:00–09:08): the first 1m bar may or may not contain the auction print
  (broker-dependent, see the roll-up recommendation). ExecSpec `open_price_source = first_bar_open | daily_open`.
- **Closing session** (15:40–16:00, trades at the official close): available only as `MOC-official` for delivery
  products.
- **Special sessions** (Muhurat, Budget-day Saturdays, mock/DR sessions) come from the calendar. Mock/DR sessions are
  excluded by default.

### 11.2 Products

| Product | Segment | Rules modelled |
|---|---|---|
| `CNC` | cash delivery | No shorting. Sells need settled holdings or same-day buys (BTST allowed, with a configurable short-delivery risk flag). T+1 settlement with sale-proceeds usability policy (broker profile). DP charge per scrip per sell day. |
| `MIS` | cash intraday | Long and short. Broker square-off time (profile: 15:15–15:25 equities; MCX and CDS per profile). Leverage per broker profile (capped by SEBI peak-margin rules). Positions never carry overnight. |
| `NRML` | F&O / commodity carry | Margin = SPAN + exposure (model or imported files). MTM daily. Expiry handling. |

### 11.3 Instrument rules

- **Tick size and lot size** are time-versioned (instrument attributes). Orders are rounded to ticks and quantities
  to lots. Lot-size changes apply to *new* orders; open positions keep their quantity.
- **Price bands / circuit filters.** Non-F&O equities have static bands (2/5/10/20 %) relative to the previous close.
  A bar that closes at the band with high == low (or with volume below a threshold) is treated as **locked**: no fills
  on the locked side. F&O stocks use dynamic bands, modelled as a no-lock flag.
- **Market-wide circuit breakers** (index moves of 10/15/20 %) halt trading. They are modelled from calendar or
  session overrides where known. Otherwise the data gap itself prevents fills.
- **Short selling** in cash is MIS only. SLB is not modelled.

### 11.4 Derivatives

- **Contracts and expiry** come from the derivatives master. The **expiry calendar is data**: the weekly/monthly
  pattern and the expiry weekday have changed over time and are exchange-specific.
- **Settlement:**
  - index options and index futures are cash-settled at the exchange settlement price;
  - stock F&O is physically settled. The model creates a delivery obligation in the underlying (CNC), with an option
    to auto-square-off before expiry per broker profile.
- **Options:**
  - European exercise;
  - ITM exercise at expiry creates **STT on the intrinsic value** (a classic Indian cost trap, modelled explicitly
    in §12);
  - premium is paid in full; short options need margin.
- **Continuous futures** (for signals only) can be built by roll rule: expiry-N days, or volume/OI crossover, with
  back-adjustment by difference or ratio. *Trades are always placed in the actual contract.*
- **Option chains:** strikes are selected at run time by rule (ATM ± k, delta band if Greeks are computed, premium
  band). Greeks come from Black-76 on futures or Black-Scholes on spot, using a user-selected rate; IV is solved
  per bar. Only chains downloaded in Historify can be used. Missing strikes at the decision time produce a rejected
  order, never a synthetic price.

### 11.5 Margin model

| Level | Method | When |
|---|---|---|
| L0 | Fixed % of notional per segment/underlying (broker profile) | Screening; default |
| L1 | SPAN approximation: scenario grid (price ±, vol ±) on the portfolio, plus exposure margin | Validation |
| L2 | NSE SPAN risk-parameter files (daily), if the user imports them | High-fidelity F&O validation |

Margin is checked on order placement (pre-trade) and at P4 (MTM). The margin-call policy (square off largest loss
first, or all) is configurable.

---

## 12. Costs, taxes and charges

### 12.1 Components

| Component | Basis | Side | Segments |
|---|---|---|---|
| Brokerage | % of value with a cap (₹ per order), flat per order, or zero | both | all |
| STT / CTT | % of value (or of premium, or of intrinsic value on exercise) | sell, buy, or both depending on segment | cash, F&O, commodity |
| Exchange transaction charge | % of value (premium for options) | both | all |
| SEBI turnover fee | ₹ per crore | both | all |
| Stamp duty | % of value | buy | all |
| IPFT / clearing (where applicable) | % of value | both | per exchange |
| GST | 18 % of (brokerage + exchange charge + SEBI fee [+ IPFT]) | both | all |
| DP charge | ₹ per scrip per sell day (+ GST) | sell | CNC |
| Auto square-off / call-and-trade fees | ₹ per order | – | broker profile |

### 12.2 Versioning

- Cost schedules live in the meta DB: `lab_cost_schedules`, with rows valid from an effective date, per (segment,
  component).
- Broker profiles (brokerage, DP, square-off fees, leverage) live in `lab_broker_profiles`. Profiles are user-editable
  and ship with seeded defaults marked "verify".
- Each fill uses the rates in force on its trade date. This matters, for example, for the F&O STT revision of
  1 Oct 2024 [E-cost].

### 12.3 Where costs apply

Costs are computed per fill and per charge. The breakdown is stored with each fill. Run reports show the cost drag by
component, which for intraday equity is typically dominated by STT on sells. The median cost was ₹370 per round trip
on ₹10 lakh in the prototype [E9].

### 12.4 Rounding and reconciliation

- Each charge is rounded to paise per order (configurable per broker: per order or per contract note).
- A reconciliation test suite holds anonymised real contract notes (added by users) as golden files. The engine must
  reproduce every line to the paisa.

### 12.5 Stress

`cost_multiplier` (e.g. 1.5×) and `extra_slippage_ticks` are standard robustness variants (§15.4).

---

## 13. Portfolio, sizing and risk

**Sizing methods** (per rule): fixed quantity or lots; fixed value (₹); % of equity; % of cash; volatility target
(ATR- or stdev-based, e.g. 0.5 % daily risk per position); risk per trade (account risk % ÷ stop distance); fractional
Kelly (capped); equal-weight, inverse-vol or risk-parity target weights (rebalancing strategies).

**Portfolio constraints:** max positions; max weight per instrument, sector and segment; gross and net exposure caps;
leverage cap per product; a minimum cash buffer.

**Rebalancing** (bt-style algos, reimplemented): a calendar (daily/weekly/monthly/first trading day) or drift bands;
weights from IR signals (rank, score, filter); turnover limits; trading in rank order within cash limits.

**Risk controls during a run:**
- daily loss limit (stop trading for the session);
- max drawdown circuit (stop the strategy);
- per-instrument cool-down after a stop-out;
- a kill switch (live mode).

Each control is evaluated at P4 and acts at the next P1.

**Benchmarks:** NIFTY 50 / 500 total return (if available), or any stored series. Used for relative metrics.

---

## 14. Metrics and analytics

### 14.1 Annualisation

- `periods_per_year` = trading sessions per year, from the *calendar for the run's exchange and period*, × bars per
  session (interval).
- Never a 365-day default. Vectorbt's 365-day default overstated a 5m Sharpe 2.38× [E5].
- The sessions-per-year used is printed with every annualised number.

### 14.2 Metric set

| Family | Metrics |
|---|---|
| Returns | total, CAGR, annualised volatility, Sharpe, Sortino, Calmar, Omega, tail ratio, skew, kurtosis, best/worst day/month |
| Risk | max drawdown (depth, duration, recovery), Ulcer index, VaR/CVaR (historical), time under water |
| Trades | count, win rate, payoff ratio, expectancy (₹ and R), profit factor, SQN, average and median holding time, MAE/MFE distributions, consecutive wins/losses |
| Exposure | time in market, average gross/net exposure, turnover, margin utilisation |
| Costs | total and per component, cost / gross P&L, break-even cost multiplier |
| Relative | alpha, beta, correlation, information ratio, up/down capture vs benchmark |
| Breakdowns | by year/month (heat map), weekday, time of day (session buckets), instrument, side, regime (e.g. trend/vol quantiles of the benchmark) |
| Research | DSR, PBO, walk-forward efficiency, parameter-stability score, robustness pass/fail matrix |

### 14.3 Computation paths

| Path | Where | What it produces |
|---|---|---|
| Screening | accumulators in the core (Welford moments, drawdown, counts) | O(1) memory per variant, as in the prototype [T1/T6] |
| Full run | equity, positions and trade records to Parquet | every metric computed from artefacts, so they can be recomputed without re-simulating |

---

## 15. Research and strategy generation

### 15.1 Projects and the trial registry

- A **research project** fixes the universe, period, hold-out vault period, objective(s), constraints (minimum trades,
  maximum drawdown) and the validation protocol.
- Every evaluated variant, in any search, is a row in `lab_trials` (params, IR hash, fold, metrics).
- The registry is **append-only**. DSR and PBO are computed from *all* trials, including abandoned searches, so
  "researcher degrees of freedom" are counted.

### 15.2 Search methods

| Method | Engine | Notes |
|---|---|---|
| Grid / random | core sweep driver (fused, parallel, memory-flat) | 12,200 intraday backtests in ~3 s on 4 cores [T1] |
| Bayesian / multi-objective | optuna 5 (TPE, CMA-ES, NSGA-II) | Lab provides batched objective evaluation; pruning on early folds |
| Genetic programming over the IR | core operators (subtree crossover/mutation respecting IR types); tournament selection; novelty and parsimony pressure | Generates *rules*, not just parameters. Every candidate is IR, so it is safe and shareable. |
| Templates | curated IR templates (breakout, mean reversion, ORB, pairs, momentum rotation) with typed holes | A good starting population for GP |

### 15.3 Validation protocol (default)

1. **Screening** on the in-sample period with the cheap fill model. Minimum trade count and cost-inclusive objective.
2. **Re-evaluation** of the top K with the full fill model (magnifier, participation cap, full costs).
3. **Walk-forward**: rolling or anchored windows. Parameters are chosen in-sample and applied out-of-sample. Report
   walk-forward efficiency (OOS / IS performance).
4. **CPCV**: combinatorial purged cross-validation, with a purge equal to the maximum holding period and an embargo
   ≥ 1 session. Produces a distribution of OOS Sharpe.
5. **PBO** (probability of backtest overfitting) across the candidate set.
6. **DSR** for the selected candidate, with N = the registry's trial count and the skew/kurtosis of its returns.
7. **Robustness suite** (§15.4).
8. **Acceptance gates** (configurable defaults): DSR ≥ 0.95, PBO ≤ 0.2, WFE ≥ 0.5, all robustness tests pass,
   ≥ 100 OOS trades.
9. The **hold-out vault** is opened once on user action. The result is recorded and never used for selection.

**Evidence [E9].** On data with no edge, the in-sample winners had Sharpe +1.33 and out-of-sample −0.82; DSR was
0.00. The gates exist because the search *will* find noise.

### 15.4 Robustness tests

| Test | What changes | Passes if |
|---|---|---|
| Parameter perturbation | ±1–2 grid steps around the optimum | performance degrades smoothly (a plateau, not a spike) |
| Cost stress | costs ×1.5, +1 tick slippage | still positive after costs |
| Delay | signals executed one bar later | not dependent on one-bar precision |
| Fill policy | pessimistic ↔ magnifier ↔ ohlc_direction | conclusion unchanged |
| Monte Carlo | trade-order resampling; block bootstrap of returns | 5th-percentile drawdown within limits |
| Noise | OHLC jitter within the bar range (seeded) | rank of the candidate stable |
| Universe | leave-one-symbol / sector out; other liquid symbols | edge not concentrated in a few names |
| Regime | split by volatility/trend regime of the benchmark | no single regime carries all P&L |

### 15.5 Optional: LLM-assisted ideation

- Natural-language idea → IR draft (local model, or a user-provided API key).
- The result is always IR, validated and type-checked. It never becomes code.
- Ideation trials count in the registry like any others.
- Off by default.

---

## 16. Paper and live trading: guardrails first, then live

### 16.1 Principle

The same strategy version, ExecSpec and cost profile run in every mode. Only the clock, the order route and the fill
source change. A strategy is **promoted** through stages by explicit owner approval, and **demoted** automatically
when a guardrail trips.

| Element | Backtest | G1 Paper | G2 OpenAlgo Analyzer | G3 Guarded pilot | G4 Live |
|---|---|---|---|---|---|
| Bars | snapshot / replica | FollowService closed bars (Historify) | same | same | same |
| IR evaluation | batch, fused | streaming (same evaluator) | same | same | same |
| Order route | – | Lab simulated broker | OpenAlgo with **analyze mode on** (sandbox fills, `SB-…` ids) | OpenAlgo → broker, **real orders**, hard caps | OpenAlgo → broker, caps raised by schedule |
| Fills | fill model | fill model on live bars | OpenAlgo sandbox engine | broker | broker |
| Costs | schedule | schedule | schedule | contract note, reconciled to the schedule | same |

### 16.2 Promotion ladder (exit criteria are defaults; owner-configurable, never disable-able)

| Stage | Entry requires | Minimum duration | Exit criteria to the next stage |
|---|---|---|---|
| **G0 Validated** | Research gates passed (§15.3: DSR, PBO, WFE, robustness) | – | Owner approves paper trading |
| **G1 Paper** | Strategy version frozen (hash) | 20 sessions | Paper decisions **identical** to a batch re-run of the same sessions (evaluator parity = 100 %); no guardrail breach; a data-staleness drill passed |
| **G2 Analyzer** | OpenAlgo analyzer mode confirmed ON by API before every order; broker session healthy | 10 sessions | OMS state machine reconciles 100 % with the OpenAlgo order and trade books; restart/recovery drill (kill the app mid-session → resumes without duplicate orders); auth-expiry drill; kill-switch drill |
| **G3 Guarded pilot** | Live prerequisites done (§16.5); broker profile **verified**; statutory schedule **verified**; owner types the strategy name | 20 sessions | Realised slippage within the modelled band (median ≤ model + 1 tick); costs reconcile **to the paisa** with contract notes; no P0 incident; drawdown within the pilot budget |
| **G4 Live** | Owner approval | continuous | Caps are stepped up at most 2× per 10 clean sessions, up to the owner-set ceiling |

**Automatic demotion.**
- Any *hard* guardrail breach, a reconciliation mismatch, auth loss with open positions, or evaluator divergence
  demotes the strategy to **G2**. Open positions are handled by the `on_demotion` policy: `flatten` (default in
  G3) or `hold_with_stops`.
- Demotion is one click to undo only after the root cause is recorded.

### 16.3 Guardrail catalogue

Hard guardrails block the action. Soft guardrails alert. All are evaluated in the main-process OMS, never inside
strategy code.

| ID | Guardrail | Default in G3 | Type |
|---|---|---|---|
| G-PRE-1 | Symbol and product whitelist (per strategy) | explicit list; MIS/NRML only as configured | hard |
| G-PRE-2 | Max quantity per order (lots) / max notional per order | 1 lot / ₹1 lakh (cash) | hard |
| G-PRE-3 | Price collar: limit price within ±x % of last price; market orders only in liquid symbols (ADV filter), otherwise marketable-limit | ±1 % | hard |
| G-PRE-4 | Fat-finger: quantity ≤ y % of the average 1m volume | 2 % | hard |
| G-PRE-5 | Max open positions, gross and net exposure | 2 positions / ₹2 lakh gross | hard |
| G-PRE-6 | Margin: OpenAlgo `margin()` + 20 % buffer ≤ available funds (`funds()`) | on | hard |
| G-PRE-7 | Order rate: below broker limits **and** below the exchange/SEBI algo threshold for unregistered algos | ≤ 1 order/s, ≤ 60/min | hard |
| G-PRE-8 | Idempotency: a unique client order key per intent; no resubmission without a reconciled state | on | hard |
| G-PRE-9 | Trading window: session open, not after `no_entry_after`, not on price-band lock, not in a calendar-flagged special session unless allowed | on | hard |
| G-IN-1 | Mandatory protective stop for every entry (broker-side SL/bracket where supported, else an engine-managed stop plus a resting broker SL order) | on | hard |
| G-IN-2 | Daily loss limit → stop new entries, then flatten | ₹2,000 | hard |
| G-IN-3 | Max drawdown from the pilot start (equity peak) → demote | ₹10,000 | hard |
| G-IN-4 | Max consecutive losses → pause the session | 5 | soft → hard |
| G-IN-5 | Data staleness: no closed bar within interval + 30 s → no new entries | on | hard |
| G-IN-6 | Engine square-off before the broker window (D-09) | 15:10 | hard |
| G-POST-1 | Fill reconciliation: every broker fill matched to an intent; position book equals the OMS | on | hard |
| G-POST-2 | Slippage drift: rolling median of realised − modelled > threshold | 2 ticks | soft → demote |
| G-POST-3 | Cost reconciliation with contract notes (daily import) | to the paisa | soft → blocks G4 |
| G-SYS-1 | Kill switch (UI, CLI, global hotkey): cancel all open orders, optionally flatten; works even if the strategy worker is hung | on | – |
| G-SYS-2 | Watchdog / heartbeat between the OMS and workers; missed heartbeat → no new orders | 5 s | hard |
| G-SYS-3 | Clock check: NTP offset ≤ 1 s, otherwise no trading | on | hard |
| G-SYS-4 | Single-instance lock (existing Historify singleton) also gates the OMS | on | hard |
| G-SYS-5 | Broker session / auth health; pre-open re-login check | on | hard |
| G-SYS-6 | Append-only audit log of decisions, intents, orders, fills and guardrail events (hash-chained) | on | – |
| G-SYS-7 | Live blocked while the broker profile / statutory schedule is unverified (D-13) | on | hard |

Defaults are placeholders sized for a small pilot. The owner sets them per strategy. Lowering a hard limit is always
allowed; raising one is logged and needs G4.

### 16.4 OMS and reconciliation

- **OMS state machine:** `intent → validated → sent → acknowledged → (partially_)filled | rejected | cancelled`.
- **Journal:** the meta DB `lab_orders` / `lab_fills`, written **before** the OpenAlgo call, which makes it
  restart-safe.
- **Reconciliation loop:** poll the OpenAlgo `orderbook` / `tradebook` / `positionbook`, and the WebSocket order feed
  where the broker supports it. Every fill is compared with the engine's **expected fill** on the same bar (timing,
  price, cost). The drift dashboard feeds the slippage model (G-POST-2).
- **Broker-side protection:** in G3/G4 every position has a resting broker SL order. If Historify dies, the broker
  still holds the stop.

### 16.5 Live prerequisites (before G3; verify each with the chosen broker)

- The SEBI retail algorithmic-trading framework (circular of Feb 2025, phased implementation through 2025): API
  access through the broker with registered apps and 2FA/OAuth, **static IP whitelisting** for order APIs, and
  **algo registration / algo-ID tagging** where the strategy exceeds the exchange's order-rate threshold. Confirm the
  current requirements and the broker's implementation. A local desktop usually needs a static IP from the ISP (or a
  small static-IP relay).
- Broker API terms for automated trading. Daily-login automation (TOTP) is permitted only as the broker allows.
- Tax/P&L treatment is outside scope, but the contract-note import (G-POST-3) provides the data.

## 17. Reproducibility and provenance

**Run manifest** (stored as JSON in `lab_runs.manifest_json` and as `manifest.json` next to the artefacts; sha256
recorded):

```json
{
  "format": "historify-lab-run/1",
  "run_id": "…", "created_at": "…", "mode": "backtest",
  "engine": {"hbt": "1.0.0", "hbt_core": "1.0.0", "ir_version": 1, "exec_spec_version": 1, "platform": "win_amd64"},
  "dataset": {"kind": "snapshot", "id": "…", "manifest_sha256": "…", "interval": "5m", "range": ["2023-01-02", "2024-12-31"],
              "adjustment": "adjusted", "ca_events_applied": 3, "calendar_hash": "…", "caveats": {"incomplete_sessions": 2}},
  "strategy": {"id": "…", "version": 7, "ir_sha256": "…", "params": {"fast": 9, "slow": 21, "sl": 0.005},
               "code_sha256": null, "callbacks": []},
  "exec_spec": {"sha256": "…", "fill_timing": "next_open", "intrabar": "pessimistic", "bracket_live_on_entry_bar": true, "…": "…"},
  "costs": {"broker_profile": "discount-20", "schedule_versions": {"EQ_INTRADAY": "2024-10-01"}, "multiplier": 1.0},
  "capital": {"initial_paise": 100000000},
  "seed": 12345,
  "results": {"artefacts": {"trades.parquet": "sha256…", "equity.parquet": "sha256…"}, "metrics_sha256": "…"}
}
```

**As implemented (Phase 1.3, `hbt.run`):** `strategy {tier: ir | python | arrays, ir_sha256, ir_version, ops_version,
params, causality}`, `dataset {sha256, interval, bars, adjustment, trade_start, frames, ca_events, provenance}`,
`dataset_sha256`, `plan_sha256`, `decisions_sha256` (every intent), `warmup_bars`, `exec_spec` (resolved) +
`exec_spec_sha256`, `order_plan_sha256`, `instrument_sha256`, `costs_sha256`, `costs_caveats` (e.g.
`broker_tariff_unverified`, `no_brokerage`), `cash0_paise`, `periods_per_year`. The boolean-array API records
`dataset_sha256` and `signals_sha256` instead of the strategy block. Artefact hashes arrive with the runs table (1.4).

**Re-run** loads the manifest, verifies that all hashes are available (dataset, strategy, exec spec, schedules) and
runs again. It then compares artefact hashes: identical means ✔; otherwise a structured diff (first diverging
fill/bar) is shown.

**Run diff** (Compare page) shows the differences between two runs (data, strategy, spec, costs, engine) and their
effect on metrics.

---

## 18. Persistence: schema and artefacts

### 18.1 Meta DB (SQLite, Alembic migration `0004_lab`)

Tables (DDL in Appendix D): `lab_strategies`, `lab_strategy_versions`, `lab_exec_profiles`, `lab_broker_profiles`,
`lab_cost_schedules`, `lab_runs`, `lab_run_metrics`, `lab_projects`, `lab_trials`, `lab_folds`, `lab_candidates`,
`lab_paper_sessions`, `lab_orders`, `lab_fills`. Plus the data-readiness tables from §8.2: `corporate_actions`,
`instrument_attributes`, `universe_membership`, `contracts`, calendar hash.

### 18.2 Artefacts (`instance/lab/`)

```
instance/lab/
  replica/bars.<as_of>.duckdb                     research replica (last 2)
  cache/<dataset_hash>/<interval>.arrow           engine cache, Arrow IPC LZ4 (LRU, size-capped, default 5 GB)
  runs/<run_id>/manifest.json, trades.parquet, orders.parquet, fills.parquet, equity.parquet, positions.parquet, log.jsonl
  projects/<project_id>/trials.parquet           bulk trial metrics (the meta DB keeps the index and summary)
```

**Sizes.** A typical intraday run with 5k trades produces under 5 MB of artefacts. A 1M-trial project stores about
100 B per trial in Parquet (~100 MB).

**Retention.** Runs are kept until deleted. "Scratch" runs (unsaved quick runs) expire after 7 days. The cache is LRU.
Weekly maintenance (existing) gains lab-cache eviction and replica rotation.

**Backups.** The existing verified backup includes the `lab_*` tables. Run artefacts are optional (they can be
regenerated from their manifests).

---

## 19. Jobs, APIs, CLI and SDK

### 19.1 Job integration

- New job kinds: `lab_run`, `lab_search`, `lab_validate`, `lab_replica_publish`, `lab_paper`. They use the existing
  JobService (status, progress, cancel, pause, history).
- **LabDispatcher** manages the worker processes:
  - spawned with `multiprocessing` (`spawn` context, portable to Windows);
  - pool size `min(cores − 1, setting)`;
  - each worker has a CPU-affinity/priority hint (below normal);
  - a memory budget per worker (the Universe streaming planner respects it).
- Workers talk to the app only through the meta DB (progress rows, result rows) and the filesystem (artefacts). They
  never touch `bars.duckdb` while the app is live.
- **Backpressure:** when a download job is running, research workers yield (lower priority, with a configurable cap of
  downloads-first CPU share).
- **Cancellation:** a cooperative check between jobs/chunks; the core checks an atomic flag every 64k bars.

### 19.2 REST API (`/api/v2/lab/*`, JSON, same error model as API v2)

Key endpoints (full catalogue in Appendix E):
- `POST /lab/runs`, `GET /lab/runs/{id}`, `GET /lab/runs/{id}/trades?…`, `GET /lab/runs/{id}/equity?…`,
  `POST /lab/runs/{id}/rerun`, `GET /lab/runs/compare?a=&b=`
- `POST /lab/strategies`, `POST /lab/strategies/{id}/versions`, `POST /lab/strategies/validate` (IR check,
  pseudo-code)
- `POST /lab/projects`, `POST /lab/projects/{id}/search`, `POST /lab/projects/{id}/validate`,
  `POST /lab/projects/{id}/vault/open`
- `GET/PUT /lab/profiles/exec|broker|costs`
- `POST /lab/replica/publish`

Columnar payloads for charts use the existing `{t,o,h,l,c,v}` convention.

### 19.3 CLI

```bash
flask --app run.py historify lab run --strategy ema-cross@7 --snapshot eval-50 --interval 5m --exec default
flask --app run.py historify lab search --project p1 --method grid --workers 3
flask --app run.py historify lab rerun <run_id>
hbt run strategy.json --snapshot /path/to/snapshot --interval 5m     # standalone, no app (notebooks/CI)
```

### 19.4 Python SDK

```python
import hbt
u = hbt.open_snapshot("instance/snapshots/eval-50-4201446f").universe("5m", adjust="adjusted")
s = hbt.Strategy.from_ir("ema_cross.json")
r = hbt.run(s, u, exec=hbt.ExecSpec.default(), costs="discount-20")
r.metrics, r.trades.to_polars(), r.equity.to_pandas()
grid = hbt.search.grid(s, u, fast=range(5, 31), slow=range(21, 121, 10), sl=[0.003, 0.005, 0.01])
report = hbt.validate.walk_forward(grid, folds=4).deflated_sharpe()
```

---

## 20. User interface

All pages follow the existing v2 conventions: Jinja shells, Alpine CSP components, no inline scripts or styles, and
Lightweight Charts.

| Page | Content |
|---|---|
| **Strategies** | Library (search, tags, versions); IR form builder (signals / rules / risk / params) with live validation; read-only pseudo-code; JSON view; diff between versions; import/export (IR only by default) |
| **Backtests → New run** | Strategy@version, dataset picker (snapshots / replica / live-quick), interval, range, ExecSpec profile (with an expandable "semantics" panel showing every choice), broker/cost profile, capital, params |
| **Run results** | Header (status, data caveats, reproducibility badge); equity + drawdown (Lightweight Charts); **trades on the price chart** (entry/exit markers, stops/targets as price lines); metrics grid; monthly heat map; time-of-day chart; cost breakdown; trade table (virtualised) with "why this fill" (phase, rule, price source) |
| **Research** | Projects; search config; live progress; heat maps (2-D slices, canvas); parameter-stability plot; Pareto front (multi-objective); validation report (WFO table, CPCV distribution, PBO, DSR) with acceptance gates as pass/fail chips; candidates list; vault status |
| **Compare** | Two to four runs: metric deltas, overlaid equity, manifest diff |
| **Paper / Live** (M7+) | Sessions, positions, orders, fills, reconciliation drift, guardrail status, kill switch |
| **Charts integration** | "Backtest this" on the Charts page opens a quick-run drawer (IR template, default exec) for the current symbol and interval, and overlays the trades |

---

## 21. Performance engineering

### 21.1 Techniques (each validated or prototyped)

| Technique | Evidence |
|---|---|
| Fused evaluation: signals generated inside the parallel per-variant loop | [T1] sweep, memory-flat |
| Statistics on the fly (Welford, drawdown) in screening; no equity arrays | 387 MB for 12,200 variants [E9] |
| Symbol-major streaming; time-blocked portfolio | 227 MB at 187.5 M bars [T6]; 9.3 ns/cell [T7] |
| Zero-copy Arrow ingestion | 0.175 s for 9.8 M rows [T10] |
| Arrow IPC LZ4 engine cache | 0.145 s for 9.8 M bars [T3] |
| Shared indicator buffers across variants (CSE by `(fn, args)` hash) | design; expected 2–5× on grids that share `fast`/`slow` |
| No bounds checks in hot loops (slices / `chunks_exact`) | removing 2-D indexing closed a 40 % gap [T1] |
| Target-CPU strategy: baseline x86-64-v2 plus runtime dispatch to AVX2 paths for indicator batches | design |
| rayon work stealing over (symbol × variant) jobs | [T1] |

### 21.2 Benchmarks and gates

- **Rust**: `criterion` micro-benchmarks (kernel per bar, indicators, IR evaluator).
- **Python**: an end-to-end suite (the NFR table scenarios) on a fixed synthetic dataset (generator seeded, as in
  `build_store.py`).
- **CI perf job** on a dedicated runner class: fail the build on a > 15 % regression against the stored baseline.
- **Profiling hooks**: `HBT_PROFILE=1` emits per-phase timings (load, compile, simulate, write); `perf`/`samply`
  friendly symbols in release builds.

---

## 22. Verification and quality

| Layer | What | Tooling |
|---|---|---|
| Unit | every order type, phase, rule, charge | `cargo test`, pytest |
| **Golden scenarios** | hand-computed micro cases (≤ 20 bars) per rule: gap through stop, stop and target in one bar (each policy), bracket on the entry bar, MIS square-off with a pending entry, partial fills, band lock, lot change, split in raw mode, option expiry ITM with STT on intrinsic value, margin call | YAML fixtures → expected fills/cash to the paisa |
| **Differential (oracles)** | independent reference implementations (E3/E5 specs); backtesting.py (bracket semantics); backtrader (signed-cost semantics); TA-Lib (indicators) | CI job; must be exact (money) / ≤ 1e-12 (analytics) |
| **Property-based** | invariants: cash ≥ 0 in cash accounts; qty % lot = 0; fill ∈ [low, high] of its bar; fees ≥ 0; equity = cash + Σ qty × mark; no fill on invalid bars; determinism (run twice) | `proptest`, `hypothesis` (already a dev dependency) |
| **Look-ahead fuzzing** | randomly perturb all bars after t; every decision, order and fill ≤ t must be unchanged; run over every IR operator and the Python-tier ctx | custom harness; mandatory for new operators |
| **Contract-note reconciliation** | real notes (user-provided, anonymised) reproduced to the paisa | golden files |
| **IR fuzzing** | parser/type checker never panics; invalid IR always produces a structured error | `cargo-fuzz` |
| **Reproducibility** | re-run equals byte-identical artefacts; cross-OS money identical | CI matrix (Linux, Windows) |
| **UI** | pages, CSP compliance, charts render, run flow end-to-end | Playwright (existing setup) |
| **Coverage** | `hbt` ≥ 90 %, `hbt-core` ≥ 90 % lines; `app/lab` ≥ 85 % (matches the existing gate) | coverage, `cargo llvm-cov` |

---

## 23. Security, packaging, operations

### 23.1 Security

- **Trust model.**
  - IR strategies are data. They can be imported and shared freely, and the evaluator is total (no I/O, no
    unbounded recursion; size limits).
  - Python and compiled strategies are **code**. Importing one requires an explicit "trust this code" step, which
    shows the file hash and diff.
  - Untrusted code never runs in the app process.
- **Worker hardening.** Workers run with a scrubbed environment (no OpenAlgo key or app secrets) and a network-deny
  policy where the OS supports it (Linux seccomp/namespaces best-effort; Windows job objects). They may write only
  under `instance/lab/`. Output paths are never derived from strategy input.
- **API.** The same loopback-only binding, CSP and no-login assumptions as Historify. All Lab endpoints validate
  sizes: IR ≤ 256 KB; at most 10⁷ search-space points per request unless confirmed.
- **Live.** Guardrails from §16, an audit log of every order intent and decision, and secrets only in the main process
  (OMS adapter).

### 23.2 Packaging

| Artefact | Contents |
|---|---|
| `hbt-core` wheels | abi3-py312; `manylinux_2_28_x86_64`, `win_amd64`; macOS arm64 best-effort; built with `maturin-action` in CI; reproducible (locked Cargo.lock, `--locked`) |
| `hbt` | pure Python package inside the Historify repo (`historify/lab/` or `packages/hbt/`) |
| `historify[lab]` extra | hbt-core, optuna; **no** numba, polars or pyarrow by default (≈ 6 MB measured: core 0.3 MB + optuna 5 MB) |
| `historify[lab-jit]` | numba for compiled callbacks (+186 MB) |
| `historify[lab-notebook]` | polars (+174 MB), for the IR-to-Polars adapter and notebook convenience |
| Dev/test only | backtesting.py, backtrader, TA-Lib (oracles) |

The CI licence gate is updated as recommended in the engine review (allow GPL/AGPL/LGPL; deny Commons Clause and
non-commercial; reviewed overrides for UNKNOWN metadata).

### 23.3 Operations and observability

- **Logs:** structured JSON per run (`log.jsonl`): phase timings, counters (orders, fills, rejects by reason,
  partials), data caveats.
- **Metrics:** bars/s, variants/s, memory high-water mark, worker utilisation. Shown on the Jobs page and the Lab
  dashboard card.
- **Health:** `/healthz` gains `lab: {workers, busy, replica_as_of}`.
- **Maintenance:** replica rotation, cache eviction, scratch-run expiry, trial-registry vacuum. Integrated into the
  existing weekly maintenance.

---

## 24. Roadmap, risks, decisions

### 24.1 Milestones

Sequenced by dependency. Estimates are engineer-weeks for one senior engineer, shown to size the work, not as a
commitment.

| M | Scope | Exit criteria | Est. |
|---|---|---|---|
| **M0** Data readiness | Historical calendar + hash; corporate actions + CA-candidate audit; `available_at`; instrument attributes; universe membership; derivatives master + expiry calendar; manifest v2; research replica; CI licence gate | Ledger clean on 10-year history; the planted split is detected; multi-timeframe leak test at 0 %; external reads via the replica | 3–4 |
| **M1** Core foundations | Rust workspace, types (ticks/paise), Universe (Arrow ingestion), ExecSpec v1, kernel P0–P4 for cash equities (long/short, market/limit/stop/bracket/trailing, MIS/CNC), costs v1, metrics accumulators, determinism | Golden suite + oracle parity (E3/E5 specs) + property tests green; NFR-1…3 met | 6–8 |
| **M2** Strategy layer | IR v1 (schema, type checker, causality check, evaluator batch+stream), 60 indicators with TA-Lib parity, Python tier, compiled-callback ABI | Look-ahead fuzzing green for all operators; T2 tier costs reproduced | 5–7 |
| **M3** Historify integration | Job kinds, LabDispatcher/workers, API, Strategies/Backtests/Run pages, charts overlay, artefacts, manifests, re-run/diff | End-to-end UI flow in Playwright; reproducibility gate | 5–6 |
| **M4** Portfolio + sizing + risk | Time-blocked portfolio kernel, sizing methods, constraints, rebalancing algos, benchmarks | NFR-5; bt-style rebalancing scenarios match the reference | 4–5 |
| **M5** Research | Sweep driver, optuna integration, trial registry, WFO/CPCV/PBO/DSR, robustness suite, Research page, vault | E9 demo reproduced in the product; gates configurable; NFR-4 | 6–8 |
| **M6** F&O | Contracts, expiry/settlement, margin L0/L1 (L2 import), options selection and Greeks, STT on exercise, continuous futures for signals | F&O golden scenarios; a hand-checked monthly expiry case | 6–8 |
| **M7** Paper + Analyzer (G1–G2) | Streaming runtime on FollowService bars, simulated broker, OMS + journal, OpenAlgo analyzer-mode route, guardrails G-PRE/G-IN/G-SYS, kill switch, Paper page, reconciliation | G1 and G2 exit criteria (§16.2) met on one pilot strategy | 4–6 |
| **M8** Guarded pilot → Live (G3–G4) | Live route, G-POST guardrails, contract-note import + cost reconciliation, slippage-drift monitor, promotion/demotion workflow, hash-chained audit log, live prerequisites (§16.5) | G3 exit criteria met; security review; owner sign-off for G4 | 4–6 |
| **M9** Generation+ | GP over the IR, templates, optional LLM ideation, 1m-magnifier default for validation | GP finds planted signals in synthetic data and rejects noise (DSR) | 3–5 |

**Critical path:** M0 → M1 → M2 → M3, about 19–25 weeks for the first user-visible release. The rest can be
parallelised by a second engineer (M4/M5 with M6).

### 24.2 Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Semantics drift between batch, streaming and live | wrong live behaviour | One evaluator for both modes; a shadow paper check (M7 exit) replays live bars in batch and compares |
| Market-rule data quality (calendar, CA, lots, expiry) | silently wrong results | Data-as-tables with validity dates; audits; data caveats on runs; "verify" flags on seeded data |
| Overfitting despite gates | false confidence | Registry-wide DSR/PBO, vault, robustness suite. UI language: "candidate", never "profitable" |
| Rust skills / bus factor | velocity | Small, well-documented core; Python owns orchestration; golden tests document behaviour; pair on M1 |
| Toolchain and ABI churn (PyO3/arrow lockstep) | build breaks | Renovate groups; abi3; pinned toolchain; nightly canary build |
| Windows specifics (spawn, AV scanning of wheels, paths) | UX issues | Windows in the CI matrix from M1; spawn-only multiprocessing; no symlinks |
| CPU contention with downloads | missed post-close sync | Dispatcher priority, CPU caps, yield when downloads run (NFR-11) |
| Live-trading liability | financial loss | Ship last, opt-in, sandbox-first, guardrails, kill switch, audit log |

### 24.3 Phase-1 plan (v1.2): build → 3-month paper (G1–G2) → guarded pilot

| Weeks | Track | Deliverable |
|---|---|---|
| 0 → | Data | Broker probe + choice; provider profile; **1y 1m + 10y D backfill; post-close schedule ON (forward accumulation starts now)** |
| 1–3 | M0-lite | OpenAlgo calendar (last 2 years), NSE CA file import (1y) + CA audit, `available_at`, lot/tick attributes, replica, weekly snapshot, CI licence gate |
| 2–7 | M1-lite | Rust core: cash equity long/short, market/limit/stop/bracket/trailing, MIS 15:10, costs, `decision_latency_s`, metrics, golden + oracle parity |
| 5–9 | M2-lite | IR v1 (~25 indicators + session functions), causality check, **batch + streaming evaluator**, Python builder |
| 8–11 | M7a (G1) | System Follow (D-21), strategy host, simulated broker with shadow quotes (D-24), OMS journal, G-PRE/G-IN guardrails, kill switch, decision journal, Paper page |
| 9–11 | Research | Select the first paper cohort on pre-T0 data (walk-forward on 12 months, DSR, robustness) |
| **~12** | **T0** | **Paper starts (G1)**; 3-month clock |
| 12–15 | M7b (G2) | OpenAlgo analyzer route, order-update stream, reconciliation, restart/auth/kill drills; promotion to G2 after 20 clean G1 sessions |
| 12–24 | Research + UI | Sweeps, optuna, WFO/CPCV/PBO/DSR, robustness, Backtests/Research pages; monthly cohort additions |
| 12–24 | Live prerequisites | Static IP, broker app registration / SEBI algo items, tariff + statutory verification (D-13) |
| **~25** | Review | Paper-phase report against D-19 → owner decision on G3 (guarded pilot) |

### 24.4 Decisions

All v1.0 decisions are resolved in §0 (D-01…D-16). Still open for the owner:

1. **Broker(s)**: data provider and execution broker. See the shortlist and verification checklist in
   [BROKER_COST_AND_DATA_COMPARISON.md](BROKER_COST_AND_DATA_COMPARISON.md). Run `broker_data_probe.py` on the
   shortlisted brokers before deciding.
2. **Pilot risk budget** for G3: per-order, daily-loss and drawdown limits (defaults in §16.3 are placeholders).
3. **Who verifies** the statutory schedule and the broker tariff before G3 (D-13 blocks live until then).

---

## Appendix A: Evidence register

| ID | Claim | Result | Script → raw result |
|---|---|---|---|
| E1 | External process cannot open the live store | read-only and read-write both fail ("Conflicting lock") | `harness/lock_test.py` |
| E3 | Engines agree with the reference under identical semantics | 5 engines; max error ≈ 5e-15 | `harness/run_*.py` → `results/parity_*.json` |
| E4 | Daily sweep speed (5,000 backtests) | numba 0.04–0.06 s · vectorbt 11.9 s · backtesting.py ~56 s · backtrader ~1,200 s | `harness/sweep_*.py` → `results/sweep_*.json` |
| E5 | Intraday semantics divergence | backtesting.py 49/50 exact; vectorbt median 10.6–11.0 %; root cause proven 50/50; Sharpe 2.38× inflated | `harness/e5_*.py` → `results/e5_*.json` |
| E8 | Cost-model interfaces | backtesting.py exit side lost; backtrader/bt signed; Nautilus order-aware | `harness/e8_*.py` |
| E9 | Search without validation finds noise | 12,200 tries: IS +1.33 → OOS −0.82; DSR 0.00 | `prototype/strategy_search_demo.py` → `results/strategy_search_demo.json` |
| E-LA | Look-ahead via bar-open timestamps | 100 % of 5m bars saw the same day's close; 0 % with `available_at` | engine review §3 D3 |
| E-cost | Round-trip cost, ₹10 lakh intraday | median ₹370 | `results/strategy_search_demo.json` (log) |
| T1 | Rust vs numba kernel | bit-identical; 17.3 vs 21.8 ms (50 runs); sweep 3.16 vs 3.04 s; cold 0.29 vs 0.85 s | `tech/t1_rust_vs_numba.py` → `results/t1_rust_vs_numba.json` |
| T2 | Strategy tier cost per bar | vectorised 37 ns · cfunc via C pointer 25 ns · Python 1,594 ns; identical results | `tech/t2_strategy_tiers.py` → `results/t2_strategy_tiers.json` |
| T3 | Data plane formats (9.8 M bars) | snapshot → Polars 0.35–0.60 s; IPC LZ4 0.145 s; lazy scan 11 ms; sizes 133–509 MB | `tech/t3_data_plane.py` → `results/t3_data_plane.json` |
| T4 | Indicator throughput (29 M values) | numba 0.11 · TA-Lib 0.25 · Polars 0.44 · pandas 1.59 s | `tech/t4_indicators.py` → `results/t4_indicators.json` |
| T5 | Polars expression serialisation | round-trip identical; documented unstable across versions and pickle-capable | `tech/t5_expr_serialization.py` |
| T6 | Target scale streaming | 187.5 M bars × 4 variants in 10.3 s, 227 MB | `tech/t6_scale.py` → `results/t6_scale.json` |
| T7 | Portfolio kernel | 9.8 M cells in 0.091 s (9.3 ns/cell) | `tech/t7_portfolio.py` → `results/t7_portfolio.json` |
| T8 | Packaging | core wheel 293 KB; abi3 on 3.12/3.13/3.14; numba +186 MB; Polars +174 MB | `results/t8_packaging.txt` |
| T9 | Intrabar ambiguity and 1m magnifier | 0 % / 0.06 % / 1.3 % of bracket exits ambiguous (0.5/0.75, 0.3/0.45, 0.2/0.3 %); 2.0 % equity gap at the tightest | `tech/t9_bar_magnifier.py` → `results/t9_bar_magnifier.json` |
| T10 | Zero-copy DuckDB → Rust | 9.79 M rows, 80 batches, 0.175 s, pyarrow absent; 4× fetchnumpy | `tech/t10_arrow_zero_copy.py` + `tech/hbt_arrow_probe_rs/` |

**Not tested:** free-threaded CPython 3.14t. The interpreter could not be downloaded in the sandbox, because GitHub and
python.org were unreachable.

**Caveats:**
- Synthetic prices throughout.
- A shared sandbox, so timings vary up to ~2×.
- Statutory rates in the prototype are placeholders.

## Appendix B: ExecSpec schema

ExecSpec v1 **as implemented in Phase 1.2** (`historify/rust/hbt-core/src/spec.rs`, builder `lab/hbt/spec.py`). The
resolved spec (after `auto` values are fixed by the bar size) is what runs and what the manifest hashes.

```yaml
exec_spec_version: 1
fill_timing: next_open            # next_open | close (flagged)
decision_latency_s: 0             # 0..3600; paper-measured (D-23); > 0 fills on the 1m timeline when 1m bars are given
intrabar_policy: pessimistic      # pessimistic | ohlc_direction | magnifier_1m
bracket_live_on_entry_bar: true
same_bar_priority: chronological  # chronological (open events -> intrabar) | stop_first (vectorbt-compatible)
slippage_model: none              # none | ticks | pct      (slippage_value: ticks or fraction)
slippage_value: 0
participation_cap: auto           # auto = 0.10 on intraday bars, none on daily (D-09) | (0, 1] | null
participation_remainder: carry    # carry | cancel
zero_volume_fills: false
no_new_entries_after: "14:55"     # D-09; bars CLOSING after this time make no new entry decision
squareoff_at: "15:10"             # D-09 / G-IN-6: engine square-off before the broker window (MIS only)
product: MIS                      # MIS | CNC (no shorts) | NRML
allow_short: null                 # null = allowed unless CNC
leverage: 1.0                     # 1..20
price_band_pct: null              # static band; a bar locked at the band does not fill on the locked side
```

Fixed in v1 (not options): gap-through stops fill at the open (`gap_fill: at_open`), adverse tick rounding, netting.

**v1 errata (fix pack 1.2.1, audit 2; applied in place because no run had been persisted yet):**

| # | Change |
|---|---|
| F1 | `product: auto` (MIS on intraday bars, CNC on daily); session times (`no_new_entries_after`, `squareoff_at`) apply to intraday bars only; MIS on daily bars is refused; a run where ≥ 50 % of entry decisions are refused carries a warning |
| F2 | DAY validity of a resting entry counts from the session in which it becomes live (an order decided at a session's last bar, or on a daily bar, lives through the next session) |
| F3 | An exit or reversal cancels a carried entry remainder (`remainder_cancelled`); a remainder whose position is gone is dropped (`stale_remainder`) |
| F4 | `dividends: credit \| ignore` (default credit); corporate-action events are applied to positions on the ex-date (raw execution; design §8.4): quantity × ratio, levels ÷ ratio, cash in lieu of fractions |
| F5 | Zero-volume bars never fill (unless `zero_volume_fills`), with or without a participation cap |
| F6 | A partial forced MIS close keeps its carried remainder |
| F7 | Bars: ts must be epoch seconds in 2000–2100, prices finite and > 0, volume ≥ 0 |
| F8 | Every ExecSpec / OrderPlan field is range-checked; OrderPlan gains `sizing: risk_pct` (needs `stop_pct`) |
| F11 | Cost model gains a per-scrip, per-day DP charge on delivery sells; the Python builder refuses to guess the brokerage |
| L7 | `bracket_live_on_entry_bar = false` holds for the whole coarse entry bar (all its 1m segments) |
Planned for ExecSpec v2 (Phase 1.4–1.6, with the runs table and the portfolio layer): `open_price_source`,
`spread_proxy` / impact slippage, per-exchange square-off map (MCX / CDS), special / mock session flags,
`data_quality` policy, `adjustment`, `order_priority`, `margin_model`, `rng_seed`, hedge mode.

## Appendix C: Strategy IR

Implemented in Phase 1.3; the normative specification is [PHASE_1_3_STRATEGY_IR_DESIGN.md](PHASE_1_3_STRATEGY_IR_DESIGN.md)
§6 and the published JSON Schema `historify/lab/hbt/data/ir_v1.schema.json`.

**Top level:** `ir_version`, `name`, `description`, `params{}`, `constraints[]`, `universe{interval}`,
`data{adjustment}`, `signals{}`, `rules[]`, `product`, `meta{}` (`meta` and `description` do not change the hash).

**Expressions:** `{"fn", "args", "out"?, "tf"?}` · `{"col": open|high|low|close|volume|typical|hl2|$name, "tf"?}` ·
`{"sig"}` · `{"param"}` · number · bool · `"HH:MM"`.

**Rules → intents:** `enter_long | enter_short` (`order` market / limit / stop with `price` expression or
`offset_pct`; `size` all_in / pct_equity / fixed_qty / fixed_value / risk_pct; `bracket` stop / target as `{"pct"}` or
`{"price": expression}`, `trail_pct`, `time_stop_bars`; `expiry_bars`), `exit_long | exit_short | exit_all`. Exits
first; the first matching entry wins (`entry_conflicts` counts the others).

**Operator catalogue (ops_version 1, 86 operators: 25 indicators, 14 session functions, 6 position operators, 17 series operators, 24 arithmetic / comparison / logic):** arithmetic, comparison, logic and `where`; series (shift, diff,
pct_change, rolling mean / sum / std / min / max / quantile, expanding max / min, cumsum, bars_since, rising, falling,
cross_above / below); indicators (SMA, EMA, WMA, DEMA, TEMA, HMA, RSI, ROC, MOM, STDDEV, z-score, LINEARREG_SLOPE,
BBANDS, MACD, ATR, NATR, CCI, WILLR, MFI, STOCH, ADX / ±DI, Keltner, Donchian, OBV, session VWAP); session functions
(minute_of_day, bar_in_session, is_first / last_bar, session open / high / low, opening range, prior-day OHLC,
day_of_week); read-only position state (position_qty, entry_price, bars_in_trade, unrealised_pct,
highest / lowest_since_entry).

**Static rules:** shift ≥ 1 and no future-reading operator (causality); `tf` ≥ the run interval, resolved through
`available_at`; types (numeric / boolean series, constants); ≤ 10,000 nodes, depth ≤ 64, lookback ≤ 100,000 bars,
≤ 64 rules; declared lookback used for warm-up. Error codes: E-SCHEMA, E-CYCLE, E-UNKNOWN-FN, E-ARITY, E-TYPE,
E-CAUSAL, E-TF-LOWER, E-TF-UNKNOWN, E-LIMIT, E-PARAM-RANGE, E-GRID-SIZE, E-RISK-NO-STOP, E-INTENT, E-PRODUCT,
E-LOOKBACK-DATA.

**Versioning:** `ir_version` bumps on breaking grammar changes; additive operators bump `ops_version` (in the manifest).

## Appendix D: Meta DB DDL (abridged)

```sql
CREATE TABLE lab_strategies (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('ir','python','compiled')),
  created_at DATETIME NOT NULL, archived_at DATETIME);
CREATE TABLE lab_strategy_versions (strategy_id TEXT REFERENCES lab_strategies(id) ON DELETE CASCADE, version INTEGER,
  ir_json TEXT, code_blob BLOB, sha256 TEXT NOT NULL, trusted BOOLEAN NOT NULL DEFAULT 0, notes TEXT, created_at DATETIME NOT NULL,
  PRIMARY KEY (strategy_id, version));
CREATE TABLE lab_exec_profiles (id TEXT PRIMARY KEY, name TEXT UNIQUE, spec_json TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE lab_broker_profiles (id TEXT PRIMARY KEY, name TEXT UNIQUE, profile_json TEXT NOT NULL, verified_at DATETIME);
CREATE TABLE lab_cost_schedules (segment TEXT, component TEXT, effective_from DATE, rate_bps_x1e4 INTEGER, cap_paise INTEGER,
  side TEXT CHECK (side IN ('buy','sell','both')), basis TEXT, source TEXT, PRIMARY KEY (segment, component, effective_from, side));
CREATE TABLE lab_runs (id TEXT PRIMARY KEY, job_id TEXT, mode TEXT NOT NULL, strategy_id TEXT, strategy_version INTEGER,
  dataset_kind TEXT, dataset_ref TEXT, exec_sha256 TEXT, params_json TEXT, status TEXT NOT NULL, manifest_json TEXT,
  manifest_sha256 TEXT, artefact_dir TEXT, scratch BOOLEAN NOT NULL DEFAULT 0, created_at DATETIME NOT NULL, finished_at DATETIME);
CREATE TABLE lab_run_metrics (run_id TEXT REFERENCES lab_runs(id) ON DELETE CASCADE, key TEXT, value REAL, PRIMARY KEY (run_id, key));
CREATE TABLE lab_projects (id TEXT PRIMARY KEY, name TEXT UNIQUE, config_json TEXT NOT NULL, vault_from DATE, vault_to DATE,
  vault_opened_at DATETIME, created_at DATETIME NOT NULL);
CREATE TABLE lab_trials (id INTEGER PRIMARY KEY, project_id TEXT REFERENCES lab_projects(id) ON DELETE CASCADE, search_id TEXT,
  ir_sha256 TEXT, params_json TEXT, fold INTEGER, sharpe_pp REAL, n_obs INTEGER, trades INTEGER, net_pnl_paise INTEGER,
  metrics_json TEXT, created_at DATETIME NOT NULL);                -- append-only (trigger forbids UPDATE/DELETE)
CREATE INDEX ix_lab_trials_project ON lab_trials(project_id, search_id);
-- paper/live (M7-M8): lab_paper_sessions, lab_orders (state machine, idempotency_key UNIQUE), lab_fills (expected vs actual)
-- data readiness (M0): corporate_actions, instrument_attributes, universe_membership, contracts, calendar_hash in store_meta
```

## Appendix E: API catalogue (v1)

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/api/v2/lab/strategies` | list / create |
| GET | `/api/v2/lab/strategies/{id}` | detail with versions |
| POST | `/api/v2/lab/strategies/{id}/versions` | new immutable version |
| POST | `/api/v2/lab/strategies/validate` | IR validation, pseudo-code, declared lookback |
| POST | `/api/v2/lab/strategies/import` | IR import (code needs the `trust` flow) |
| GET/POST | `/api/v2/lab/runs` | list / start (returns job id) |
| GET | `/api/v2/lab/runs/{id}` | status, manifest, metrics, caveats |
| GET | `/api/v2/lab/runs/{id}/trades` · `/equity` · `/orders` | paged, columnar |
| POST | `/api/v2/lab/runs/{id}/rerun` | reproducibility check |
| GET | `/api/v2/lab/runs/compare?ids=` | metric deltas and manifest diff |
| GET/POST | `/api/v2/lab/projects` | research projects |
| POST | `/api/v2/lab/projects/{id}/search` | start a search (grid / random / optuna / gp) |
| POST | `/api/v2/lab/projects/{id}/validate` | WFO / CPCV / PBO / DSR / robustness for candidates |
| POST | `/api/v2/lab/projects/{id}/vault/open` | one-time hold-out evaluation (confirmed) |
| GET/PUT | `/api/v2/lab/profiles/{exec,broker,costs}` | profiles and schedules |
| POST | `/api/v2/lab/replica/publish` | publish a research replica now |
| GET/POST | `/api/v2/lab/paper` (M7) | sessions, start/stop |
| POST | `/api/v2/lab/live/kill` (M8) | kill switch |

## Appendix F: Code layout

Phase 1.3 state: three crates, `rust/hbt-ir` (Strategy IR: parser, compiler, plan), `rust/hbt-core` (modules money,
costs, spec, decision, engine, kernels, eval, metrics) and `rust/hbt-py`; Python modules `lab/hbt/{ir, dataset,
strategy, fuzz, engine, spec, costs, data}.py`. The module boundaries below are kept and become crates when they grow
(indicators, portfolio, search).

```
historify/
  lab/                                 # Python package "hbt" (ships with historify[lab])
    hbt/{__init__,data,universe,strategy,ir,run,research,validate,report,live}.py
  app/lab/                             # Historify integration: jobs, dispatcher, api, pages, migrations
  rust/
    Cargo.toml                         # workspace; pyo3 0.30 + pyo3-arrow 0.20 + arrow 59 in lockstep
    crates/hbt-types      (Ticks, Paise, Qty, ids, enums, fixed-point)
    crates/hbt-data       (Arrow ingestion, layouts, calendar/sessions, adjustments, available_at)
    crates/hbt-ir         (schema, type checker, causality check, planner, CSE)
    crates/hbt-ind        (indicators: batch + streaming)
    crates/hbt-exec       (orders, phases P0-P4, fill models, magnifier, market rules)
    crates/hbt-costs      (schedules, charges, rounding)
    crates/hbt-portfolio  (accounts, margin L0-L2, sizing, constraints, rebalancing)
    crates/hbt-metrics    (accumulators, full metrics from artefacts)
    crates/hbt-search     (sweep driver, GP operators, RNG)
    crates/hbt-py         (PyO3 bindings, abi3-py312) -> wheel "hbt-core"
  tests/lab/{golden,oracle,property,lookahead,recon,perf}/
```

## Appendix G: Glossary

| Term | Meaning |
|---|---|
| ExecSpec | The versioned set of execution semantics used by a run |
| IR | Historify's JSON strategy intermediate representation |
| MIS / CNC / NRML | Intraday / delivery / F&O-carry products of Indian brokers |
| Bar magnifier | Resolving the intrabar order of events with the stored 1m bars |
| Research replica | Read-only, point-in-time copy of the bar store for out-of-process readers |
| Trial registry | Append-only log of every evaluated variant in a research project |
| DSR | Deflated Sharpe ratio: significance corrected for the number of trials and for non-normality |
| PBO | Probability of backtest overfitting: the chance that the in-sample best is below median out of sample |
| CPCV | Combinatorial purged cross-validation |
| WFE | Walk-forward efficiency: out-of-sample vs in-sample performance |
