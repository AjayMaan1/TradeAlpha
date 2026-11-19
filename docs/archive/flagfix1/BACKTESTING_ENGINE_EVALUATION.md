# Backtesting and strategy-generation engines for Historify: audit, hands-on evaluation, recommendation

**Questions.**
1. Which backtesting tool (vectorbt, backtesting.py, and the alternatives) integrates best with Historify's trade
   data stores?
2. Can we take the best parts of all of them and build our own backtesting system?

**Method.** I did not rely on documentation. I built a realistic Historify instance with Historify's own code, installed
each engine on Historify's exact pinned stack, ran one precisely specified strategy in every engine and compared each
result with an independent reference implementation. I also prototyped the recommended core. Every number below comes
from a script in [`backtest_evaluation/`](backtest_evaluation/README.md).

---

## 1. Summary

**Answer to question 1: no single off-the-shelf engine should become Historify's backtest core.**
- All of them read the data equally easily: each needs a ~50-line adapter from a snapshot or store query to pandas.
- What separates them is **licence, stack compatibility and execution semantics**. On those criteria, every engine
  fails at least one hard requirement:

| | Best at | Blocking issue for Historify |
|---|---|---|
| **backtesting.py** 0.6.6 | Best drop-in fit. AGPL-3.0, the same licence as Historify. Small (+8 packages / 53 MB). Its bracket-stop semantics reproduced our intraday reference 49/50 exactly. | One instrument per run; no portfolio. **Side-aware costs are impossible**: exit commission is computed with the positive trade size, so STT-on-sell / stamp-on-buy cannot be modelled. Sweeps are slow (~90 backtests/s on 4 cores, daily data). |
| **vectorbt** 1.2.0 | Fastest off-the-shelf engine. Multi-asset, parameter grids, walk-forward splitters. | **Apache-2.0 + Commons Clause** (the `vectorbt-rust` engine too), which conflicts with redistribution inside an AGPL app. **Its defaults are wrong for NSE intraday**: median **10–11 %** deviation on our 5m spec, and a Sharpe ratio overstated **2.4×**. Memory grows as bars × backtests (3.8 GB for 800 intraday backtests). Cold start 9–24 s. |
| **NautilusTrader** 1.231 | Highest fidelity: order lifecycle, volume-limited fills, a fee model that sees order + instrument, and a live-trading architecture. LGPL. | Heavy (+761 MB). With bar data it fills at the signal bar's **close**, so there is no "next open" without extra machinery. ~0.27 s per daily backtest, so sweeps are impractical. No OpenAlgo adapter. |
| **bt** 1.4.1 | Daily portfolio allocation and rebalancing. MIT. | One price series for both fills and valuation; not suited to intraday signal strategies. |
| **backtrader** 1.9.78 | Flexible commission model (signed sizes). | **Unmaintained since April 2023.** Optimiser crashes on Python 3.14 (`forkserver`). Slowest (4 backtests/s). |
| zipline-reloaded, PyBroker, qstrader, hftbacktest, pandas-ta | – | Excluded. zipline needs `pandas<3`, and 3.0.4 does not build on 3.14. PyBroker is non-commercial (Commons Clause). qstrader is unmaintained and daily-only. hftbacktest needs L2/tick data. pandas-ta needs `numpy<2.3`. |

**Answer to question 2: yes, and the evaluation shows it is the stronger option.** Build a thin **Historify-native
backtest layer** that owns the things that decide whether a result is right:
- the data contract,
- the execution semantics,
- the Indian cost schedules,
- calendar-correct metrics,
- overfitting controls.

Borrow the good ideas from each tool. Keep **backtesting.py as a bundled cross-check engine and user-facing API**, and
leave vectorbt as an *optional, user-installed* research tool that reads snapshots.

The prototype ([`backtest_evaluation/prototype/hbt`](backtest_evaluation/prototype/hbt)) is ~350 lines and shows this is
practical:
- It reproduces both independent reference implementations **exactly**: daily to 5e-15, and intraday with bracket
  stops and MIS square-off to 1e-13.
