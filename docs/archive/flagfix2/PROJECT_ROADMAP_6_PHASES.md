# Historify + Lab: Architecture, Six-Phase Roadmap, and This Round's Delivery (Scanner + E17)

| | |
|---|---|
| **Your decisions** | Build the scanner and E17 · break the whole project into 6 phases · show a proper HLD diagram (not a sequence chart) |
| **Delivered this round** | Intraday "in-play" scanner + India VIX regime service + E17 study, running inside Historify (API, CLI, UI page, scheduled 09:05 / 09:31 IST), tested end to end |
| **Diagrams** | `diagrams/hld_architecture.png` (layered HLD) · `diagrams/roadmap_6_phases.png` (Gantt) · `diagrams/trading_day_timeline.png` (a day in the system) |
| **Replaces** | The roadmaps in `BACKTESTING_AND_SIGNALS_HLD.md` §15, `TWO_TRACK_SIGNALS_DESIGN.md` §9 and `INDIA_DATA_SOURCES_CATALOGUE.md` §9 are consolidated here |

---

## 1. High-level design

![HLD architecture](diagrams/hld_architecture.png)

**How to read it**
- Data flows **top to bottom** through seven layers.
- Colour shows status:
  - **green**: built earlier;
  - **blue**: built this round;
  - **grey dashed**: planned, tagged with its phase.
- The only flow going **up** is the order path (orange). Signals reach the broker through OpenAlgo, paper first, live only after your approval.

| Layer | Responsibility | Key rule |
|---|---|---|
| 1 External sources | Broker (OpenAlgo → Angel One), free public websites, NSE archives, optional paid vendors | Never more than the broker budget (80%); public sites polled politely |
| 2 Ingestion | Download engine, News & Filings archive, reference-data jobs, scheduler | Restart-safe background jobs; raw data kept as received |
| 3 Storage | DuckDB bars, meta SQLite, feeds SQLite + files, reference tables, snapshots | Append-only / idempotent; every series has provenance |
| 4 Data services | Integrity & roll-up, **point-in-time DataPortal**, events & disclosures, feature library, **regime service** | Nothing is visible before its `available_at` |
| 5 Research & decision | **Scanner**, trade simulator (T), rebalance simulator (L), F&O engine, **experiments** | The same code runs live and in backtests / replays |
| 6 Signals & execution | Evaluation gate, signal service, risk & sizing, order bridge | A strategy goes live only after passing the gate and paper parity |
| 7 Interfaces | Web UI, API v2, CLI, backtest/signal pages, you | Local by default; login + API tokens required on a network bind (P6b ✅) |

## 2. A trading day in the system

![Trading day](diagrams/trading_day_timeline.png)

**What runs today**

| Time (IST) | What happens |
|---|---|
| All day | Filings and news are archived continuously |
| 09:05 | Pre-market scan |
| 09:31 | 1m refresh, then the confirmation scan |
| 15:45 | Post-close download |
| Evening | Disclosure and flow snapshots |

Signals (P4) and the evening bhavcopy features (P1/P2) slot into the same day once those phases land.

---

## 3. The six phases

![Roadmap](diagrams/roadmap_6_phases.png)

