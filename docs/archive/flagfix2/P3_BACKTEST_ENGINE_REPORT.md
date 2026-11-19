# P3 · Backtest & Research Engine: Implementation Report

| | |
|---|---|
| **Phase** | P3 of `PROJECT_ROADMAP_6_PHASES.md`. The plan you approved is `P3_BACKTEST_ENGINE_PLAN.md`; it used the recommended answers to decisions D1–D9. |
| **Status** | ✅ Built, self-reviewed and tested in this environment. The real-data acceptance runs (E1, E9, E11, E14) need your downloaded data (§6). |
| **User guide** | `historify-master/docs/backtesting.md` |
| **Code** | `app/lab/{data, features, strategy.py, strategies, sim, market, eval, events, runner.py, backtests.py, worker.py}`, `app/web/backtest_api.py`, the `/backtests` page, the `flask historify bt …` CLI, Alembic `0007_backtests`. About 5,050 lines of engine code, 410 lines of UI and 2,180 lines of test code. |
| **Tests** | **+182 tests**. Full suite: **626 passed, 1 skipped, 9 xfail** (the xfails are pre-existing v1 characterisations). **41 browser tests pass.** Performance budgets are met at full scale (§4). |

---

## 1. What was built (by work package)

| WP | Delivered |
|---|---|
| **WP1 Snapshot v2 + DataPortal** | **Partitions.** One partition per (instrument, interval, year). Its id is the sha256 of its canonical column bytes, so an unchanged year is stored once and shared by every later snapshot (tested: rebuilding after a one-bar edit adds exactly one partition). Partition files are read-only.<br>**Reference bundle.** Everything a run reads is frozen with the snapshot: calendar, split and total corporate-action factors, point-in-time index membership, bhavcopy delivery fields, ban list, reference series, events, insider trades and pledge data.<br>**Manifest.** It has a content hash (same data gives the same hash). There is verify and garbage collection, plus a readiness report with coverage, gaps, missing instruments, membership exactness and queued repairs.<br>**DataPortal.** Every frame is indexed by `available_at`, so timeframes are joined with a single `align()` rule. Intraday timeframes are rolled up from 1m with the app's own `rollup_frame`. It also provides W/M frames, day-so-far ("forming") bars, a clock-schedule frame (`schedule:15:25`), adjusted and raw views with the `adj` multiplier, bhavcopy fields usable the next session, event flags known only once announced, and completeness masks. |
| **WP2 Feature library** | **37 registered indicators**, for example SMA, EMA, **WMA**, HMA, VWMA, ATR, RSI, ADX, **Supertrend**, MACD, Bollinger, **VWAP**, **opening range**, **CPR**, Camarilla, Donchian, PSAR, trend score and 52-week distance. Cross-sectional helpers (RS vs index, Mansfield RS, rolling beta, rank, gap vs official close) are in the library but not in the registry. **Incremental twins** for live updates exist for EMA, WMA, ATR, RSI, VWAP and Supertrend. The scanner now uses the same ATR/SMA/WMA code. |
| **WP3 Event studies** | Day 0 is the first session after the event became known, and scheduled events use the event date. It computes market-model abnormal returns, reaction CAR and tradable return, t-test, sign test, bootstrap CI, results by year, and a placebo on days outside any event window. Overlapping events are dropped, and the verdict is "inconclusive" below `min_events`. |
| **WP4 Simulator + Indian execution model** | The hybrid engine searches for fills and exits with vectorised numpy and orders them across instruments with an event heap. It supports market and limit orders, stops and targets, gap-through fills, and stop-first handling of ambiguous bars. Circuit-locked and zero-volume bars never fill, and each fill is capped at a share of bar volume (remaining exit quantity fills over the following bars). MIS positions are squared off at 15:15 under a leverage cap; CNC is long-only. Also covered: time exits, max-hold, signal exits, ticks rounded against the trader, four sizing modes, and a versioned cost schedule (brokerage, STT, exchange, SEBI, stamp, GST, DP, with `effective_from` dates). A split while holding keeps the position's value. The full semantics are written in `sim/types.py`. |
| **WP5 ComputeRunner + registry** | A spawn-based process pool, separate from the background job runner. It supports progress reporting, cancellation (before and during a run), restart recovery, and rebuilding the pool after a worker dies. Alembic `0007` adds `lab_snapshots`, `lab_partitions`, `lab_snapshot_partitions`, `backtest_runs`, `run_groups` (trial counts) and `strategy_versions` (hold-out freezes). A memory guard and input validation protect it. |
| **WP6 Evaluation** | Metrics cover returns, risk, Sharpe (against a risk-free rate and against zero), Sortino, Calmar, Ulcer, trade statistics, cost drag and turnover, and a benchmark comparison (beta, alpha, information ratio), by year and by month.<br>Reports add **1×/2×/3× slippage** results, **regime** splits (VIX, trend, expiry days, event days, Nov-2024 F&O rules, COVID) and **Monte Carlo** (trade bootstrap and random entries).<br>Sweeps (grid or random) report the **deflated Sharpe ratio**, **PBO (CSCV)** and a plateau check. **Walk-forward** supports rolling or anchored windows with a purge gap and reports the stitched out-of-sample result. A **hold-out lock** blocks the last 12 months unless the strategy code hash was frozen. |
| **WP7 UI / API / CLI** | The Backtests page has tabs for runs, run detail (metrics, equity chart, price chart with trade markers, slippage, regimes, Monte Carlo, trades, and a re-run-and-verify button), a new-backtest form (quick run, sweep, walk-forward, event study), sweeps and walk-forward (results table and overfitting verdict), data and snapshots (readiness and creation), and strategies (freeze). There are 21 API endpoints and 9 CLI commands. |
| **Strategies** | Built-in examples map to the experiment list: `buy_hold`, `trend_allocation` (E1), `mtf_pullback` (E9), `breakout_delivery` (E10), `rsi2` (E11), `results_drift` (E12), `orb_cpr` (E14) and `event_risk` (E16). You can add your own in `instance/strategies/`. |