- It runs a **12,200-backtest intraday grid (0.48 G bar-steps) in 3.3 s at 387 MB**. vectorbt's memory use grows linearly with grid size
  (7 GB at 1,600 backtests), so the same grid would need a projected ~50–60 GB; I stopped the run before it ran out
  of memory.
- Its walk-forward step plus deflated Sharpe ratio correctly rejected the "best" of 12,200 strategies on data with no
  edge: in-sample Sharpe +1.33, out-of-sample −0.82.

**Before any engine is trustworthy, Historify's data needs four fixes** (§3). All were found by testing:
1. The bundled market calendar covers only 2025. It reports 262 NSE trading days for 2024 (the real figure is about
   249) and has no MCX holidays.
2. Corporate actions go undetected. An unadjusted 1:2 split (a −51 % "overnight move") passed the full integrity audit
   as `ok: true`.
3. Bar-open timestamps allow look-ahead. A naive daily-to-intraday join leaks the day's close into 100 % of intraday
   bars.
4. The DuckDB store cannot be opened **even read-only** by another process while Historify runs.

---

## 2. What Historify already does well for backtesting

The v2 data layer is already stronger than most home-grown stores. Verified during this audit:
- **Baseline suite:** 250 passed, 9 xfailed (the expected v1 characterisation tests) on Python 3.14
  (`results/historify_baseline_tests.log`).
- **One long `ohlcv` table**, clustered by instrument, interval and time. Loading 50 symbols × 2 years of 1m
  (9.8 M bars) takes about 1–5 s in-process; 50 × 10 years of daily takes 0.05 s.
- **Session-anchored roll-up** of 1m to 5m: exactly 75 bars per session for all 50 symbols; 1.96 M bars in about 3 s.
- **Dataset snapshots:** immutable, sha256-verified Parquet files plus a manifest with provenance, incomplete sessions
  and revisions per series. Building one for 50 symbols × (D + 1m) = 9.9 M bars took 44 s and 145 MB. This is exactly
  the right primitive for reproducible backtests.
- **Integrity machinery:** a session ledger, grid validation with quarantine, revision detection with archived old
  values, and a weekly audit.

The recommendation builds on these pieces rather than around them.

---

## 3. Audit findings on the data stores (prioritised)

