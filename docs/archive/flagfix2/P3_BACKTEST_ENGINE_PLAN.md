# P3 · Backtest & Research Engine: Implementation and Test Plan

| | |
|---|---|
| **Phase** | P3 of `PROJECT_ROADMAP_6_PHASES.md` (≈ 28 engineer-days, on the critical path P1 → **P3** → P4) |
| **Goal** | Test any trading idea on Indian market data **honestly**: point-in-time, after Indian costs and execution frictions, reproducibly, and with overfitting controls |
| **Builds on** | P1 reference data (calendar, corporate actions, bhavcopy, PIT index membership, lot sizes, ban list), P2 intelligence (events with `available_at`, disclosures, flows, pre-open), the existing engine (DuckDB bars, roll-up, session ledger, snapshots, jobs) |
| **Design source** | `BACKTESTING_AND_SIGNALS_HLD.md` §5–§11, §13–§14 (target design). This document is the **build plan**: what gets built in which order, the interfaces, and how each part is tested. |
| **Status** | **Implemented** with the recommended decisions (§9). Results, test evidence and the gaps fixed are in `P3_BACKTEST_ENGINE_REPORT.md`. |

---

## Contents
1. [Scope](#1-scope)
2. [Starting point: what P3 reuses](#2-starting-point-what-p3-reuses)
3. [Architecture and module layout](#3-architecture-and-module-layout)
4. [Key design decisions](#4-key-design-decisions)
5. [Work packages: implementation](#5-work-packages-implementation)
6. [How we test P3](#6-how-we-test-p3)
7. [Sequencing, milestones and effort](#7-sequencing-milestones-and-effort)
8. [Acceptance criteria (definition of done)](#8-acceptance-criteria-definition-of-done)
9. [Decisions and inputs needed from you](#9-decisions-and-inputs-needed-from-you)
10. [Risks](#10-risks)

---

## 1. Scope

| In P3 | Later phase |
|---|---|
| **Equity cash:** CNC (delivery, long only) and MIS (intraday, long and short, auto square-off) on NSE | Futures and options simulation, margins, expiry, rolls (**P6a**) |
| Daily, swing and intraday strategies. Signal timeframes 1m…1h, D, W. Execution on 1m or D. | Live signals, deployments, paper trading (**P4**) |
| Multi-timeframe features aligned on `available_at`; the feature library (WMA, HMA, EMA, Supertrend, ADX, RSI, ATR, VWAP, CPR, ORB, RS, breadth, delivery %, …) | PIT fundamentals, rebalance simulator for Track L portfolios (**P5**) |
| Index series (NIFTY, INDIAVIX, sector indices) and P1/P2 data as **inputs** (regime, events, disclosures, breadth, delivery %, participant OI) | WebSocket / tick data |
| Event-study tool (abnormal returns around P2 events) | |
| Sweeps, walk-forward, deflated Sharpe, PBO, Monte Carlo, regime splits | |
| Process-pool runner, run registry, Backtests UI, API, CLI | |

**Target experiments** (from `TWO_TRACK_SIGNALS_DESIGN.md` §8) that P3 must be able to run end to end. Each one is an acceptance test with real data on your machine:
- **E1:** Nifty 200-DMA allocation.
- **E9:** MTF trend pullback.
- **E11:** RSI(2) mean reversion.
- **E14:** 15m ORB with CPR width.
- **E10:** breakout with a delivery-% filter (needs the P1 bhavcopy).
- **E12:** post-results drift, as an event study plus a strategy (needs P2 events).
- **E16:** event-risk rule (needs P2 events).

E2–E4 need portfolio rebalancing (P5), but the same engine will run them through a target-weights adapter.

---

## 2. Starting point: what P3 reuses

| Existing piece | Where | Role in P3 |
|---|---|---|
| `available_at` rules: D = close + 15 min, intraday = `ts + L` (+ lag) | `app/lab/pit.py` | Core of the DataPortal. Extended to every series and to MTF joins. |
| Session-anchored roll-up 1m → 3m…1h | `app/core/rollup.py::rollup_frame` | The **same** function derives every intraday timeframe inside workers, so a backtest and a chart can never disagree. |
| Session ledger (`series_sessions`: complete / short / missing / confirmed) | `app/core/integrity` | Completeness gate and quality mask (`strict` / `skip_day` / `warn`) |
| Snapshots v1 (immutable Parquet + checksummed manifest) | `app/core/snapshots.py` | Upgraded to v2: content-addressed partitions plus a reference-data bundle |
| Corporate-action factors, raw / split / total views | `app/core/refdata/corporate.py` (`factors`, `multipliers`, `apply`) | Adjusted view for signals, raw view for fills (see §4.2) |
| PIT index membership, F&O eligibility, ban list, lot sizes | `app/core/refdata/universe.py` | `Universe.index("NIFTY 200", pit=True)`; survivorship warnings |
| Calendar (holidays, special sessions, derived from data) | `app/core/calendar.py` + P1 derivation | Trading-day maths, session open/close, MIS cut-off, Muhurat sessions |
| Equity bhavcopy (delivery %), participant OI, breadth series | refdata `cm_eod`, `participant_oi`, `ref_series` | Features with `available_at` = that evening → usable next session |
| Event calendar, disclosures, pre-open, flows (all PIT) | `app/core/intel` (P2) | Event-study input, event filters (E16), strategy features (promoter cluster, pledge flag) |
| ATR, SMA, WMA, beta, percentile | `app/lab/indicators.py` | Seed of the feature library. Moved into `app/lab/features`; the scanner keeps working. |
| Permutation and bootstrap statistics, report writer | `app/lab/experiments/e17.py` | Re-used by the evaluation and event-study modules |
| Jobs table, `BackgroundRunner`, progress, restart recovery | `app/core/jobs` | Job kinds `backtest`, `sweep`, `walk_forward`, `event_study`, run by a **separate** process pool |
| Scanner features (RVOL, gap, opening range, RS) | `app/lab/scanner.py` | Become library features. The scanner and strategies then share one implementation. |

---

## 3. Architecture and module layout

![P3 architecture](diagrams/p3_architecture.png)

```
app/lab/
  data/
    partitions.py     Snapshot v2: content-addressed (instrument, interval, year) Parquet partitions + GC
    bundle.py         reference-data bundle frozen with a snapshot (calendar, CA factors, membership, lots, ban,
                      delivery %, events, costs) - workers need nothing else
    portal.py         DataPortal: bars(tf, view=adj|raw), align(), forming(), universe(), ref(), events(), quality mask
    mtf.py            available_at per timeframe, merge_asof on decision time, warm-up planning
  features/           pure causal indicators: ma.py (SMA/EMA/WMA/HMA/VWMA), trend.py (ADX, Supertrend, MACD,
                      Donchian, regression slope, MTF trend score), momentum.py, volatility.py, volume.py,
                      structure.py (VWAP, CPR, pivots, ORB, gaps, 52w), relative.py (RS, beta, ranks), registry.py
  strategy.py         Strategy base class, parameter schema, helpers (enter_long/short, exits, sizing)
  strategies/         built-in examples = reference strategies used by the tests (buy_hold, ma_cross, rsi2, orb_cpr,
                      mtf_pullback, results_drift, event_risk_filter)
  sim/
    engine.py         hybrid engine: vectorised signals -> event loop over "interesting" timestamps
    orders.py         order types, lifecycle, OCO brackets
    fills.py          fill rules on 1m/D bars: next-open, stop/limit, gap-through, ambiguity, participation cap
    portfolio.py      cash, positions, MTM, margin (MIS leverage), capital constraint
    accounting.py     trades, equity curve, costs per fill, invariants
  market/
    costs.py          versioned India cost schedule (effective_from), per product; contract-note reproduction
    slippage.py       bps/ticks model by liquidity bucket, open/close widening, 1x/2x/3x sensitivity
    rules.py          MIS square-off, CNC long-only, circuit-lock detection, tick/lot rounding, ban list
  eval/
    metrics.py        returns, risk, ratios, trade stats, benchmark, costs/capacity
    validation.py     sweeps (grid/random), walk-forward (anchored/rolling, purge + embargo), DSR, PBO (CSCV)
    montecarlo.py     trade bootstrap, randomised entries with the same exits
    regimes.py        splits: VIX regime, Nifty 200-DMA, expiry days, pre/post Nov-2024, COVID, events
    report.py         report.json + report.md + charts data
  events/study.py     event studies: market-model abnormal returns, CAR windows, tests, clustering control
  runner.py           ComputeRunner: ProcessPoolExecutor (spawn), progress, cancellation, restart recovery
  runs.py             run registry (meta tables) + artefacts under instance/lab/runs/<run_id>/
app/web/backtest_api.py   /api/v2/lab/backtests|runs|sweeps|event-studies|strategies|readiness
templates_v2/backtests.html + static/js/v2/pages/backtests.js   Backtests page (+ run detail, sweep view)
cli: flask historify bt run | sweep | walk-forward | event-study | readiness | rerun | strategies | gc
```

**Process model.**
- Workers never open `bars.duckdb`. They read snapshot partitions (plain Parquet) and the frozen reference bundle.
- The app process only queues work, tracks progress and serves results.
- A running sweep therefore can't block downloads or maintenance, and maintenance can't corrupt a running backtest.

---

## 4. Key design decisions

### 4.1 One time rule: decisions happen at `available_at`, fills happen later
Every row in every frame carries `ts` (bar open) and `available_at` (when it became known):

| Series | `available_at` |
|---|---|
| Intraday bar of length L | `ts + L` (+ `latency_s`: 0 in research, e.g. 60 s in live-parity mode) |
| Daily bar | session close + 15 min |
| W / M bar | close of the last trading day of the period |
| Bhavcopy fields (delivery %, etc.) | 20:00 IST of the trade date |
| Events, disclosures | their own `available_at` / `broadcast_at` (P2) |

Rules:
- A signal computed from a bar has `decision_time = available_at`.
- An order becomes active at `decision_time + order_latency`.
- The order fills on the first execution bar that opens at or after that moment, priced at that bar's open plus slippage.
- Higher-timeframe values reach a lower timeframe only through `merge_asof(decision_time ≥ available_at)`.

There is no other path between timeframes, so the look-ahead test (§6.2) checks one mechanism, not every strategy.

### 4.2 Adjusted vs raw prices without recomputing history at every bar
P1 computes point-in-time adjustment factors: as of day *t*, only corporate actions with `ex_date ≤ t` apply. Recomputing every feature as of every bar would cost O(n²). P3 uses a scale identity instead:

- **The identity:** for any *t*, the fully adjusted series up to *t* equals the point-in-time adjusted series × **C(t)**. C(t) is the product of the factors of actions after *t*, and it is constant over all bars ≤ *t*.
- **Scale-free features are computed once on the fully adjusted series.** Returns, RSI, ATR %, MA crosses and z-scores are identical in both views.
- **Price-level outputs are divided by C(t).** Stops, targets and absolute price filters are converted to the raw price at decision time. The DataPortal ships C(t) as a column (`adj_factor`).
- **Fills, P&L, ticks, circuit bands and "price > ₹100" filters always use raw prices.**
- **Volumes scale by 1 / C(t).**

A property test checks the identity on random dates against a brute-force recomputation (§6.2).

### 4.3 Hybrid engine: vectorised signals, event-driven execution
- **Stage 1** computes features and entry/exit signals vectorised per instrument. A WMA over 10 years of 1m data takes milliseconds.
- **Stage 2** walks only the timestamps where something can happen: a signal, an open position's bar, or a scheduled event such as MIS square-off. Fills and stops use 1m bars.
- **Why not a bar-by-bar loop:** iterating every 1m bar of 200 symbols in Python is too slow.
- **Correctness check:** a deliberately naive **reference simulator**, which loops over every bar, lives in the test suite. The two engines must agree on random strategies (§6.3).

### 4.4 Indian execution model (defaults; all configurable and recorded in the run)
| Rule | Default |
|---|---|
| Signal on bar *t* | fill at the next execution bar's open + slippage |
| Daily "trade at close" | decide on 15:25 data, fill at the 15:25–15:29 VWAP. Never "decide on the close and fill at the close". |
| Stops / targets | checked on 1m high/low. **Gap-through** fills at the open. If stop and target both hit in the same bar, the stop is filled first; the number of such trades is reported. |
| Limit orders | fill only when price trades *through* the limit |
| Circuit lock | a 1m bar flat at a band (±2/5/10/20 % from the previous close) → no fill on the locked side |
| Zero-volume bar | no fill |
| Participation cap | ≤ 5 % of bar volume; the remainder carries over or is cancelled (configurable) |
| MIS | square-off 15:15 (configurable); leverage 5× (configurable); shorts allowed intraday only |
| CNC | long only; no overnight short |
| F&O ban list | no new positions in banned stocks when they are used as cash-equity filters (the rule exists for futures; kept as an optional filter) |
| Rounding | quantity in whole shares (lots from refdata for later phases); prices to ₹0.05 (tick table by date) |
| Costs | versioned schedule (brokerage plan, STT, exchange, SEBI, stamp, GST, DP), keyed by `effective_from` |
| Slippage | `max(k ticks, bps × price)` by liquidity bucket (turnover tercile), ×1.5 in the first and last 15 minutes; every report also shows results at 2× and 3× |

### 4.5 Reproducibility as a contract
A run is fully defined by these hashes, which are stored together:

| Input | Identified by |
|---|---|
| Strategy source | `code_sha` |
| Parameters | `params` |
| Data | `snapshot_id` + `manifest_sha` (partition ids + reference bundle version) |
| Execution | `execution` config |
| Costs and slippage | `cost_schedule` version, `slippage_model` version |
| Software | app version |
| Random number generation | `seed` |

- **Re-running** produces byte-identical trades, equity and metrics. This holds across worker counts and across machines.
- **"Quick run" on latest data** builds or reuses an auto snapshot. Thanks to content addressing, that costs only the partitions that changed, typically the current month.

### 4.6 Strategy API (user code stays small and declarative)
```python
class MtfTrendPullback(Strategy):                       # E9
    name = "mtf_pullback"
    universe = Universe.index("NIFTY 200", pit=True)
    timeframes = {"regime": "D", "trend": "1h", "signal": "15m", "exec": "1m"}
    params = P(fast=Int(20, 5, 60), slow=Int(50, 20, 200), atr_mult=Float(1.5, 0.5, 4.0), risk_pct=Float(0.5, 0.1, 2))
    product = "MIS"

    def features(self, d):                                # d = DataPortal view; joins only on available_at
        reg, trend, sig = d.index("NIFTY", "D"), d.bars("1h"), d.bars("15m")
        return dict(
            regime_ok=d.align(reg.close > F.sma(reg.close, 200), to="signal"),
            trend_up=d.align((F.wma(trend.close, self.p.fast) > F.wma(trend.close, self.p.slow))
                             & (F.adx(trend, 14) > 20), to="signal"),
            pullback=F.cross_up(F.rsi(sig.close, 14), 40) & (sig.low <= F.ema(sig.close, 20)),
            atr=F.atr(sig, 14))

    def signals(self, f):
        return self.enter_long(f.regime_ok & f.trend_up & f.pullback,
                               stop=self.atr_stop(f.atr, self.p.atr_mult), size=self.risk_size(self.p.risk_pct),
                               exit_at="15:10")
```
- **Warm-up:** `features` declares its own warm-up through the library. Each indicator knows its warm-up, and the portal loads `start − warmup` automatically.
- **Parameter schemas:** these drive validation, sweeps and the UI form.
- **Path-dependent logic:** optional `on_fill` / `on_bar` hooks cover it, such as trailing stops and scaling.
- **Where strategies live:** `app/lab/strategies/` (built-ins, also the test oracles) and `instance/strategies/` (yours).
- **Which code runs:** `code_sha` is the hash of the source file, so results always name the exact code.

---

## 5. Work packages: implementation

Each work package ends with green tests and something you can run.

### WP1 · Snapshot v2 + reference bundle + DataPortal (5 d)
| Item | Implementation notes |
|---|---|
| Partitions | One Parquet file per (instrument, interval ∈ {1m, D}, year), stored under `instance/lab/partitions/<sha256>.parquet` and identified by the hash of its canonical bytes. An unchanged year is reused by every later snapshot. |
| Manifest v2 | Partition ids, reference bundle id, calendar version, `intraday_source`, quality per session. Stays verifiable with `python -m app.core.snapshots verify`; v1 snapshots stay readable. |
| Reference bundle | Frozen Parquet/JSON:<br>• calendar rows;<br>• corporate-action factors (split + total);<br>• PIT index membership;<br>• F&O eligibility, lots, ban list;<br>• `cm_eod` delivery fields for the universe;<br>• `ref_series` (breadth, flows, risk-free);<br>• P2 events / disclosures needed by the run;<br>• cost schedule. |
| GC | Delete a partition when no snapshot references it and it is older than N days (`bt gc`, also in weekly maintenance). |
| DataPortal | `bars(sym, tf, view="adj"\|"raw")`: 1m and D from partitions; 3m…1h via `rollup_frame`; W/M via `resample`.<br>`align(frame, to=tf)`<br>`forming("D")` (day-so-far from 1m)<br>`universe(on)`<br>`ref(name)`<br>`events(kinds, as_of)`<br>`quality(sym, tf)`<br>`adj_factor`<br>`LivePortal` (same interface on the in-process BarStore) prepared for P4. |
| Completeness gate | Per-run policy `strict` (default), `skip_day` or `warn`, taken from the ledger statuses. Mixed provenance (broker vs rolled up) is refused unless allowed. |
| Data readiness | `bt readiness --universe "NIFTY 200" --tf D,1m --from 2016-01-01`: coverage matrix, incomplete sessions, CA events, membership exactness, missing reference data. One click queues the downloads. |

### WP2 · Feature library (5 d)
- **Categories:**
  - **Moving averages:** SMA, EMA (SMA seed, documented), **WMA**, HMA, VWMA.
  - **Trend:** ADX/DMI, Supertrend, MACD, Donchian, Parabolic SAR, regression slope and t-stat, MA stack, **MTF trend score**.
  - **Momentum:** RSI (Wilder), Stochastic, ROC, CCI, Williams %R.
  - **Volatility:** TR, ATR (Wilder), ATR %, Bollinger + squeeze, Keltner, close-to-close and Parkinson volatility.
  - **Volume:** OBV, volume ratio, RVOL by time of day, **delivery % and its z-score**.
  - **Structure:** session VWAP, CPR + width, classic and Camarilla pivots, previous-day H/L/C, ORB 5/15/30, gap vs official close, 52-week distance.
  - **Relative:** RS vs index, Mansfield RS, rolling beta, cross-sectional rank.
  - **Breadth:** from `ref_series`.
- **Contract for every feature:**
  - pure function over arrays/Series;
  - declared `warmup(params)` (NaN before it);
  - causal;
  - session-aware where relevant (VWAP, ORB and CPR key on the calendar trading date);
  - `inputs` metadata (adjusted or raw);
  - registered in `registry.py`, so strategies, sweeps and the UI can list it.
- **Incremental twins** for the indicators P4 will update live (EMA, WMA, ATR, RSI, VWAP, Supertrend), with a parity guarantee.
- **Scanner migration:** the scanner switches to the library functions, and its existing tests guard the change.

### WP3 · Event-study tool (3 d)
- **Input:** any event set with `available_at`. Sources: P2 `market_events` (results, ex-dates, RBI…), disclosures (promoter clusters, pledge flags, bulk deals), feeds kinds (order wins, ratings) or a CSV.
- **Event time:** the first tradable bar after `available_at`. An event after 15:30 maps to the next session open, and one during market hours to the next 1m bar when running intraday. This prevents the classic "announced at 17:00, traded at that day's close" leak.
- **Returns:**
  - market-model abnormal returns, with beta estimated on a [−250, −30] window;
  - NIFTY or the sector index as the market;
  - CAR windows, default [−5, +20] days and intraday [0, +60] minutes;
  - mean/median CAR, t-test, a **sign test**, a bootstrap CI;
  - results by year, by regime and by event subtype.
- **Controls:**
  - overlapping events of the same stock are clustered (the first event wins, the rest are reported);
  - a placebo of random dates with the same distribution of stocks and months;
  - the minimum sample size (E12: < 100 events → inconclusive) is enforced in the verdict.
- **Output:** a report (JSON + Markdown + chart data) and the UI tab. E12 and E16 are the first real uses.

### WP4 · Simulator + India execution model (7 d)
| Item | Notes |
|---|---|
| Orders | market, limit, stop, stop-limit, bracket (entry + stop + target, OCO), time exit, `exit_at` (e.g. 15:10). Quantities from sizing helpers: fixed ₹, % equity, risk-based (₹ risk / stop distance). |
| Engine loop | A heap of event timestamps (signals, scheduled exits, square-off, day boundaries). A position is observed only on its own instrument's 1m bars. Daily MTM at the official close. |
| Fills, rules, costs, slippage | §4.4. Each fill records the rule that applied (`gap_through`, `locked`, `participation`, `ambiguous`…), so the report can explain every trade. |
| Portfolio | Cash, positions, exposure and MIS margin (leverage). Orders beyond capital or margin are rejected and logged. Optional maximum positions and per-trade risk cap. |
| Accounting | Trades (entry/exit, MAE/MFE, R multiple, costs split by component), daily and intraday equity, and a ledger with checksums. |
| Artefacts | `instance/lab/runs/<run_id>/{config.json, orders, fills, trades, positions, equity}.parquet`, `report.json`, `report.md` |

### WP5 · ComputeRunner + run registry (2 d)
- **Job kinds** `backtest`, `sweep`, `walk_forward` and `event_study` on a `ProcessPoolExecutor(mp_context=spawn)` with N = cores − 1 (configurable). This pool is separate from `BackgroundRunner`, so exports and maintenance are never blocked.
- **Progress and cancellation:**
  - workers report progress through short SQLite transactions;
  - they check a cancel flag between instruments / combinations;
  - a stale `running` job is re-queued on restart.
- **Alembic `0007_backtests`:** tables `strategies`, `backtest_runs`, `run_groups` (including the number of combinations tried, for DSR), `lab_partitions` (reference counts for GC) and `cost_schedules` (versioned JSON).
- **Memory guard:** data is loaded per partition, with a panel-size estimate before the start. The run is refused with a clear message if the estimate exceeds the configured cap.

### WP6 · Evaluation (3 d)
- **Metrics** per run, year, regime and instrument:
  - returns: CAGR, total return;
  - risk: volatility, max drawdown (depth and duration), Ulcer index;
  - ratios: Sharpe (risk-free from `ref_series`, default 6.5 % if missing, flagged), Sortino, Calmar;
  - trades: profit factor, expectancy (R), win rate, holding time, MAE/MFE;
  - costs and capacity: turnover, cost drag, participation;
  - benchmark: NIFTY 50 TRI, or the price index with a warning when TRI is missing.
- **Sweeps:** grid or random (seeded), streamed into a run group.
- **Walk-forward:** anchored or rolling windows with a **purge and embargo** between train and test. Only stitched out-of-sample results are the headline.
- **Overfitting statistics:**
  - **deflated Sharpe ratio**, using the number of trials in the group and the Sharpe skew/kurtosis;
  - **PBO** via CSCV;
  - a parameter-stability heatmap and plateau check.
- **Monte Carlo:** trade bootstrap (drawdown distribution) and randomised entries with the same exits.
- **Hold-out lock:** a date range (e.g. the last 12 months) that runs refuse to touch until the strategy version is "frozen" (`bt freeze`). The unlock is logged.
- **Regime splits:** VIX regime (existing service), NIFTY vs 200 DMA, expiry vs non-expiry days (P2 events), pre/post 20 Nov 2024 F&O rules, COVID window, event days vs others.

### WP7 · Backtests UI, API, CLI (3 d)
- **Backtests page:**
  - **run form:** strategy, parameters from the schema, universe, dates, execution/cost presets, snapshot or quick run; it shows a readiness check before submitting;
  - **run list:** compare two runs.
- **Run detail:**
  - metrics cards; equity + drawdown (Lightweight Charts, already bundled); monthly heatmap;
  - trades table with filters, and a chart with trade markers (reuses the Charts page);
  - regime, slippage-sensitivity and data-quality tabs;
  - the "Re-run and verify identical" button.
- **Sweep / walk-forward view:** heatmap, stitched OOS equity, DSR / PBO verdict.
- **Event-study view:** CAR chart with CI bands, tables by subtype and year.
- **API:** `/api/v2/lab/...` as in HLD §13.2, with the same error envelope and 202 + job id model.
- **CLI:** `flask historify bt …`.

---

## 6. How we test P3

A backtester fails **silently**: wrong numbers look just like right numbers. So the strategy does not rest on example checks; it is built around **independent oracles**, where each answer is computed in a second, simpler way or known analytically.

![P3 test strategy](diagrams/p3_test_strategy.png)

### 6.1 Test layers
| Layer | What | Oracle | Runs |
|---|---|---|---|
| **1 · Unit** | Each feature, fill rule, cost line, metric, calendar helper | Hand-computed fixtures; a **naive loop implementation** in the test for every vectorised indicator | every commit |
| **2 · Property (hypothesis)** | Invariants for random data, parameters and cut-offs (§6.2) | The invariant itself | every commit |
| **3 · Golden "toy markets"** | Tiny synthetic markets (3–20 bars) with an exact known answer for each execution rule (§6.4) | Worked by hand, documented in the fixture | every commit |
| **4 · Differential** | The hybrid engine vs the **naive reference simulator** on random strategies and data (§6.3) | A second implementation | every commit (fast subset), nightly (large) |
| **5 · Known-answer strategies** | Buy & hold, always flat, coin-flip entries, a perfect-foresight "cheater" (§6.5) | Analytic results | every commit |
| **6 · Integration** | Services → snapshot v2 → ComputeRunner (real spawn processes) → artefacts → API/CLI; cancel / restart / re-run; isolation | End-to-end behaviour; checksums | every commit (≈ 2 min) |
| **7 · Browser E2E** | Backtests page: run form, live progress, run detail, trade markers, sweep heatmap; no console/CSP errors | Playwright | every commit |
| **8 · Performance** | Budgets (HLD §10.1): 200 symbols × 10 years D < 15 s; 200 × 1 year of 15m with 1m execution < 90 s; UI p95 < 300 ms during a sweep | `benchmarks/p3_backtest.py` with thresholds | on demand + before release |
| **9 · Real-data acceptance** | E1, E9, E11, E14 (+ E10, E12, E16) on your data | Sanity bounds (§6.7), reproducibility, human review | on your machine |
| **Regression** | The whole existing suite (446 + 39 browser) stays green; scanner results unchanged after the feature-library migration | Existing tests | every commit |

### 6.2 Property tests: the look-ahead guard and friends
1. **Corrupt the future** (the main look-ahead test). This is already used for the P1 scanner and the P2 intel.
   - For random data and a random cut-off *t*: run, then scramble every bar and reference row with `available_at > t`, then run again.
   - All features, signals, orders and fills with decision time ≤ *t* must be identical.
   - It runs for **every registered feature** (automatically, by iterating the registry) and **every built-in strategy**. A new indicator that peeks fails CI without anyone writing a new test.
2. **MTF alignment.**
   - A higher-timeframe value is never visible before its `available_at`.
   - A 1h value appears at `ts + 1h`; D appears after close + lag.
   - The partial last hour (15:15–15:30), special sessions and Muhurat are handled.
3. **Adjustment identity (§4.2).**
   - For random series with random splits/bonuses, features computed on the PIT-adjusted series at *t* equal the full-adjusted features (scale-free) or the full-adjusted values / C(t) (price levels).
   - Position value is continuous across a split.
4. **Batch ↔ incremental parity.** The incremental twin equals the batch value to 1e-9 on random streams.
5. **Accounting invariants on every simulated run:**
   - cash + Σ positions × raw price = equity at every MTM point;
   - Σ trade P&L − Σ costs = equity change;
   - every fill price lies within its bar's [low, high];
   - no fill on a zero-volume or locked bar;
   - no CNC short, and no MIS position after the cut-off;
   - quantities are whole lots;
   - cash ≥ 0 unless leverage is configured.
6. **Determinism:**
   - same inputs, byte-identical artefacts;
   - 1 worker vs 4 workers give identical results;
   - a snapshot rebuilt from the same data has the same partition ids.
7. **Feature hygiene (all via the registry):**
   - values before warm-up are NaN;
   - no feature forward-fills across sessions unless it declares that;
   - NaN inputs never produce signals.

### 6.3 Differential testing against a reference simulator
- **The reference simulator:** about 200 lines in `tests/lab/reference_sim.py`. It steps through **every** 1m bar of every instrument and applies the same documented rules in the most literal way: slow, but obviously correct.
- **The test:** hypothesis generates random markets (including gaps, locked bars, zero-volume bars and splits) and random rule-based strategies. The hybrid engine and the reference must produce the **same fills, trades and equity**.
- **Why it matters:** any optimisation in the hybrid engine (skipping bars, vectorised stop checks) that changes results is caught here.

### 6.4 Golden toy markets (execution and costs)
A fixture file of 25–30 named scenarios, each with hand-worked expected fills, P&L and costs. Examples:

| Scenario | Expected |
|---|---|
| Signal at the 10:00 15m close | fill at the 10:15 1m open, never 10:00 |
| Stop 98, next bar opens 95 (gap-through) | fill at 95 |
| Stop and target inside one 1m bar | stop first; `ambiguous = 1` |
| Upper-circuit locked bar (h = l = +5 %) | buy not filled; sell filled |
| MIS position open at 15:15 | squared off at the 15:15 bar open; MIS costs |
| CNC sell with no position | rejected |
| Split 1:5 while holding 100 shares at ₹1000 | 500 shares at ₹200; equity unchanged |
| Daily strategy deciding "at the close" | fill at the 15:25–15:29 VWAP |
| Order > 5 % of bar volume | partial fill, remainder carried |
| D strategy on a Muhurat / special session day | uses the calendar session times |
| Decision after 15:30 on a results event | first fill at the next session open |

**Cost tests:**
- published-rate worked examples per product: delivery, intraday, and (prepared for P6a) futures and options;
- **reproduce one of your Angel One contract notes to the paisa** (§9);
- STT / stamp rate changes at their `effective_from` dates (1 Oct 2024).

### 6.5 Known-answer strategies
| Strategy | Must equal |
|---|---|
| Buy & hold NIFTYBEES (CNC) from day 1 | benchmark return − entry/exit costs, exactly |
| Always flat | zero trades, equity constant, metrics well defined (no division errors) |
| Coin-flip entries with fixed holding (1000 seeded runs) | mean net return ≈ −cost per trade (statistical test); gross ≈ 0 |
| "Cheater" that reads tomorrow's close through an illegal path | **must be impossible**: the portal raises, and corrupt-the-future fails if any path leaks |
| Known synthetic edge injected into random prices | recovered by the backtest and the event study, with the correct sign and magnitude (within CI) |
| Deflated Sharpe | matches the worked example in Bailey & López de Prado (2014) |
| PBO on pure-noise strategies | ≈ 0.5 |

### 6.6 Integration and isolation tests
- Run a backtest through the API. The job reaches `succeeded`, the artefacts exist, and the metrics in the DB equal `report.json`.
- **Cancel** mid-run; **restart** the app with a running job (it is re-queued); **re-run** and get an identical checksum.
- **Isolation:** workers must not open `bars.duckdb`. The test enforces this by making the file unreadable to the worker. Downloads keep running during a sweep.
- **Hold-out lock:** a run touching the locked range is refused until `freeze`.
- **Readiness:** a universe with missing sessions fails `strict`, passes `warn` with the sessions listed, and the "queue repair" action creates download jobs.
- **Scanner regression:** after the feature-library migration, scanner results on the existing fixtures are unchanged.

### 6.7 Real-data acceptance on your machine
- **Run:** E1, E9, E11 and E14 after the P1 backfill; then E10, E12 and E16.
- **Every run must be:** reproducible (identical re-run); inside **sanity bounds**, meaning no trade with a return beyond the day's high/low range, cost drag consistent with turnover, and a buy-and-hold baseline equal to the index TRI within costs; and reviewed by you on a sample of 20 trades using the trade-marker chart.
- **Results** are recorded in `P3_ACCEPTANCE_REPORT.md`, including the failures. A strategy that fails its rejection criterion (TWO_TRACK §8) is a valid outcome, not a defect.

---

## 7. Sequencing, milestones and effort

![P3 plan](diagrams/p3_plan.png)

| Week | Work | Milestone |
|---|---|---|
| 1 | WP1 Snapshot v2 + bundle + DataPortal; readiness CLI; start the **reference simulator** and toy-market fixtures (tests first) | `bt readiness` on your data |
| 2 | WP2 feature library + registry-driven property tests; scanner migration | all features pass corrupt-the-future |
| 3 | WP4 simulator core (market/stop/limit, CNC/MIS, costs, slippage) + the differential test | **M3a: first honest backtest** from the CLI (E1, E11) |
| 4 | WP4 remainder (brackets, circuit locks, participation, MTF execution) + WP5 runner + run registry | E9, E14 runnable as jobs |
| 5 | WP6 evaluation (sweeps, walk-forward, DSR, PBO, Monte Carlo, regimes) + WP3 event study | **M3b: E12 event study + E16** |
| 6 | WP7 UI/API, performance benchmark, docs, self-review and gap fixes, real-data acceptance | **M3: P3 done** |

| Work package | Effort |
|---|---|
| WP1 Snapshot v2 + DataPortal | 5 d |
| WP2 Feature library | 5 d |
| WP3 Event-study tool | 3 d |
| WP4 Simulator + India execution model | 7 d |
| WP5 ComputeRunner + registry | 2 d |
| WP6 Evaluation | 3 d |
| WP7 UI / API / CLI | 3 d |
| **Total** | **28 d** (the reference simulator, fixtures and property harness are included in each package; self-review + fixes is ≈ 10 % inside the estimate) |

**Working practice** (same as P1/P2):
- Tests are written with each package; the reference simulator and toy markets come **before** the engine.
- The whole suite is green at the end of each package.
- A self-review pass closes each package, with gaps fixed and listed in the report.
- Each milestone is usable from the CLI before the UI exists.

---

## 8. Acceptance criteria (definition of done)
- [x] Workers read only snapshot v2 partitions and the reference bundle. An unchanged year is never copied twice, and the GC removes unreferenced partitions.
- [x] Every registered feature and every built-in strategy passes corrupt-the-future, MTF alignment, the adjustment identity and warm-up properties.
- [x] The hybrid engine equals the reference simulator on random markets and strategies.
- [ ] All toy-market scenarios pass ✅, and one real contract note is reproduced to the paisa *(waiting for your contract note; published-rate worked examples pass)*.
- [x] Known-answer strategies pass (buy & hold = benchmark − costs, coin-flip ≈ −costs, DSR worked example, PBO ≈ 0.5 on noise).
- [x] Re-runs are byte-identical, including across worker counts. Cancel and restart work, and the hold-out lock is enforced.
- [x] Performance budgets are met on the benchmark, and the UI stays responsive during a sweep.
- [x] The Backtests UI, API and CLI are complete, with browser tests green; docs (`docs/backtesting.md`) and the report are written.
- [ ] E1, E9, E11 and E14 run end to end on your data, with results recorded (pass or reject). *(All four run end to end on synthetic data in the test suite; the real-data runs are pending on your machine.)*

---

## 9. Decisions and inputs needed from you
| # | Decision / input | Recommendation |
|---|---|---|
| D1 | First strategy families to support end to end | Equity swing on D and intraday 15m with 1m execution on NIFTY 200 / F&O stocks (E1, E9, E11, E14) |
| D2 | Where your strategies live | `instance/strategies/` (git-versionable); built-ins in the app |
| D3 | **A recent Angel One contract note** (delivery + intraday; amounts can be blurred) | Needed for the "to the paisa" cost test; until then, published rates (HLD App. B) |
| D4 | Brokerage plan, MIS leverage and square-off time on your account | Defaults: ₹20 / order or 0.1 % (lower), 5×, 15:15 |
| D5 | Benchmark | NIFTY 50 TRI (imported as a `ref_series` CSV from niftyindices), price index as fallback |
| D6 | Hold-out period | The last 12 months, locked until a strategy version is frozen |
| D7 | Default fill assumptions | Next-bar open; stop-first on ambiguity; 5 % participation; 2×/3× slippage always reported |
| D8 | Worker processes / memory cap | cores − 1, 4 GB cap (configurable) |
| D9 | Depth of 1m history to download for the universe | ≥ 2 years for E14 / E9 (check how far Angel One serves 1m: the `lab prepare` command already queues it) |

---

## 10. Risks
| Risk | Impact | Mitigation |
|---|---|---|
| Silent look-ahead through a new feature or join | Fake edge | One join mechanism (§4.1); registry-driven corrupt-the-future on every feature and strategy; portal API without raw "future" access |
| Engine optimisation changes results | Wrong P&L | Differential test against the reference simulator |
| Costs / slippage underestimated | Intraday edges vanish live | Versioned schedule checked against a contract note; 2×/3× sensitivity in every report; live-parity latency mode |
| Survivorship (incomplete index history before the first P1 snapshot) | Optimistic long-only results | `point_in_time_exact` flag surfaced in readiness and the report; import historical constituent changes (P1 CSV import) |
| Corporate-action errors | Fake gaps, wrong stops | Adjustment identity test; P1 verification against the bhavcopy; runs list the CA events they crossed |
| Short 1m history from the broker | E14 / E9 samples too small | Readiness shows sample sizes; the minimum-trades rule in verdicts |
| Overfitting by many sweeps | False discoveries | Trial counting per group, DSR, PBO, the hold-out lock, walk-forward headline |
| Memory / time on large panels | Crashes, slow UI | Per-partition loading, size estimate before the start, process isolation, performance budgets in CI |