| Phase | Goal | Status | Remaining effort | Done when (exit criteria) |
|---|---|---|---|---|
| **P1 Data foundation & correctness** | Every bar, date and corporate action is right and point-in-time | **✅ Built, reviewed, tested** (see `P1_DATA_FOUNDATION_REPORT.md`); live self-test pending on your machine | **done** | Calendar trading days equal bhavcopy dates for every year · split-adjusted series pass the CA test · roll-up audit run on your account · bhavcopy (delivery %, F&O OI) ingested daily · F&O chain forward collection running |
| **P2 Market intelligence** | Turn filings, news, flows and volatility into usable signals | **✅ Built, reviewed, tested** (see `P2_MARKET_INTELLIGENCE_REPORT.md`, `P2_TEST_STRATEGY.md`); live self-test + fixture capture pending on your machine | **done** | Typed insider / deal / pledge tables + pledge metrics · event calendar (results, RBI, Fed, Budget, expiry) · FII/DII + participant-OI series · BSE↔NSE mapping · pre-open snapshot |
| **P3 Backtest & research engine** | Test any idea honestly | **✅ Built, reviewed, tested** (see `P3_BACKTEST_ENGINE_REPORT.md`); real-data acceptance runs (E1, E9, E11, E14) pending on your machine | **done** | DataPortal with MTF alignment + adjusted/raw views · feature library (WMA…OI) · event-study tool · trade simulator with India costs/circuits/MIS · walk-forward, sweeps, deflated Sharpe · look-ahead property test on every strategy · Backtests UI |
| **P4 Track T → paper signals** | Swing and intraday signals you can trade | **✅ Built, reviewed, tested** (see `P4_SIGNALS_REPORT.md`, guide `docs/signals.md`); deploying E9/E11 and the 20–40-session paper period run on your machine | **done** (paper period in calendar time) | E9–E16, E18–E21 run · swing EOD signals → D + 15m bridge → 15m intraday · signal service with completeness gate · OpenAlgo Analyzer paper trading · 20–40 sessions of 1:1 parity |
| **P5 Track L long-term** | Model portfolio and thesis alerts | **✅ Built, reviewed, tested** (see `P5_P6_IMPLEMENTATION_REPORT.md`, guide `docs/portfolios.md`); a fundamentals vendor feed and 2 quarters of paper run on your machine | **done** (paper period in calendar time) | Rebalance simulator + factor toolkit · price-only E1–E5 · PIT fundamentals · ownership/pledge scores · thesis-breaker alerts from filings · model portfolio on paper for 2 quarters |
| **P6 F&O/options + live operations** | Options research, then live trading safely | **✅ Built, reviewed, tested** (see `P5_P6_IMPLEMENTATION_REPORT.md`, guides `docs/options.md`, `docs/live-operations.md`); E15 on your collected chain bars and the live dry run pending on your machine | **done** (E15 data + live dry-run in calendar time) | F&O data + simulator (lots, margins, expiry) · E15 options study · authentication · live order bridge with limits + kill switch · WebSocket bar builder · monitoring |

**Total remaining: implementation complete.** What remains runs in calendar time on your machine with your data: the
P3/P4 experiment runs and paper periods, a fundamentals vendor feed for P5 (or XBRL parsing), two quarters of model
portfolios on paper, E15 on collected option-chain bars, and the live dry-run sessions before any real order.

**Staffing options**

| Staffing | Plan | Duration |
|---|---|---|
| One engineer | P1 → P2 → P3 → P4 → P5 → P6 | ≈ 30 weeks |
| **Two engineers** (recommended) | A: P1 → P3 → P4 → P6b (live ops), ≈ 81 d. B: P2 → P5 → P6a (F&O), ≈ 62–72 d. | **≈ 17 weeks** |

**Critical path:** P1 → P3 → P4.

- P5 can start after P1: it needs corporate actions and index constituents, but not the trade simulator.
- P6a needs the P3 simulator and the chain data collected since P1.

**Milestones**

| # | When | Outcome |
|---|---|---|
| M1 | now | Archive, scanner, regime and E17 running |
| M2 | W3 | Data correct · **E17 on your real data** · daily in-play lists |
| M3 | W9 | First tested swing/intraday ideas |
| M4 | W10 | Track L paper model portfolio |
| M5 | W13 | Track T paper signals with parity checks |
| M6 | W17 | Options research · live trading (auth-gated) |

### Phase details