| # | Severity | Finding | Evidence | Fix (effort) |
|---|---|---|---|---|
| **D1** | **Critical** for any backtest before 2025 | **The market calendar is a 2025-only sample.** 13 equity holidays, all in 2025; **0 MCX holidays**. Real closures such as 26 Jan and 15 Aug 2024 count as trading days. Real special sessions (Sat 20 Jan 2024, Muhurat) count as closed. The calendar shows **262 NSE trading days for 2024 against ~249 actual**. In real data this creates phantom `missing` sessions for every past holiday, and special-session bars are quarantined as `non_trading_day`, so they silently drop out of backtests. | `app/core/data/market_calendar.json` (`"version": "2025.1-sample"`); harness E7 | Seed history from **`exchange_calendars` XBOM** (Apache-2.0; BSE sessions since 2006; 245 sessions in 2024). Overlay NSE circulars for special sessions and add an MCX list. Put a **content hash** of the calendar in snapshot manifests, not a version label. (1–1.5 d) |
| **D2** | **High** | **No corporate-action layer.** The revision detector only catches values that change *after* they were stored. A split that is already in the first download is never flagged. The 1m-vs-D cross-check also passes, because both series are equally unadjusted. Our planted 1:2 split (−51.0 % overnight) gave **audit `ok: true`, 0 issues, 0 revisions**. SMA(20/50) equity on that symbol moved 6.0 %. A position held through the split would show a fake −50 %. | harness E6 | Add a `corporate_actions` table (NSE CA feed or manual import) and an *adjusted view* at read time (factor series; raw bars stay immutable). Add an audit check for overnight jumps that match typical split/bonus ratios. Record the adjustment mode in snapshots. (2–3 d) |
| **D3** | **High** (multi-timeframe) | **Look-ahead through bar-open timestamps.** `ts` is the bar *open*, and daily bars sit at 00:00 IST. A `merge_asof` of daily data onto 5m bars gives the 09:15 bar **that day's close**: 100 % of intraday bars leaked. It drops to 0 % once bars are aligned by an `available_at` time. | harness (look-ahead check) | Add `available_at` (bar close + finality buffer, the planner's `DAILY_FINAL_BUFFER`) to snapshot Parquet, `/api/v2/bars` and the loaders. Provide one multi-timeframe alignment helper. (0.5–1 d) |
| **D4** | **High** (integration) | **Other processes cannot read the store.** While Historify runs, `duckdb.connect(bars.duckdb, read_only=True)` from another process fails with *"Conflicting lock is held"*. Notebooks, a CLI backtester or a worker process cannot use the live file. | harness E1 (`lock_test.py`) | Two supported paths: **(a) snapshots** (already built) and **(b) a research replica**, i.e. a read-only copy refreshed after the post-close sync, using the existing rewrite/copy + swap mechanics. Backtests that run *inside* the app use `store._reading()` MVCC cursors. (0.5–1 d) |
| **D5** | Medium | **No instrument history.** `lot_size` holds only the current value (NIFTY lot sizes have changed several times). `tick_size` is never populated. There is no index-membership history, so universes built from today's index constituents carry survivorship bias. | `models.py`; `openalgo.py:243` | Add `instrument_attributes(valid_from, valid_to, lot_size, tick_size, …)` and `universe_membership(index, instrument, from, to)`. (1.5–2 d) |
| **D6** | Medium | **The snapshot manifest lacks backtest inputs:** no lot/tick size, instrument type, expiry, adjustment mode, calendar content, `available_at` or cost-schedule version. Each row also carries a `datetime_ist` string, which is redundant given `ts`. | `snapshots.py` | Extend the manifest (`historify-snapshot/2`). (0.5 d) |
| **D7** | Low | **The HTTP API is chart-sized.** `/api/v2/bars` caps pages at 5,000 bars, so 2 years of 1m is 40 JSON pages (1.8 s). 50 symbols take **~88 s, against ~2 s** from a snapshot. Fine for charts, wrong for research. | harness E2 | Do not build the backtester on this API. If needed, add a bulk Parquet/Arrow export endpoint. (optional) |
| **D8** | Medium (process) | **The CI licence gate is wrong in both directions.** It *rejects* GPL-3.0/AGPL-3.0 packages (backtesting.py, backtrader), which are compatible with this AGPL-3.0 project. It has *no explicit deny* for Commons Clause / non-commercial licences: vectorbt and PyBroker fail only by accident ("UNKNOWN", "Free for non-commercial use"). Permissive packages with missing metadata (TA-Lib BSD, peewee MIT) fail spuriously. | `.github/workflows/ci.yml`; harness run of the exact gate per engine | Allow `GPL-3.0`, `AGPL-3.0` and `LGPL`. Deny `Commons Clause`, `non-commercial` and `BUSL`. Keep a reviewed per-package override list for UNKNOWN metadata. (0.25 d) |
| **D9** | Info | **numba is deliberately absent** (see the `requirements.txt` comment). Every fast engine adds numba and llvmlite (+186 MB). | `footprint.txt` | Ship backtesting as an optional extra, `historify[backtest]`. |
| **D10** | Info | **DuckDB 2.0** refuses new work on a connection that still has an unconsumed result. The store is safe because it uses one cursor per call; adapter authors must do the same. | harness E2 | Note it in the adapter docs. |

---

## 4. Test bed