Diagrams and screenshots:
- `diagrams/p3_architecture.png`, `diagrams/p3_test_strategy.png`, `diagrams/p3_plan.png`
- `diagrams/backtests_run.png`, `diagrams/backtests_sweep.png`, `diagrams/backtests_new.png`
- `diagrams/hld_architecture.png` and `diagrams/roadmap_6_phases.png` are updated: P3 is marked built.

---

## 2. How it was tested (plan §6, as implemented)

| Layer | Implemented by | Result |
|---|---|---|
| 1 · Unit (oracle: a naive loop) | `tests/unit/test_bt_features.py`: SMA, EMA, RMA, WMA, HMA, true range, ATR, RSI, VWAP, Donchian, OBV and stochastic are each checked against a hand-written loop, plus worked values (WMA, EMA seed, CPR) | 1e-9 agreement |
| 2 · Property | **Corrupt the future for every registered feature.** The test is driven by the registry, and a deliberately leaky feature was shown to fail it. Also: declared warm-ups are exact, index and length are preserved, incremental twins equal the batch functions (hypothesis), and split adjustment matches P1 | all 37 features |
| 3 · Golden toy markets | `tests/unit/test_bt_execution.py` covers 17 scenarios: fill timing, overnight decisions, gap-through, ambiguous bars, target gap, shorts, circuit lock, zero volume, participation and sliced exits, limit through vs touch, 15:15 square-off and cut-off, time exit and max-hold, CNC vs MIS shorting, end of data, split while holding, sizing and capital, slippage and ticks, and the accounting identity. It also has cost worked examples at published rates, the `effective_from` change and contract-note rounding | all pass |
| 4 · Differential | `tests/unit/test_bt_differential.py` compares the hybrid engine with a **literal bar-by-bar reference simulator** (`tests/lab/reference_sim.py`) on 600 fixed plus 60 random markets, with identical fills, trades, equity and counters. Accounting invariants are checked on every run. A coverage test proves the generator exercises every execution rule. Deliberately introduced engine bugs (gap check on the entry bar, entries ordered before exits) were **caught** | 0 differences |
| 5 · Known answers | Buy and hold equals the instrument return minus costs to the paisa. An always-flat strategy gives zero. Coin-flip entries (8 seeds, more than 200 trades) show no gross edge and pay costs. The DSR worked example from the paper (0.9004) is reproduced. PBO is about 0.5 on noise and below 0.1 with a real edge. An event study recovers an injected +4 % effect, and the placebo does not | all pass |
| 6 · Integration | `tests/integration/test_backtests.py` (24 tests): snapshots (sharing, verify, tamper detection, GC); readiness; the portal (available_at, roll-up parity with the app, partial last hour, W/M, forming bars, next-session bhavcopy, split identity); **corrupt-the-future for all 8 built-in strategies**; event study; API; **re-run gives identical results**; **1 worker equals 2 workers**; quick run; sweep and walk-forward; **restart recovery and isolation** (the bar store is unreadable during the run); cancel before and during a run; hold-out lock, freeze and code edits; completeness policies; MIS on 1m; user strategies; a crashing worker is survived; input validation; path safety; CLI | all pass |
| 7 · Browser | `tests/ui/test_backtests_page.py` (run detail, chart, tables, re-run and verify, form quick run, sweep, readiness, freeze) and `/backtests` in the all-pages CSP test | all pass |
| 8 · Performance | `benchmarks/p3_backtest.py` (§4) | budgets met |
| Regression | full suite | 626 passed, 41 browser |