#### P1 · Data foundation & correctness (≈ 15 d)
| Work item | Effort |
|---|---|
| Holiday and special-session history 2010→ (blocker B1: 2024 currently has 262 "trading" days) | 1.5 d |
| Instrument metadata + lot/tick history (B4) | 1.5 d |
| Corporate-actions table + adjusted/raw views (B3) | 2.5 d |
| Bhavcopy importers, equity + F&O, legacy + UDiFF format (delivery %, settle price, OI) | 4 d |
| F&O master · eligibility · ban/ASM lists | 2 d |
| Index constituents (point-in-time universes) | 1.5 d |
| Risk-free and breadth REF series | 1 d |
| **Start the F&O chain forward collection** | 1.5 d |
| **You:** run `rollup-audit` and the feeds self-test on your machine | — |

#### P2 · Market intelligence ✅ done
Built earlier: News & Filings archive, regime service, in-play scanner, E17. Built in P2 (details in
`P2_MARKET_INTELLIGENCE_REPORT.md`):

| Work item | Status |
|---|---|
| Typed disclosures from the archived raw records: insider trades, bulk/block deals, SAST, pledge snapshots, encumbrance events; promoter-buying and pledge signals | ✅ |
| Point-in-time event calendar (results, ex-dates, expiries, Budget/CPI/GDP rules, CSV import for RBI/FOMC/elections, re-scheduled meetings) | ✅ |
| Flow series (FII/DII from the archive, participant OI) + pre-scan snapshot | ✅ |
| BSE↔NSE↔ISIN securities map + BSE filing links + headline entity linking (precision/recall measured) | ✅ |
| 09:08 pre-open snapshot → 09:10 pre-market scan (pre-open gap in the score; other intel as recorded features) | ✅ |
| Quality report → Data Health issues; live-fixture contract tests; self-test | ✅ |
| **You:** `flask historify intel selftest`, then after a few days `intel capture-fixtures` and commit `tests/fixtures/intel/live/` | — |

#### P3 · Backtest & research engine ✅ done
Implementation and test plan: `P3_BACKTEST_ENGINE_PLAN.md`; what was built, test results and gaps fixed:
`P3_BACKTEST_ENGINE_REPORT.md`; user guide: `docs/backtesting.md`. Next: P4 (done, see below).

| Work item | Effort |
|---|---|
| Snapshot v2 (content-addressed partitions) + DataPortal (MTF alignment on `available_at`, adjusted/raw) | 5 d |
| Feature library with batch/incremental parity | 5 d |
| Event-study tool | 3 d |
| Hybrid trade simulator: vectorised signals + 1m execution; India execution model (next-bar fills, gap-through stops, circuits, MIS, lots, ticks, cost schedule) | 7 d |
| ComputeRunner (process pool) + run registry | 2 d |
| Evaluation: walk-forward, sweeps, deflated Sharpe, Monte Carlo, regime splits | 3 d |
| Backtests UI | 3 d |

#### P4 · Track T to paper signals ✅ done
Design: `P4_SIGNALS_HLD.md` (decisions R1–R12); what was built, tests, performance and the post-implementation code
review: `P4_SIGNALS_REPORT.md` + `CODE_REVIEW_FINDINGS.md`; user guide: `docs/signals.md`.

| Work item | Status |
|---|---|
| Signal service: deployments + versions, decision points (15m bar close, EOD, schedules, exit clocks), UniverseFollow, catch-up / missed, completeness gate, live replay (engine live mode) + differ, shadow book | ✅ |
| Delivery (SSE page, HMAC webhook with retries / dead letter, Telegram, digest), risk limits + kill switch, API token + host guard, audit log | ✅ |
| OpenAlgo Analyzer paper orders (SL-M / target OCO, reconciliation, execution-quality report), nightly parity with divergence classes, promotion gate with P3 evidence | ✅ |
| Trade journal + Signals UI + dashboard widget | ✅ |
| **You:** strategy and experiment runs on your data (E9–E14, E16, E18–E21 in live-parity mode), deploy E9 / E11, 5 + 10 + 20–40 sessions through signals → shadow → paper | — |