| | |
|---|---|
| Runtime | Python **3.14.7**, Historify's pinned lock (**pandas 3.1.1, numpy 2.5.4, duckdb 2.0.0**). Every install was repeated on 3.12.3. |
| Store | Built with Historify's own `Services` and `DuckDBBarStore.upsert`: **9.92 M bars**. 50 NSE equities + the NIFTY index. **D** 2015–2024 (2,609 sessions). **1m** 2023–2024 (522 sessions × 375 bars). For 2023–24 the daily close is the VWAP of the last 30 minutes, as NSE defines it. EQ07 has an **unadjusted 1:2 split** on 2024-06-03. |
| Snapshot | `eval-50` (D + 1m), created with Historify's `SnapshotService`. All engines read this, out of process. |
| Prices | Synthetic GBM with regime drift. Good for *engine* comparison; says nothing about real profitability. |
| Machine | Shared 4-core / 15 GB sandbox. Timings vary up to ~2× between runs, so compare orders of magnitude. |

---

## 5. Tool-by-tool results

### 5.1 Compatibility, licence, footprint, maintenance

| Engine | Installs on Historify lock, py3.14 | Licence | Passes current CI gate | Compatible with bundling in AGPL app | Extra deps on top of Historify | Latest release | Releases 2025-26 |
|---|---|---|---|---|---|---|---|
| vectorbt | yes (numba 0.68) | Apache-2.0 **+ Commons Clause** | no ("UNKNOWN") | **no** | +59 pkgs / 480 MB | 1.2.0 (Oct 2026) | 13 |
| backtesting.py | yes | AGPL-3.0 | no (should pass) | **yes** | +8 / 53 MB | 0.6.6 (Jul 2026) | 9 |
| NautilusTrader | yes | LGPL-3.0 | yes | yes | +8 / **761 MB** | 1.231 (Aug 2026) | 26 |
| bt | yes | MIT | yes | yes | +20 / 221 MB | 1.4.1 (Oct 2026) | 12 |
| backtrader | yes | GPL-3.0 | no (should pass) | yes | +1 / 1 MB | 1.9.78 (**Apr 2023**) | **0** |
| zipline-reloaded | **no** (3.2.1 needs `pandas<3`; 3.0.4 fails to build on 3.14) | Apache-2.0 | – | – | – | 3.2.1 | 3 |
| PyBroker | yes | Apache-2.0 + Commons Clause ("non-commercial") | no | **no** | – | 2.0.2 | 12 |
| qstrader | yes | MIT | – | yes | – | 0.3.0 (Jun 2024) | 0 |
| hftbacktest | yes | MIT | – | yes | – | 2.4.2 | – (needs L2/tick data) |
| *Building blocks:* numba 0.68.1 (BSD, +186 MB) · optuna 5.0 (MIT) · TA-Lib 0.8.2 (BSD, wheels) · exchange-calendars 4.13 (Apache-2.0) · DEAP 1.4.4 (LGPL) · gplearn 0.4.3 (BSD) · quantstats 0.0.86 (Apache-2.0, pulls yfinance) · riskfolio-lib 7.4 (BSD) · polars 2.1 (MIT) | all install | | | | | | |
| pandas-ta | **no** (`numpy<2.3`) | MIT | | | | | |

> Licence notes are an engineering assessment, not legal advice.
> - **GPL-3.0 and AGPL-3.0 combine legally** (section 13 of both licences).
> - **Commons Clause** forbids "selling" the software. AGPL-3.0 §7/§10 forbid further restrictions on recipients.
>   So vectorbt must not be bundled or required by a distributed Historify. A user may still install it to analyse
>   exported snapshots.

### 5.2 Correctness parity: daily (E3)

**Spec.** SMA(20/50) crossover on D, 50 symbols × 10 years. Signal on close; all-in whole shares; 0.1 % per side;
₹10 lakh per symbol. Compared against an independent pure-Python reference.

| Engine | Fill | Final equity = reference | Max rel. error | Trade-count mismatches | Time (50 backtests) |
|---|---|---|---|---|---|
| vectorbt | next open | **50/50** | 4.8e-15 | 0 | 0.06 s |
| vectorbt | close | **50/50** | 5.1e-15 | 0 | 0.07 s |
| backtesting.py | next open | **50/50** | 5.6e-15 | 26 ¹ | 1.8 s |
| backtrader (cheat-on-open) | next open | **50/50** | 5.5e-15 | 0 | 8.8 s |
| bt | close | **50/50** | 5.0e-15 | 0 | 5.3 s |
| NautilusTrader | close ² | 2/50 | 5.9e-6 ³ | 0 | 13.6 s |
| backtesting.py / backtrader | close | 49/50 ⁴ | 1.0e-3 | – | 1.3 / 8.3 s |