---

## 3. Self-review: gaps found and fixed

The tests found these problems; each is fixed and now guarded by a test.

| # | Gap | Found by | Fix |
|---|---|---|---|
| 1 | An end-of-data exit was stamped at the last bar's *open*. Pending entries at that open then re-entered, producing one-bar phantom trades. | smoke run | End-of-data fills now happen at the bar's close; the rule is in the spec and the reference simulator |
| 2 | A look-ahead test compared fills *at* the cut-off time. Such a fill legitimately uses the opening price printed at that moment, so the test was wrong, not the engine. | look-ahead test | The rule is now: fills strictly before the cut-off must be unchanged. It is documented. |
| 3 | The event-study placebo drew random days that overlapped real event windows, so it "found" the effect. | injected-effect test | The placebo now only draws days outside every event window |
| 4 | Stability ("plateau") judged negative Sharpe ratios as a plateau. | sweep smoke test | Plateau is undefined when the best objective is ≤ 0, and a note explains why |
| 5 | Walk-forward overwrote each fold's test dates with its metrics. | smoke test | The metrics now go in a separate `test_metrics` field |
| 6 | A 6.5 % risk-free rate makes a mostly-cash strategy look terrible. | review | `sharpe_rf0` is reported next to `sharpe`, with a warning when the default rate is assumed |
| 7 | Readiness relied only on the ledger, so series written without ledger rows looked "missing". | test | Readiness now counts bars in the bar store and reports gaps and late starts; the ledger only supplies incomplete flags |
| 8 | Performance: one DuckDB read per partition, and per-row pandas `iloc` when building execution data. | benchmark and profiler | Series now load in one scan and the previous close is computed vectorised (10 s → 3.3 s on the 40-symbol case). Per-instrument frames are evicted after use to bound memory. |
| 9 | A worker that died (for example out of memory) would leave the pool broken for every later job. | review | The pool is rebuilt and the run fails with a clear message (test: a strategy that calls `os._exit`) |
| 10 | A job cancelled before it started left its run "queued" forever. | review | The runner now reconciles these runs as cancelled |
| 11 | Execution settings were unvalidated (negative capital, participation > 1, bad times). | review | Settings are range- and enum-checked, with HTTP 400 on bad input |
| 12 | The artefact endpoint built a file path from an unchecked run id. | review | The run id is looked up first; unknown runs get 404 and unknown artefacts 400 |
| 13 | The memory guard was skipped for quick runs, because no snapshot exists yet at submission. | review | It is re-checked in the runner after the snapshot is built |
| 14 | A deprecated pandas call (`dayofweek`). | warnings | Replaced with `day_of_week` |
| 16 | The plan promised "compare two runs" in the run list; it was missing. | review against the plan | Tick two runs to get a side-by-side metrics table with differences and a results-hash "identical" check; browser-tested |
| 15 | The plan promised "a chart with trade markers" in the run detail, but the first UI build showed only equity. | review against the plan | Added a **Price & trades** candlestick of any traded instrument (raw prices from the run's snapshot) with entry and exit markers, plus `GET /lab/runs/<id>/chart?key=`; browser-tested |

---

**Deviations from the plan (deliberate):**
- **Module names.** `data/store.py` + `data/snapshot.py` replace `partitions.py` + `bundle.py`, and the MTF logic
  lives in `portal.py`.
- **Cost schedule.** It is versioned in code with a settings override, instead of a `cost_schedules` table. Every run
  records the schedule id and hash.
- **LivePortal.** The in-process live variant of the DataPortal is designed in the P4 HLD rather than built now:
  only P4 uses it.
- **Event-study chart.** It shows the CAAR line; the confidence intervals are in the windows table, not drawn as
  bands.

## 4. Performance (`python benchmarks/p3_backtest.py`)

Measured on this sandbox (4 cores), with synthetic data and a complete backtest (signals, execution and metrics) in one process:

| Scenario | Time | Budget | Headroom |
|---|---|---|---|
| A · 200 symbols × 10 years of D (RSI-2) | **13.2 s** | 15 s | 12 % |
| B · 200 symbols × 1 year of 15m signals, 1m execution (ORB) | **87.9 s** | 90 s | 2 % |
| UI p95 while a 24-combination sweep keeps 3 workers busy (status, runs list, page) | **10 ms** (idle 6.5 ms) | 300 ms | ample |

Scenario B has almost no margin. Possible fixes, if it becomes tight on your machine:
- build instrument inputs in parallel across the process pool;
- use one DuckDB scan for all of a snapshot's 1m partitions;
- trim the rounding in per-fill bookkeeping.

The design allows all three without changing results: the differential and re-run tests guard the outcome.

---

## 5. Known limitations

| Limitation | Impact | Mitigation / later phase |
|---|---|---|
| The cost schedule uses published rates; your contract note has not been reproduced yet. | Small cost errors | Send a contract note; add your brokerage plan under Settings → `backtest.costs` |
| Slippage is modelled; there is no bid/ask history. | Intraday results may be optimistic | Every report shows 2×/3× slippage; spreads can be calibrated from quote snapshots (HLD §10.3) |
| The broker's intraday price basis is assumed to match its daily basis. | A wrong split adjustment on intraday data around ex-dates | P1 detects the daily basis; check one intraday split on your data |
| Circuit bands are detected heuristically (flat bar at ±2/5/10/20 %). | A rare locked bar could be missed | The band history is not published in an easy form |
| Portfolio rebalancing (target weights) is not in P3. | E2–E4 (Track L) are not runnable yet | P5 rebalance simulator |
| F&O products (NRML, lots, margins, expiry) are not in P3. | Options experiments wait | P6a; the engine already carries lot sizes and a product field |
| `instance/lab/` is not part of the weekly backup. | Snapshots can be lost with the disk | They are rebuildable from the bar store; keep important ones |
| Scenario B is at 98 % of its budget. | Large intraday sweeps are slow | §4 options |

---

## 6. What you need to do on your machine

1. Download D for NIFTY 200 from 2016, and at least 2 years of 1m for the F&O stocks (`lab prepare` or the Data page). Then run `flask historify bt readiness --index "NIFTY 200" --intervals D,1m --start 2016-01-01 --end <today> --repair`.
2. Create a snapshot and run the target experiments: E1 (`trend_allocation` on NIFTYBEES), E11 (`rsi2`), E9 (`mtf_pullback`) and E14 (`orb_cpr`). Record pass or reject against the criteria in TWO_TRACK §8.
3. Send one Angel One contract note (delivery and intraday; amounts may be blurred) so the cost test can be matched to the paisa.
4. Keep the last 12 months locked until a strategy version is worth freezing.