- **Gate:** every deployment passes the evaluation gate, then 20–40 sessions of OpenAlgo Analyzer paper trading with 1:1 signal parity.

#### P5 · Track L long-term ✅ delivered (see `P5_P6_IMPLEMENTATION_REPORT.md`)
Pre-P5 audit (`PRE_P5_AUDIT.md`) prerequisites - all delivered first:
- symbol-change / ISIN continuity (A-24);
- bhavcopy-sourced history for delisted stocks, against survivorship bias (A-25);
- TRI benchmarks (A-26).

| Work item | Effort |
|---|---|
| **Prerequisites:** ISIN continuity across symbol changes, delisted-stock history from the bhavcopy archive | 4 d |
| Rebalance simulator | 5 d |
| Factor toolkit (IC, quintiles) | 4 d |
| TRI benchmarks + Nifty P/E series | 1 d |
| Price-only E1–E5 | 3 d |
| PIT fundamentals: adapter protocol + CSV import delivered; a vendor adapter (5 d) or XBRL core (15 d) remains | 5–15 d |
| Ownership/pledge scores | 3 d |
| Thesis-breaker monitor | 2 d |
| Model-portfolio UI | 4 d |

#### P6 · F&O/options + live operations ✅ delivered (see `P5_P6_IMPLEMENTATION_REPORT.md`)
| Part | Work item | Effort |
|---|---|---|
| P6a | F&O data (continuous futures, IV/Greeks job) | 10 d |
| P6a | F&O simulator (lots by date, margins, expiry/physical settlement, baskets) | 8 d |
| P6a | E15 options study | 3 d |
| P6b | Authentication | 4 d |
| P6b | Live order bridge (limits, kill switch, idempotency, approvals) | 4 d |
| P6b | WebSocket tick → 1m bar builder | 8 d |
| P6b | Monitoring | 2 d |

---

## 4. Delivered this round: scanner, regime, E17

### 4.1 What it does
| Component | What you get | Where |
|---|---|---|
| **Regime service** | India VIX (previous close), its 1-year percentile, day-on-day change, expected Nifty move (VIX ÷ √252, in % and points), Nifty trend, 10-day realised volatility, implied − realised. Regime **low / normal / high / stress** with a size multiplier (1 / 1 / 0.5 / 0.25) and a trading hint. Thresholds are percentile-based and fixed in advance. | Scanner page header · `GET /api/v2/lab/scanner/status` · `flask historify lab regime` |
| **Pre-market scan (09:05)** | For each instrument in the watchlist **"Intraday universe"** (or all watchlists), computed **point-in-time** from daily bars up to the previous close: <br>• ATR %, ATR 5/20 expansion, NR7 / inside day;<br>• 60-day beta to Nifty, 5/20-day relative strength;<br>• median turnover;<br>• **catalysts** from the News & Filings archive published since the previous close (results, orders, ratings, pledges, insider/SAST, bulk/block…);<br>• **board meeting today**.<br>Eligibility filters (price, turnover, history), then a weighted percentile score, rank and bias. | Scanner → In play · `POST /api/v2/lab/scanner/run {"kind":"premarket"}` |
| **Confirmation scan (09:31)** | Refreshes the universe's 1m bars through the normal download engine, then adds: gap (% and in ATR), 15-minute opening range (in ATR), **relative volume** vs the same 15 minutes over the last 20 sessions, VWAP position, and relative strength vs Nifty since the open. Output: ranked **top N** with long / short / neutral bias. | Scanner → In play · `{"kind":"confirm"}` |
| **Replay** | Any past trading date, as of its scheduled time, with identical point-in-time code: the basis for backtesting scanner-selected universes (E18) | Scanner → Replay date · `{"kind":"confirm","date":"YYYY-MM-DD"}` |
| **History** | Every run is stored with its regime snapshot, parameters, notes and all per-instrument features (scan_runs / scan_results, Alembic 0004) | Scanner → History · `GET /api/v2/lab/scanner/runs` |
| **E17 study** | Does the prior-close VIX percentile predict Nifty's intraday range, \|open→close\|, gap, trend days and opening-range-breakout follow-through? Pre-registered terciles, seeded permutation tests, bootstrap confidence intervals and a verdict (SUPPORTED / MIXED / REJECTED / INCONCLUSIVE). Report in Markdown + JSON. | Scanner → Research: E17 · `POST /api/v2/lab/e17` · `flask historify lab e17` |
| **Prepare research data** | One click: creates NIFTY + INDIAVIX (NSE_INDEX) in a "Research indices" watchlist and queues NIFTY/INDIAVIX daily (10 years), NIFTY 1m (1 year, as deep as Angel One serves), universe daily (2 years) + 1m (60 days) | Scanner → Research → Prepare · `flask historify lab prepare` |