Notes:
1. `# Trades` excludes the trade still open at the end. Every mismatch is exactly −1.
2. Nautilus filled **51/51** market orders at the **signal bar's close**. With bars only there is no next-open fill.
3. Not a bug: Nautilus limits each O/H/L/C price point to **25 % of bar volume**. A 2,905-share order on a
   5,218-share bar filled 1,306 at 795.89 and 1,599 at 795.88. It is the only engine that models liquidity by default.
4. Edge case: a signal on the final bar. The engines correctly ignore it (there is no bar left to execute on); the
   reference filled it.

**Conclusion.** Once semantics are identical, the arithmetic of every engine is exact. **What differs is the
defaults**: fill timing, liquidity, how trades are counted, end-of-data handling. A Historify result is only meaningful
if Historify fixes those semantics explicitly.

### 5.3 Correctness parity: NSE intraday (E5)

This is where the engines really differ.

**Spec.**
- Data: 5m bars rolled up by Historify from 1m; 50 symbols × 2 years (39,150 bars each).
- Signal: EMA(9/21) crossover; fill at the next open.
- Stop: **0.5 % stop below the fill price, active from the entry bar** (a broker bracket/cover order).
- Entries: none after 14:55. **MIS square-off at the 15:15 open.**
- Costs: 0.03 % per side.
- Scale of the reference run: 40,814 entries and 16,776 stop exits.

| Engine (same precomputed signals) | Exact | Within 0.1 % | Median deviation | Max deviation | Time (50) |
|---|---|---|---|---|---|
| backtesting.py (`sl=` bracket) | **49/50** | **50/50** | 0.000 % | 0.001 % | 13.2 s |
| vectorbt, defaults | 0/50 | 0/50 | **11.0 %** | **30.4 %** | 0.97 s |
| vectorbt, `stop_entry_price="fillprice"` | 0/50 | 0/50 | **10.6 %** | 15.7 % | 0.86 s |
| **hbt prototype** | **50/50** (1e-13) | 50/50 | 0 | 0 | **0.06 s** |

**Root cause, proven.** A reference implementation with the following rules matches vectorbt **50/50 exactly**
(`e5_ref_variants.py`). All three are documented vectorbt behaviour, not bugs:
- `stop_entry_price` defaults to `close`, so the stop is anchored to the entry bar's close rather than the fill price;
- the stop is **not active on the entry bar**;
- the stop has **priority over open-executed signal exits and square-offs on the same bar** (`base.py:2308`: *"Stop
  signal has priority"*).

For tight intraday stops, these rules produce a materially different strategy.

**Annualisation.** vectorbt's default `year_freq = 365 days` treats 5m data as 105,120 bars a year. NSE actually has
18,600 (248 × 75).
- Reported Sharpe: **−2.54**. Correct Sharpe: **−1.07**, so the figure is inflated **2.38×**.
- On daily data the inflation is √(365/248) ≈ 1.21×.

### 5.4 Speed and scale for strategy search (E4)

| Workload | hbt prototype (numba, 4 threads) | vectorbt | backtesting.py | backtrader | NautilusTrader |
|---|---|---|---|---|---|
| **5,000 daily** backtests (10×10 SMA grid × 50 symbols × 10 y) | **0.04–0.06 s** | 11.9 s | ~56 s (4 procs) | ~1,200 s single core (parallel mode crashes on 3.14) | ~1,360 s (est.) |
| **Intraday** backtest, 39k 5m bars, warm | **~0.3 ms** (4 threads; 1.2 ms single-threaded) | 73–81 ms (numba/rust engine; sim 23–34 ms + metrics 47 ms) | 264 ms | – | – |
| **12,200 intraday** backtests (244 sets × 50 symbols, 0.48 G bar-steps) | **3.3 s, 387 MB** | projected ~50–60 GB: 200 → 1.4 GB, 800 → 3.8 GB, 1,600 → 7.0 GB; stopped before OOM | ~54 min single process (~14 min on 4 cores, est.) | – | – |
| Cold start in a fresh process (import + first run) | 0.4 s first call with the numba cache | 9 s with warm numba cache (3.5 s import + 5.5 s first call); 24 s without; 12 s with the rust engine | ~2.7 s import | ~1 s import | 2.4–4.7 s import |

**Why the prototype is faster.**
- vectorbt's `from_signals` is general-purpose. It materialises bars × columns arrays and computes metrics through
  pandas.
- A purpose-built kernel generates signals *inside* the parallel loop and accumulates statistics on the fly, so memory
  stays constant.
- This is a design choice we can make for our own workloads, not a criticism of vectorbt.

### 5.5 Indian cost model (E8)

NSE costs are asymmetric and change over time:
- STT is charged on the sell side (intraday equity) or both sides (delivery);
- stamp duty applies to the buy side only;
- brokerage is `min(₹20, x %)`;
- GST is charged on brokerage and exchange charges;
- rates were revised on 1 Oct 2024.

| Engine | Interface | Side-aware? | Time-versioned? |
|---|---|---|---|
| backtesting.py | `commission(size, price)` | **No for exits.** Verified: the closing SELL is called with `+size` (source line 1089 uses `trade.size`). | no (no timestamp) |
| vectorbt | `fees` / `fixed_fees` per bar × column | only via per-bar arrays; wrong for stop exits; no cap (`min`) | via arrays |
| backtrader | `CommInfoBase._getcommission(size, price, pseudoexec)` | **yes** (signed size, verified) | via strategy clock |
| bt | `commissions(q, p)` | **yes** (signed q, verified) | no |
| NautilusTrader | `FeeModel.get_commission(order, fill_qty, fill_px, instrument)` | **yes** | **yes** (order timestamps) |
| hbt prototype | per-bar rate vectors from a versioned schedule | **yes** | **yes** |

On realistic intraday costs, the median round trip on a ₹10 lakh position costs **₹370**. That is the difference
between a profitable-looking 5m strategy and a losing one. Cost modelling therefore belongs in the core, not a plug-in.

### 5.6 Strategy generation tools

| Capability | Off-the-shelf options (tested to install on the stack) | Verdict |
|---|---|---|
| Grid search | vectorbt param products; backtesting.py `optimize(method="grid")`; backtrader `optstrategy` | Use our kernel. It is 100–1000× faster and memory-flat. |
| Bayesian / adaptive search | **optuna 5.0** (MIT, passes CI); backtesting.py `method="sambo"` | Adopt optuna for large or continuous spaces. |
| Rule / genetic generation | **DEAP 1.4.4** (LGPL), **gplearn 0.4.3** (BSD) | Phase 2: compile a small rule DSL (indicator blocks + comparators + exits) to kernel inputs. |
| Walk-forward splits | vectorbt `RangeSplitter`/`RollingSplitter` | Reimplement (about 30 lines); the prototype does it. |
| Overfitting control | none of the engines ship it (PyBroker has a bootstrap, but a non-commercial licence) | **Build it**: deflated Sharpe (done in the prototype), PBO/CPCV, parameter-stability heat maps, cost stress, Monte Carlo trade reshuffle. |
| Tear sheets | quantstats, pyfolio-reloaded, empyrical (Apache-2.0) | Optional. They assume 252-day years; pass NSE period counts or compute metrics ourselves. |

Commercial generators (StrategyQuant X, Build Alpha) are worth copying for their *robustness-test menu*, but cannot be
integrated.

**Prototype demonstration** (`strategy_search_demo.py`, synthetic data with **no** real intraday edge):

| Stage | Result |
|---|---|
| Search | **12,200** EMA × stop combinations over 50 symbols in 3.3 s |
| Walk-forward (3 folds) | In-sample Sharpe of chosen sets **+1.33** → out-of-sample **−0.82** (positive in only 32 % of cases) |
| Deflated Sharpe | Best of 12,200: annualised +1.26, against +3.12 expected by chance → **DSR = 0.00, not significant** |

A strategy generator *without* these stages would have presented curve-fit noise as an edge.

---

## 6. Recommendation: a Historify Backtest Lab that takes the best of each tool

```
                          ┌────────────────────────── Historify (existing) ─────────────────────────┐
                          │ bars.duckdb (single writer) · meta SQLite · calendar · ledger · archive │
                          └──────┬───────────────────────────┬────────────────────────────┬────────┘
                    in-process job│ store._reading()     snapshots (immutable Parquet   research replica
                                  │                        + manifest v2, available_at)   (read-only copy after
                                  ▼                           ▼                           post-close sync)
 ┌──────────── Backtest Lab  (optional extra: historify[backtest] = numba, optuna, TA-Lib) ──────────────────┐
 │ hbt.data      Universe: aligned matrices + session metadata + provenance (snapshot id, manifest sha256) │
 │ hbt.adjust    corporate-action factors (adjusted view), calendar from the versioned calendar            │
 │ hbt.signals   numpy / TA-Lib / numba indicators; user Strategy API (init/next, backtesting.py-style)    │
 │ hbt.kernel    ONE execution spec: fill timing, bracket/cover stops, MIS square-off, lots, liquidity cap │
 │ hbt.costs     time-versioned, side-aware schedules per segment (EQ delivery/intraday, FUT, OPT), meta DB │
 │ hbt.metrics   calendar-correct annualisation; trades, exposure, drawdowns                                │
 │ hbt.search    grid (prange) · optuna · rule DSL + DEAP/gplearn (later)                                   │
 │ hbt.validate  walk-forward · CPCV/PBO · deflated Sharpe · cost stress · Monte Carlo                      │
 │ oracle tests  backtesting.py + reference implementations in CI (parity suite, as in this evaluation)     │
 └──────────────────────────────────────────┬────────────────────────────────────────────────────────────────┘
                                            ▼
              meta DB: backtest_runs (snapshot_id, manifest_sha256, code_sha, params, exec_spec, cost_version,
              calendar_hash, metrics) · instance/backtests/<run>/trades.parquet, equity.parquet
              UI: Backtests page (job kind "backtest", progress through the existing JobService/BackgroundRunner)
```

| We take… | …from | Why |
|---|---|---|
| Columnar, compiled, parallel parameter sweeps; splitters | **vectorbt** (the idea, not the code) | Speed for strategy search. Avoids its licence, memory model and NSE-unsafe defaults. |
| A simple `Strategy.init/next` API; bracket-order semantics; interactive single-run analysis | **backtesting.py** (bundled, AGPL-compatible) | Familiar for users. Proven to match our intraday reference. Becomes the CI **oracle** for the kernel. |
| A fee model that sees order, side, time and instrument; liquidity cap as % of bar volume; same strategy code for backtest and live | **NautilusTrader** (design) | Realism. A future path to live/paper trading through OpenAlgo. |
| Weighting and rebalancing algos for positional portfolios | **bt** (MIT, could be bundled) | Daily/monthly allocation strategies (phase 3). |
| Historical exchange sessions | **exchange_calendars** XBOM (Apache-2.0) | Fixes D1. |
| Indicators | **TA-Lib** (BSD, wheels for 3.14) | pandas-ta does not install on numpy 2.5. |
| Bayesian search; genetic rule generation | **optuna**; **DEAP/gplearn** | Strategy generation beyond grids. |
| Deflated Sharpe, PBO, CPCV | Bailey & López de Prado (papers; our own code) | The only defence against generating noise. |

### Non-negotiable semantics for the kernel

The prototype already implements the first five. Each must be a parameter with a safe default, written into every run
record:
1. **Signals are read at the bar close; orders execute at the next bar open.** `close` fills are opt-in and flagged.
2. **Within a bar:**
   - open events come first (square-off, then signal exits, then entries);
   - then protective orders, active on the entry bar;
   - a gap through a stop fills at the open;
   - if both stop and target are touched, the stop counts first (pessimistic).
3. **Session rules:** MIS square-off time and a no-new-entries-after time per segment; positions never carry across
   sessions for intraday segments.
4. **Sizing** in whole lots (lot-size history from D5). Optional cap per bar as a share of bar volume (Nautilus-style).
5. **Costs** from the versioned schedule for the fill's trade date; **annualisation** from the exchange calendar and
   interval.
6. **Data:** adjusted or raw (explicit); multi-timeframe joins only through `available_at`; a run refuses to start on
   a snapshot whose checksum fails, and **warns** if it has incomplete sessions in range.

---

## 7. Roadmap and effort

Estimates are for one engineer familiar with the codebase. Phase 0 is a prerequisite whichever engine is chosen.

| Phase | Content | Effort |
|---|---|---|
| **0. Data readiness** | D1 historical calendar + special sessions + MCX · D2 corporate actions + adjusted reads + jump audit · D3 `available_at` · D4 research replica · D6 manifest v2 · D8 CI licence gate | **5–8 d** |
| **1. Core (MVP)** | `hbt.data/kernel/costs/metrics` hardened from the prototype (long and short, targets, trailing stops, lots, liquidity cap) · parity suite against backtesting.py and the reference implementations in CI · job kind `backtest`, `backtest_runs` table, results Parquet · minimal Backtests page (equity, trades, drawdown on Lightweight Charts) | **8–12 d** |
| **2. Strategy generation** | Grid and optuna search as background jobs · walk-forward, CPCV/PBO, deflated Sharpe · robustness tests (parameter perturbation, cost ×1.5, Monte Carlo) · results comparison UI | **6–10 d** |
| **3. Portfolio and F&O** | Shared cash across instruments, position sizing, bt-style rebalancing · continuous futures and roll rules · lot-size/expiry history · option-chain backtests (needs on-demand chain downloads) | **8–15 d** |
| **4. Paper/live parity** (optional) | Same strategy class driving OpenAlgo `placeorder` in paper mode; signal-to-fill reconciliation against backtest expectations. Alternatively, an OpenAlgo adapter for NautilusTrader. | **5–10 d** |

### Decisions needed
1. **Engine strategy:** build the thin core and bundle backtesting.py as oracle and API (recommended), or accept one
   engine's semantics and limits.
2. **Packaging:** is an optional extra `historify[backtest]` acceptable? It adds ~270 MB: numba 186, backtesting.py 53,
   TA-Lib 30, optuna 5.
3. **vectorbt:** document it as "bring your own, reads snapshots", or ask the author for a licence exception (the
   README invites this).
4. **Corporate-action source:** an NSE corporate-actions file import, or manual entry with the audit flagging
   candidates.
5. **Default execution semantics:** confirm the next-open, bracket-stop and 15:15 MIS defaults above (and the 15:20
   variant some brokers use).

---

## 8. Limitations of this evaluation
- **Synthetic prices.** These tests say nothing about strategy profitability. They test engines and data-handling
  semantics only.
- **Timings** come from a shared sandbox with up to ~2× variance. The conclusions rest on differences of 10× or more.
- **Not evaluated:** vectorbt PRO (commercial, closed) and QuantConnect LEAN (C#/Docker, its own data format).
  zipline was not run because it does not install on the stack.
- **Cost rates** in `hbt/costs.py` are illustrative placeholders. Verify them against NSE/SEBI circulars and the
  broker's tariff.
- **The prototype is an evaluation artefact:** long-only, single-asset kernel, no portfolio. It is intentionally not
  wired into the app.

**Reproduce:** [`backtest_evaluation/README.md`](backtest_evaluation/README.md). Raw results are in
`backtest_evaluation/results/`.