### 4.2 How to use it (your machine)
1. Create a watchlist named **Intraday universe** with 20–200 liquid F&O stocks, or choose another watchlist in Scanner → Settings.
2. Click **Prepare research data** (Scanner → Research: E17), then let the download jobs finish (Jobs page).
3. Click **Run E17**. The verdict tells you whether to use the VIX regime for sizing and stop width.
4. From the next trading day, the scans run automatically at 09:05 and 09:31 IST. Review them on the Scanner page.
   - Defaults: price ≥ ₹100, median turnover ≥ ₹50 Cr, top 15.
   - Weights and times are adjustable in Settings.

### 4.3 Verification in the sandbox
| Check | Result |
|---|---|
| **E17 detects a real VIX→volatility link** (synthetic data where intraday σ = VIX/√252) | **SUPPORTED**: range 1.29% → 1.71% → 2.16% across terciles, Spearman 0.55, p = 0.0005; range ÷ expected stable across terciles (calibrated) |
| **E17 does not invent a link** (synthetic data with constant volatility, 3 seeds) | **REJECTED** (e.g. Spearman −0.04, p = 0.32) |
| E17 with too little data | **INCONCLUSIVE** with the reason |
| E17 point-in-time | Corrupting today's and future VIX values leaves every classification unchanged |
| **Scanner point-in-time** | Overwriting every 1m bar after 09:31 and today's daily bar with absurd values leaves ranks, scores, RVOL and ATR **identical** |
| Catalysts timing | A filing published at 10:00 is not counted by the 09:05 scan, but is counted by a 10:30 scan; filings before the previous close are ignored |
| Scheduling | Runs at 09:05 and 09:31 once per trading day (refreshing 1m via the engine), skips weekends and holidays, honours the auto switch |
| End to end | Real downloads from the mock Angel One broker (daily + 1m), Prepare data → E17 job → report files, API, CLI, Scanner page in Chromium (no console errors, no CSP violations) |
| Tests | New: **16 lab unit/method tests + 9 lab integration tests + 2 browser tests**, all passing. Full-suite results are in the round summary. |

A sample report on synthetic data (clearly labelled) is at `E17_SAMPLE_REPORT_SYNTHETIC.md`. **The real E17 verdict needs your INDIAVIX + NIFTY history.**

---

## 5. Next decisions for you
| # | Decision | Recommendation |
|---|---|---|
| 1 | Staffing: one engineer (≈ 30 weeks) or two in parallel (≈ 17 weeks)? | Two, split as shown (Track T path / Track L + F&O path) |
| 2 | Order of P1 vs P2 remainder if only one engineer | P1 first: data correctness gates everything after it |
| 3 | Your universe for the scanner | F&O stocks with median turnover ≥ ₹50 Cr (about 150–200 names) in "Intraday universe" |
| 4 | Run E17 now on your machine, and share the verdict | Yes: it decides whether the regime multiplier becomes a hard rule in P4 |
