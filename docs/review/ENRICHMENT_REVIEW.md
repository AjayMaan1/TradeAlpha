# TradeAlpha: component-by-component enrichment review

**Purpose:** list every enrichment TradeAlpha needs for its use case: Indian-equity research, backtesting, signal/idea
generation and rule-bound execution through OpenAlgo. This document is for review. **Nothing here is implemented
yet.** Approve, reject or re-prioritise items, then implementation starts in the order in §12.

**Reviewed tree:** `tradeAlphav2` as adopted in commit `6bdb44e`. Where the v2 capture is missing a module, I read
its direct ancestor (the `fix1` historify tree, kept under git tag `pre-v2-cleanup`). Those items are marked *(ancestor)*.

---

## 0. Read this first: the v2 upload is incomplete (blocker)

`tradeAlphav2/` was not a source tree. It was a **partial change capture**: a diff plus saved files. Its own manifest
says *"1,461 files (54.8 MB of diff) are in neither changes.diff nor files/"*.

What I did:

| Step | Result |
|---|---|
| Applied `changes.diff` to the repository and overlaid `files/` | Old change sets deleted, v2 layout in place (`src/tradealpha`, `rust/`, `tests/`, `docs/`) |
| Restored files v2 *moved unchanged* but the capture omitted | **397 files** restored byte-exact. Git rename headers have a fixed size (`63 + 2·len(src) + 2·len(dst)`), so each source path was identified exactly (Rust engine, `hbt` Python package, ADR-001..004, deploy files, icons, fonts) |
| Repaired binaries corrupted by the capture | 8 PNGs + the `legacy_v1.db` test fixture, restored from the identical originals |
| Deleted the older change sets, as instructed | `fix1`, `fix2`, `flagfix1`, `flagfix2`, `flagfix-…-final (1)` (a byte-identical duplicate of flagfix1), `eq m 3`, the v1 `TradeAlpha/` app, `design/`, `sample/`, stale `test-results/` |
| Kept everything recoverable | Tag **`pre-v2-cleanup`** = the original upload; `git checkout pre-v2-cleanup -- <path>` brings anything back |

**What is still missing:** `docs/review/V2_MISSING_FILES.tsv` lists 208 v2 files whose content was never captured.
The manifest's unlisted tail adds 498 more. Together they include the **entire application layer**:

* `src/tradealpha/execution/{oms,risk,host,reconciliation}` (OMS, guardrails, runtime, EOD reconciliation)
* `src/tradealpha/ideas/*` (generation, generators, scanners, service), `altdata/*`, `features/*` (except 2 files),
  `evolution/*`, `apps/*` (container, serve, trader, research)
* `src/tradealpha/interfaces/{api,cli,web}` (all REST endpoints, CLI, Flask app, templates, JS, compiled CSS)
* everything alphabetically after `interfaces/web/static/icons/moon.svg`: `lifecycle/*` (gates, signals), `marketdata/*`
  (store, ingestion, providers, refdata), `platform/*` (db models, timeutil, calendar, auth, events), `research/*`,
  most of `tests/`
* `README.md` (v2, 39 KB), `ARCHITECTURE.md`, `CHANGELOG.md`, `pyproject.toml`, `run.py`, `requirements*.txt/in`,
  `.env.sample`, ADR-005..017, `docs/architecture/*`, runbooks, CI workflows

The retained v2 modules import about 60 modules that are not in the tree, so **the repository cannot run today**.

| ID | Enrichment | Priority | Effort |
|---|---|---|---|
| R-0.1 | **Re-export v2 completely.** Preferred: `git bundle create tradealpha.bundle --all` from your working copy. Alternative: a plain zip of the working folder without `.venv`, `node_modules`, `rust/target` or `instance/`. Everything below assumes this; item IDs stay valid either way | **P0** | S (you) |
| R-0.2 | Fallback if the full tree is gone: rebuild the missing modules from the `fix1` ancestor at tag `pre-v2-cleanup` (about 53 K LOC, same design, pre-restructure paths) into the v2 package layout, and re-run the suites | P0 (only if R-0.1 is impossible) | L (2–3 wks) |
| R-0.3 | The top folder is misspelled **`TradeAplha`**. Rename it to `TradeAlpha` in the same change that lands the full tree (CI paths, pre-commit `files:` pattern, docs) | P1 | S |

---

## How to read the tables

* **Priority.** **P0** = correctness, safety or blocker (wrong P&L, wrong rules, cannot run). **P1** = high value for
  the use case. **P2** = polish or later.
* **Effort.** **S** ≤ 1 day · **M** 2–4 days · **L** ≥ 1 week (one engineer).
* **Tags.** `[exists→extend]` already built, needs extending · `[missing]` · `[bug]` · `[verify]` regulatory
  fact to confirm against the current NSE/BSE/SEBI circular before it ships. File dates in the upload are Nov 2026;
  rules may have changed, so every rate or date is effective-dated and sourced, never hard-coded.

---

## 1. Trading system: Indian market rules, charges and edge cases

**What exists:**

* Rust `hbt-core` engine: fixed-point paise, next-open fills, pessimistic intrabar handling, MIS square-off
  (15:10) and entry cut-off (14:55), participation cap, corporate actions on positions, time-versioned lot/tick.
* Cost model: STT, stamp, exchange, SEBI, GST and DP, by trade date.
* OMS *(ancestor)*: journal-first, idempotency keys, compare-and-set state machine, restart reconcile.
* 23 guardrails *(ancestor)*: kill switch, collar, fat finger, rate 10/s, loss and drawdown limits, clock offset.
* Staged promotion `signals → shadow → paper → eligible_live → live`, with live drills and stepped caps (ADR-004).

This is a strong core. The enrichments below are about **Indian-market fidelity** and **edge cases**.

### 1A. Charges and the statutory model (`rust/hbt-py/python/hbt/data/statutory_nse.json`, `hbt-core/src/costs.rs`)

The post-Oct-2024 equity and F&O rates in the table look right (STT, stamp, NSE txn 0.00297 %, SEBI ₹10/cr, GST 18 %).
The gaps are history, coverage and exotic legs.

| ID | Enrichment | Evidence / why | Pri | Eff |
|---|---|---|---|---|
| T-1.1 | **Verified, sourced, effective-dated rate tables.** Every row carries a circular reference and `verified_on`; CI fails if `verified: false` on a live profile | File says `"verified": false … UNVERIFIED` (decision D-13 blocks live) | P0 | M |
| T-1.2 | **Full rate history instead of two versions** (2015 and 2024-10-01). NSE txn charges changed several times before 2024. Stamp duty was **state-wise before 2020-07-01** (uniform only under the Indian Stamp Act amendment). Backtests before 2020 are mis-costed today `[verify]` | Only 2 versions per segment | P0 | M |
| T-1.3 | **BSE schedule** (different txn charges by scrip group; Sensex/Bankex options) and **exchange-aware cost selection** per fill | Only `statutory_nse.json` exists; `BSE`/`BFO` are in the calendar | P1 | M |
| T-1.4 | **STT on exercised ITM options** (charged on intrinsic value at settlement, far higher than on premium) and an "ITM at expiry" cost path `[verify]` | Not modelled; classic expiry-day P&L trap | P0 (F&O) | S |
| T-1.5 | **Same-day CNC round trip is taxed as intraday.** Buy and sell the same day in CNC pays intraday STT and no DP charge. Pick the segment **per round trip**, not per backtest | `cost_model(segment=…)` is fixed for a whole run | P0 | M |
| T-1.6 | **Mixed products in one run.** MIS→CNC conversion at close, BTST (sell T+1 before delivery), and per-leg segment for portfolios mixing cash and F&O | One `CostModel` per engine | P1 | M |
| T-1.7 | Missing small legs: NSE **IPFT** charge, clearing charges (broker pass-through), **auto square-off fee** (a broker RMS square-off costs a flat fee plus GST at most discount brokers), call & trade fee, MTF/DPC interest, pledge/unpledge `[verify per broker]` | Not in `brokers.json` schema | P1 | S |
| T-1.8 | **Contract-note rounding parity.** STT rounded to the rupee per contract note; other charges per order or trade depending on broker. Add a reconciliation test against 3 real contract notes per broker | Engine rounds each charge per fill in paise (close, but not contract-note identical) | P1 | S |
| T-1.9 | **Charges breakdown as first-class output.** Per-trade and per-run STT, stamp, txn, SEBI, GST, DP, brokerage, plus "cost drag %" and "break-even move %" per strategy | `Charges` exists per fill; not surfaced per category in reports | P1 | S |
| T-1.10 | **Tariff drift watcher.** A monthly job diffs bundled broker tariffs against a pinned copy of each broker's pricing page and raises "verify tariffs" | Tariffs drift silently | P2 | S |

### 1B. Sessions, calendar and time

| ID | Enrichment | Evidence / why | Pri | Eff |
|---|---|---|---|---|
| T-2.1 | **Official, multi-year holiday calendar** (NSE/BSE/MCX, 2010→next year) with source circular per year and a yearly "next year's list not loaded" alert. The bundled list is 2025-only, flagged *"a SAMPLE"*, and appears to **miss 2025-10-21** (Diwali Laxmi Pujan / Muhurat day) `[verify]` | `market_calendar.json` (ancestor): `version 2025.1-sample`; the dashboard itself shows *"Holiday calendar is a sample list"* | **P0** | M |
| T-2.2 | **Special sessions:** Muhurat trading (about 1 h, evening), Budget-day Saturday sessions (e.g. 2025-02-01), Saturday live/DR-switch sessions (e.g. 2024-01-20, 2024-03-02), exchange-extended sessions after outages (e.g. 2021-02-24 extended close) `[verify]` | `special_sessions` is absent from the calendar file; the rollup and engine anchor on it | P0 | M |
| T-2.3 | **Pre-open call auction (09:00–09:08/09:12).** A market order "at the open" fills at the **pre-open equilibrium price**, and the 09:15 first 1m bar is not the auction. Model an `open_auction` fill for next-open entries and gap days | Engine fills next-open at the first bar's O | P1 | M |
| T-2.4 | **Closing price = NSE official close** (volume-weighted over 15:00–15:30, not the last trade). `fill_timing="close"` on daily bars is optimistic. Add a "post-close session at official close (15:40–16:00)" fill mode for CNC/EOD strategies, and document which close each series carries | `FillTiming::Close` uses the bar close | P0 | S |
| T-2.5 | **Per-broker session rules.** Each broker profile carries its auto square-off time (it differs by broker and segment), the engine square-off must be ≥ N min before it, and cut-off times differ by segment (equity, F&O, CDS, MCX) | `squareoff_at` 15:10 and `no_new_entries_after` 14:55 are global (ADR-001) | P0 | S |
| T-2.6 | **AMO (after-market orders)** for EOD strategies. Decide after close, place an AMO, which goes live at pre-open. Plus AMO windows per broker | Live EOD path has no AMO concept | P1 | M |
| T-2.7 | **Market-wide index circuit breaker** (10/15/20 % NIFTY/SENSEX halts; halt length depends on time of day). The engine must stop fills during a halt and resume in the re-opening call auction `[verify]` | Not modelled | P1 | M |
| T-2.8 | **Periodic call auction for illiquid scrips** (hourly auctions, no continuous trading), and the **IPO/relisting special pre-open** (09:00–10:00 on listing day) | Not modelled; the universe filter should exclude or handle these | P2 | S |
| T-2.9 | **MCX DST rule and seasonal closes** exist (`us_dst`). Extend to CDS/BCD and keep all sessions in one effective-dated table | `seasonal_close` only covers MCX | P2 | S |

### 1C. Products, margin and settlement

| ID | Enrichment | Evidence / why | Pri | Eff |
|---|---|---|---|---|
| T-3.1 | **Leverage bounds that match SEBI peak-margin rules.** Equity MIS is at most 5× (≥ 20 % upfront margin) and stock-specific (VaR+ELM); CNC is 1×; NRML is exchange SPAN+exposure. The core accepts `leverage ≤ 20` for any product `[bug]` | `spec.rs validate(): (1.0..=20.0)`; ADR-003 set MIS 5× only in the Python `ExecConfig` | P0 | S |
| T-3.2 | **Stock-specific MIS margin / eligibility.** Brokers publish per-stock MIS leverage and an MIS-allowed list that changes daily. Import it as PIT reference data and refuse MIS on ineligible scrips in backtest **and** live | Not modelled | P1 | M |
| T-3.3 | **T+1 settlement cash ledger.** Sale proceeds of delivery holdings are not freely withdrawable until settlement; broker rules limit same-day reuse; peak-margin snapshots intraday. Model a `settled / unsettled / blocked` cash split so portfolios cannot recycle cash unrealistically. Add optional **T+0** (beta segment) later `[verify]` | Engine credits `cash += value - fee` immediately | P1 | M |
| T-3.4 | **Short-selling rules.** Retail cash-segment shorts are intraday-only (MIS). A CNC sell without holdings leads to **short delivery → auction penalty**; the live OMS must block a CNC sell above holdings (including holdings pledged or sold earlier today) | `shorts_allowed()` blocks CNC shorts in backtests; the live-side holdings check is not visible in v2 | P0 | S |
| T-3.5 | **Broker auto square-off as an edge case.** If the engine's 15:10 square-off order is rejected, partially filled or the feed is stale, escalate (retry marketable limit → alert → rely on broker RMS and book its fee). Track "positions still open at T-5 min" | Square-off failure path not explicit | P0 | M |
| T-3.6 | **Product conversion** (MIS→CNC at close when a swing signal persists; CNC→MIS) as an explicit intent with its own checks and costs | Not modelled | P2 | M |

### 1D. Instrument and exchange rules

| ID | Enrichment | Evidence / why | Pri | Eff |
|---|---|---|---|---|
| T-4.1 | **Price bands / circuit limits as PIT data.** Per-scrip 2/5/10/20 % bands (revised often), F&O stocks' dynamic operating range, and band-hit detection from the exchange's band file. Current `price_band_pct` is one static number and locks only when H = L | `engine.rs` "static band vs the previous day's close" | P0 | M |
| T-4.2 | **Tick size is price-dependent and changed in 2024.** NSE moved stocks below ₹250 to a ₹0.01 tick (June 2024) and revises price-based ticks for F&O names. The default tick is ₹0.05 for everything. Make tick a function of (date, price band, segment) `[verify]` | `DEFAULT_INSTRUMENT = {"tick": 5}` | P0 | S |
| T-4.3 | **Surveillance lists as PIT filters.** ASM (long/short term), GSM stages, ESM, T2T/BE/BZ series (no intraday netting; delivery compulsory), and IBC/suspension. Block MIS, force 100 % margin, or exclude, consistently in backtest, scanner, signals and OMS | Not present | P0 | M |
| T-4.4 | **F&O ban list** (MWPL > 95 %: no fresh positions, only reduction) applied in live guardrails, not only in backtests | Backtest has `skip_banned` *(ancestor)*; no guardrail id | P1 | S |
| T-4.5 | **Symbol / ISIN identity over time.** Renames, mergers, demergers (e.g. a TATAMOTORS-type demerger), series changes. Key everything by ISIN + effective dates so history, positions and signals follow the security | KB symbols "approximate" per status doc | P1 | M |
| T-4.6 | **Survivorship-free universes.** PIT index membership exists *(ancestor)*. Add delisted securities to the bar store and refuse "current NIFTY 500" universes for historical tests | Status doc lists PIT membership; delisted coverage unclear | P1 | M |

### 1E. Orders, routes and broker capabilities

| ID | Enrichment | Evidence / why | Pri | Eff |
|---|---|---|---|---|
| T-5.1 | **Marketable-limit instead of raw MARKET for engine exits**, priced within the exchange's Limit-Price-Protection range, with re-pricing loops. Market orders are rejected or restricted for illiquid contracts at many brokers `[verify]` | *(ancestor)* `openalgo_route.py`: "engine-managed exits go out as MARKET orders" | P0 | M |
| T-5.2 | **Broker capability matrix** (order types allowed per segment, e.g. SL-M restrictions in F&O; GTT/OCO availability; bracket/cover order deprecation; modify limits; max orders per second; AMO windows) consulted by the OMS before sending | No capability model | P1 | M |
| T-5.3 | **Freeze-quantity slicing** for F&O (orders above the exchange freeze qty are rejected) and optional iceberg / TWAP slicing for large cash orders | Not modelled | P1 (F&O) | M |
| T-5.4 | **SEBI retail-algo framework compliance.** Exchange algo-ID tagging above the order-rate threshold, static-IP whitelisting for API orders, broker OAuth session rules; audit evidence in the live gate `[verify current effective dates]` | G-PRE-7 uses the 10 orders/s threshold, but there is no algo-ID or static-IP check | P0 (live) | M |
| T-5.5 | **Daily broker token lifecycle.** Tokens expire daily; pre-open login reminder, TOTP flow, block the live start until the session is valid, and no auto-trading on an unverified session after a restart | Auth-blocked metric exists; make it a hard pre-open gate | P1 | S |
| T-5.6 | **GTT/OCO protective orders at the broker for CNC swing positions** (an overnight stop cannot rest as an intraday SL order) | ADR-004 rests the protective stop at the broker intraday only | P1 | M |

### 1F. F&O specifics (in scope if F&O is traded; `lab/fno/*` exists in the ancestor)

| ID | Enrichment | Pri | Eff |
|---|---|---|---|
| T-6.1 | Effective-dated **expiry calendar** (weekly/monthly; the expiry weekday changed by exchange in 2025; only one benchmark weekly per exchange since the 2024 SEBI F&O measures) `[verify]` | P0 (F&O) | M |
| T-6.2 | **SEBI 2024 F&O measures:** higher minimum contract value (lot revisions), upfront premium collection, no calendar-spread benefit on expiry day, +2 % ELM on short options on expiry day, intraday position-limit monitoring `[verify]` | P1 | M |
| T-6.3 | **Physical settlement of stock F&O** (ITM stock options and futures held into expiry result in delivery) and broker-forced pre-expiry square-off windows | P0 (F&O) | S |
| T-6.4 | **Continuous futures with roll rules** (volume/OI switch, fixed days before expiry) and roll costs; ancestor `fno/continuous.py` exists, expose and test it | P1 | S |
| T-6.5 | **Margin from SPAN files** (exchange SPAN + exposure) for backtests; live already asks the broker (ADR-004 "margin is asked") | P2 | L |

### 1G. Corporate actions

| ID | Enrichment | Pri | Eff |
|---|---|---|---|
| T-7.1 | Cover **rights issues** (RE entitlements), **demergers** (cost-basis split, new ISIN), **buybacks** (tender offers; tax treatment changed from 2024-10-01 `[verify]`), **mergers/amalgamations** (swap ratio). The engine handles split, bonus, consolidation and cash dividend | P1 | M |
| T-7.2 | **Record-date vs ex-date** under T+1 (ex-date = record date). Add a PIT check that adjustment factors use ex-dates from the exchange's CA file | P1 | S |
| T-7.3 | Dividend **TDS** on credited dividends (net cash in the ledger; gross in tax) `[verify thresholds]` | P2 | S |

### 1H. Execution-lifecycle edge cases (live/paper)

| ID | Edge case → required behaviour | Pri | Eff |
|---|---|---|---|
| T-8.1 | **Mid-session restart.** Reconcile broker positions and orders **before** any strategy step; rebuild brackets; refuse new entries until reconciled (the OMS `reconcile()` exists; make it a blocking startup gate with a UI state) | P0 | S |
| T-8.2 | **Gap opening through stop.** Exit at the open (the engine does this). Live: marketable limit with protection, not a stop order that may not trigger | P0 | S |
| T-8.3 | **Circuit-locked position** (cannot exit; upper circuit for shorts, lower for longs). Alert, retry each minute, mark unrealised P&L at the locked price, report "trapped" exposure | P0 | S |
| T-8.4 | **Trading halt / exchange outage / extended session** (T-2.2, T-2.7). Session rules read the *actual* session of the day, not 15:30 | P1 | S |
| T-8.5 | **Partial fill at square-off, or a rejected square-off order** (T-3.5) | P0 | S |
| T-8.6 | **Late or missing bar at decision time** (finality). Exists via the completeness gate; add explicit "skip vs. decide on stale" policy per deployment, surfaced in UI | P1 | S |
| T-8.7 | **Duplicate-order protection across processes** (idempotency exists). Add a broker-side tag check (`strategy` tag plus client order id) in reconcile | P1 | S |
| T-8.8 | **Corporate action overnight on a live CNC position** (qty and stops must be adjusted before the open; broker shows new qty). Reconcile expects the CA | P1 | S |
| T-8.9 | **Holiday announced at short notice.** Calendar override from the UI with audit; scheduler re-plans | P1 | S |
| T-8.10 | **Kill switch semantics.** Define "flatten vs freeze" per product (MIS flatten, CNC freeze by default), and test both in drills | P0 | S |

### 1I. Risk and portfolio (from the ancestor status doc, still open)

| ID | Enrichment | Pri | Eff |
|---|---|---|---|
| T-9.1 | **Regime sizing is computed but never applied** (`lab/regime.py` multipliers unused). Apply them in the simulator and the risk guard so backtest = live | P1 | M |
| T-9.2 | **Book-level risk:** risk-% per trade, sector and business-group caps, correlation-aware exposure, cross-deployment netting, **drawdown circuit breaker** (e.g. 1.5× backtest max DD), **decay kill switch** (CUSUM of live vs backtest) | P0 | M |
| T-9.3 | **Capital allocation across strategies** (risk budgets, vol targeting, capacity caps) | P1 | L |
| T-9.4 | **Event-risk filter:** results day, board meetings, ex-dates, F&O expiry, Budget/RBI policy days, index rebalances (block or size down) | P1 | S |

### 1J. Tax (after-tax reporting)

| ID | Enrichment | Pri | Eff |
|---|---|---|---|
| T-10.1 | Delivery STCG/LTCG is modelled correctly for listed equity (`portfolio/tax.py`: 15→20 %, 10→12.5 %, ₹1.25 L from 2024-07-23). **Add intraday = speculative business income and F&O = non-speculative business income** (slab rates, configurable), so MIS and F&O strategies show after-tax results too | P1 | S |
| T-10.2 | Grandfathering (31-Jan-2018 FMV), surcharge and cess as configurable options; FY P&L statement export (Apr–Mar) matching the broker's tax P&L | P2 | M |

---

## 2. Backtesting: charts, candlesticks, signals, ratios and trends

**What exists:**

* Rust engine plus the Strategy IR (about 90 ops: arithmetic, rolling stats, SMA/EMA/WMA/DEMA/TEMA/HMA,
  RSI/MACD/BBands/ATR/ADX/Stoch/MFI/CCI/Keltner/Donchian/OBV/VWAP, session ops, prior-day OHLC, position state).
* pandas feature library (`features/core.py`: adds Supertrend, PSAR, CPR, Camarilla, RS ratio, Mansfield RS, beta,
  52-week distance, RVOL by time of day, opening range).
* Research statistics: DSR, PBO/CSCV, walk-forward, bootstrap.
* Look-ahead harness; backtest pages with candle + trade markers, equity and drawdown *(ancestor UI)*.

### 2A. Engine fidelity and metric correctness

| ID | Enrichment | Evidence / why | Pri | Eff |
|---|---|---|---|---|
| B-1.1 | **Risk-free-adjusted Sharpe/Sortino.** Metrics use a 0 % risk-free rate; India's T-bill yield (about 6–7 %) materially changes verdicts. Use an effective-dated 91-day T-bill series (configurable) for excess returns | `metrics.rs` has no rf term | P0 | S |
| B-1.2 | **Benchmark-relative metrics:** alpha, beta, information ratio, tracking error, up/down capture vs **NIFTY 50 TRI** (total return, not price index) or a chosen benchmark; excess-return equity curve | No benchmark in core metrics | P0 | M |
| B-1.3 | **Daily-resampled statistics for intraday strategies.** Annualising per-bar returns of 5m bars inflates Sharpe through intraday autocorrelation and overnight gaps | `periods_per_year` × per-bar SR | P1 | S |
| B-1.4 | **One kernel.** Two simulators exist (Python `pysim` ExecConfig and Rust `hbt`) reconciled by ADR-001/003 with 2 documented deliberate differences. Plan the retirement of `pysim`, or keep it strictly as a test oracle, so a verdict never depends on the kernel | ADR-003 §2 | P1 | L |
| B-1.5 | Fidelity items from §1 apply to backtests: T-1.5 (same-day CNC), T-2.3 (pre-open auction fill), T-2.4 (official close), T-3.1 (leverage), T-3.3 (T+1 cash), T-4.1–4.3 (bands, ticks, surveillance) | | P0 | – |
| B-1.6 | **Slippage calibration from paper/live fills.** Fit the bps buckets per liquidity tier from the realised fills that ADR-004 already journals; store the calibration version in the run manifest | Slippage buckets are fixed constants (ADR-001) | P1 | M |
| B-1.7 | **Weekly/monthly base-bar counts use 5 and 23** for warm-up (`ops.rs base_bars_per`). Derive them from the calendar so warm-up is exact across holiday-heavy months | `"W" => 5, "M" => 23` | P2 | S |

### 2B. Signal vocabulary: candlesticks, chart structure, trends, ratios

Add as **causal IR ops** (Rust, so they are deployable) with pandas references and corrupt-the-future tests, like the
existing features.

| ID | Enrichment | Pri | Eff |
|---|---|---|---|
| B-2.1 | **Candlestick patterns:** doji, hammer / hanging man, shooting star, engulfing, harami, piercing / dark cloud, morning / evening star, three soldiers / crows, marubozu, inside / outside bar, NR4 / NR7. Parameterised body / wick thresholds (ATR-relative); TA-Lib parity tests (TA-Lib is already a dev oracle) | P1 | M |
| B-2.2 | **Chart structure:** causal swing highs/lows (confirmed after k bars, never repainting), HH/HL/LH/LL trend state, break of structure, support/resistance zones from swing clusters, breakout / breakdown with volume confirmation, range contraction (squeeze exists), gap classification (common / breakaway / exhaustion), gap-fill | P1 | L |
| B-2.3 | **Trend / regime ops in IR:** Supertrend, PSAR, Heikin-Ashi OHLC, Ichimoku, linear-regression channel, `trend_score`, `ma_stack` (already in pandas `features/core.py`, not in IR) | P1 | M |
| B-2.4 | **Pivots in IR:** CPR (width as a regime), Camarilla, classic / Fibonacci pivots (pandas only today) | P1 | S |
| B-2.5 | **Cross-sectional ops:** rank / percentile / z-score across the universe per bar, sector-relative strength, top-N selection (pandas `rank_cross_section` exists; IR has none) for momentum / rotation strategies | P1 | M |
| B-2.6 | **India-specific market data as IR columns:** **delivery %** (bhavcopy), futures **OI build-up class** (long build-up / short covering / …), **PCR**, max pain, IV rank (when the chain history exists), **India VIX** regime, **FII/DII flows**, market breadth (A/D ratio, % above 200-DMA, new highs/lows) | P1 | L |
| B-2.7 | **Fundamental ratios, point in time:** P/E, P/B, EV/EBITDA, ROE, ROCE, debt/equity, interest cover, EPS / revenue growth (YoY, QoQ), margins, promoter holding and **pledge %**, FII/DII holding change. Availability date = filing date (XBRL mapping is marked "VERIFY" in the ancestor) | P1 | L |
| B-2.8 | **Multi-timeframe in one rule** exists (`tf` on Ohlc ops). Add an explicit **higher-timeframe finality** check (no use of the still-forming weekly bar) in the IR compiler's look-ahead analysis | P0 | S |
| B-2.9 | **Calendar/seasonality features:** expiry-week, days-to-expiry, month-end / FY-end (March), results season, Budget week, Diwali / Muhurat, Monday / Friday effects | P2 | S |

### 2C. Reports and visualisation (backtest tearsheet)

| ID | Enrichment | Pri | Eff |
|---|---|---|---|
| B-3.1 | **Tearsheet:** equity vs benchmark (log scale option), underwater curve, **monthly-returns heatmap** (FY and calendar year), rolling Sharpe / vol / beta, return distribution, exposure over time, turnover, cost drag by charge type (T-1.9), reject reasons histogram (the engine already counts 10 reject kinds) | P1 | M |
| B-3.2 | **Trade analytics:** MAE/MFE scatter, holding-period histogram, P&L by weekday / hour / month / regime / sector, win/loss streaks, R-multiple distribution, top-N winners/losers with click-to-chart | P1 | M |
| B-3.3 | **Strategy-aware price chart:** auto-plot the IR's own series (the MAs / bands / levels the rule uses), entry/exit markers with the reason (stop / target / trail / square-off / forced), stop and target lines per trade, candlestick-pattern and structure annotations (B-2.1/2.2), corporate-action and event markers, regime-shaded background | P1 | M |
| B-3.4 | **Decision trace per bar** ("why did it enter here?"): the IR `explain.rs` exists. Surface the evaluated condition tree on click | P1 | S |
| B-3.5 | **Run comparison:** overlay equities, metric diff table, parameter diff, spec/cost diff (spec hashes exist) | P2 | S |
| B-3.6 | **Exportable report** (HTML/PDF) with manifest (data snapshot hash, spec hash, cost version, code SHA) for reproducibility | P2 | S |

### 2D. Validation and robustness

| ID | Enrichment | Pri | Eff |
|---|---|---|---|
| B-4.1 | **Real-data bring-up.** The ancestor status says *"Nothing has been run on real market data yet"*. Run the experiment backlog E1–E17 on NSE data and publish the pass/fail table against the pre-registered reject rules | **P0** | M (+compute) |
| B-4.2 | **Regime-split results** (bull / bear / sideways by NIFTY trend + VIX) and crisis windows (2008, 2013 taper, 2016 demonetisation, 2020 COVID crash, 2022 rate cycle) as standard report sections | P1 | S |
| B-4.3 | **Capacity analysis:** return vs capital curve using the participation cap and the slippage model | P1 | S |
| B-4.4 | **Parameter-stability heatmaps** for sweeps (neighbourhood robustness), alongside the existing PBO/DSR | P2 | S |

---

## 3. Market data and roll-up accuracy

**What exists:**

* Hybrid roll-up policy: D from the broker; 3m..1h from broker candles or from 1m anchored at the session open; W and M
  derived from D; provenance labels.
* Roll-up audit comparing broker candles with a roll-up of 1m.
* Vectorised normalisation; revision tracking and hashing.
* Session-grid quarantine at ingest and a per-day session ledger *(ancestor README)*; dataset snapshots.

| ID | Enrichment | Evidence / why | Pri | Eff |
|---|---|---|---|---|
| D-1 | **Naive ISO timestamps are parsed as UTC.** `_to_epoch` uses `pd.to_datetime(…, utc=True)`. A provider returning `"2024-01-02 09:15:00"` (naive IST) shifts every bar by 5h30m (verified by running the function: `'2024-01-02 09:15:00'` → 14:45 IST), and the grid quarantine then drops them as off-session (silent data loss). OpenAlgo's history API returns epoch seconds, so the exposure is any adapter, CSV import or feed that yields naive date-times. Make the provider timezone explicit per adapter; reject naive strings unless declared | `marketdata/providers/normalize.py` `[bug]` | **P0** | S |
| D-2 | **Bar-label convention detection** (some feeds stamp 1m bars by close time, not open time): a per-provider offset check in the roll-up audit, and a normalisation rule | Not checked | P0 | S |
| D-3 | **Bucket completeness metadata:** `n_1m` (minutes present / expected) per rolled bar, `is_final` (the bucket has ended and finality elapsed), and a `partial_session_bucket` flag (e.g. 1h 15:15–15:30 = 15 min). Strategies and charts must not treat an in-progress or short bucket as a full bar | `rollup_frame` returns OHLCV only | P0 | M |
| D-4 | **The roll-up enforces the session end itself** (bars ≥ session close excluded) instead of relying on ingest quarantine. The roll-up *audit* feeds it un-quarantined downloads | `rollup_frame` filters only `ts >= open` | P1 | S |
| D-5 | **Weekly/monthly roll-up semantics:** trading-week buckets (holiday-aware), label = first trading day (state it), partial-week flag for the current week, **F&O expiry-month periods** as an alternative bucket | W/M code lost in the capture; verify against these rules when restored | P1 | S |
| D-6 | **Late-data invalidation cascade.** When a 1m bar is revised or backfilled, recompute the derived 3m..1h/W/M bars, cached features and any signal that used them; record the revision in the ledger (`revisions.py` exists) and raise a "signal changed after the fact" audit event | No cascade visible | P0 | M |
| D-7 | **Golden-source reconciliation with the NSE bhavcopy** (official OHLC, close, volume, delivery qty) nightly. Broker D bars that differ beyond tolerance are flagged; bhavcopy becomes the tie-breaker for daily bars | Bhavcopy fields are used for delivery % *(ancestor)*; no OHLC reconciliation | P0 | M |
| D-8 | **Adjusted vs raw series contract:** signals on adjusted prices, execution on raw (design §8.4). Roll-ups and W/M must be built on raw and adjusted **separately** with volume adjusted inversely; the chart toggle shows which one is displayed; dividends optional (total-return series) | Confirm in the restored code | P1 | M |
| D-9 | **Outlier / bad-tick detection** at ingest (spike vs neighbours with low volume, price outside the day's band, volume 100× median). Quarantine with reason, like the grid | `normalize_bars` checks only OHLC consistency | P1 | S |
| D-10 | **Zero-volume vs missing minutes:** distinguish "no trade" (carry-forward bar, volume 0) from "not delivered" in the ledger, per provider, so gaps are not masked | | P1 | S |
| D-11 | **Index bars** (no volume) and **futures OI** (updated at intervals, `last` is correct but stale): per-instrument-type roll-up rules and tests | | P2 | S |
| D-12 | **Nightly roll-up audit job** on a rotating sample, trending the match % on the Data Health page; alert on drop | Audit exists as an on-demand job | P1 | S |
| D-13 | **Tick-based 1m builder** (WebSocket) vs REST 1m: reconcile both and prefer REST for history; report the disagreement rate | `stream_*` metrics exist *(ancestor)* | P2 | M |

---

## 4. Idea and signal generation pipelines

**What exists *(ancestor + v2 names)*:**

* **Pipeline A:** themes, knowledge graph (`india_theme_graph.json`), macro, corporate, fundamental, seasonal; placebo
  tests, BH-FDR.
* **Pipeline B:** evolved technical rules (genome), walk-forward fitness, trial ledger → DSR, PBO.
* **Track T:** hand-designed strategies E1–E17 deployed as live signals with Telegram/webhook delivery.
* v2 generator names: `genome`, `ir_templates`, `llm`, `seasonal`, `thematic`, `scanners/intraday`; `IdeaSpec` v1
  with HMAC-signed intake.

| ID | Enrichment | Evidence / why | Pri | Eff |
|---|---|---|---|---|
| S-1 | **One Signal object and one inbox.** Idea alerts and deployment signals are different tables and pages (status doc T-R5). Define `Signal v1`: instrument, side, entry / stop / target, size, validity window, horizon, confidence (calibrated), rationale (feature snapshot + IR explain), regime, data-as-of, lineage (idea → strategy version → deployment → signal → order → fill → outcome) | Two streams | P0 | M |
| S-2 | **Real-data bring-up for Pipelines A and B** (XBRL mapping "VERIFY", KB symbols approximate, no cycle run on real data) | Status doc A-R0 / T-R0 | P0 | M |
| S-3 | **EOD swing setup scanner** (after the post-close download): ranked setups across the liquid universe with entry / stop / target / size, and one daily digest | Status doc T-R3 (missing) | P1 | M |
| S-4 | **Pre-market scanner with India context:** GIFT Nifty gap, US close, crude / USDINR / US10Y moves, FII/DII provisional flows, F&O ban list, results calendar, corporate announcements overnight; then 09:20/09:31 confirmation (intraday scanner exists) | Partly exists | P1 | M |
| S-5 | **Smart-money templates:** promoter open-market buys, insider trades, SAST crossings, bulk/block deals by marquee investors, FPI flow shocks. Data is archived but unused by templates | Status doc A-R2 | P1 | M |
| S-6 | **Conflict resolution and de-duplication** across strategies (same symbol, opposite sides, overlapping ideas); portfolio-level ranking and a daily cap on alerts | | P1 | S |
| S-7 | **Signal hygiene filters**, applied identically in backtest and live: liquidity / capacity, surveillance (T-4.3), F&O ban, event days (T-9.4), circuit proximity, MIS eligibility | | P0 | S |
| S-8 | **Confidence calibration and forward tracking:** each signal's predicted hit rate / expected R vs realised (the forward loop exists for ideas; extend to all signals), reliability plots, decay detection (CUSUM) → auto-demote | | P1 | M |
| S-9 | **Event ideas need executable strategies** (catalyst-window, lead-lag, macro / corporate drift can only be traded by hand) → `idea_theme` / `idea_event` IR templates | Status doc A-R3 | P1 | M |
| S-10 | **"Promote idea" in one action:** walk-forward group + sweep (PBO) + freeze + deploy to `signals` with lineage | Status doc B-R7 | P1 | S |
| S-11 | **LLM generator guardrails:** model / prompt version in the manifest, token and cost caps, training-cutoff contamination rule (forward-only validation), deterministic seeds, human approval before deployment, offline/local model option | `ideas/generators/llm.py` exists (content lost) | P1 | S |
| S-12 | **Explainability in delivery:** each Telegram/webhook message carries the rationale, chart snapshot link, risk (₹ at stop, % of capital), validity and lineage ID | | P2 | S |
| S-13 | **Missing strategy families:** OI build-up (S3), sector rotation (S5), index inclusion (needs an index-change feed), VWAP pullback (I2), gap-and-go / gap-fill (I3), RS leaders (I4); **start collecting intraday OI now** (months of history needed) | Status doc T-R4 | P1 | L |

---

## 5. Monitoring, logging and alerting

**What exists:**

* A hand-rolled Prometheus `/metrics` endpoint with about 30 gauges (threads, jobs, deployments, orders, stream,
  kill switch, clock offset, reconciliations).
* 8 alert rules for the trader node (`deploy/prometheus-alerts.yml`).
* Tamper-evident audit log (`platform/audit/security.py`) and a health page.

**Gap:** no central logging configuration *(ancestor: no `basicConfig`/`dictConfig` anywhere)*; only about 121 log
calls in 53 K LOC; stray `print()` calls in 10+ modules.

| ID | Enrichment | Pri | Eff |
|---|---|---|---|
| M-1 | **Central logging config** (`platform/logging.py`, `dictConfig`): console (human) + rotating files per component (`web`, `scheduler`, `trader`, `research`, `jobs`, `audit`), size/time rotation and retention, JSON format option, per-module levels from settings; replace all `print()` | P0 | S |
| M-2 | **Correlation IDs** via `contextvars`: `request_id`, `job_id`, `run_id`, `deployment_id`, `session_id`, `order_id`, `signal_id` stamped on every log line, so one order can be traced from signal to fill | P0 | M |
| M-3 | **Secret redaction filter** (API keys, TOTP seeds, access tokens, webhook secrets, phone numbers) on all handlers + a test that fails on leakage. The upstream history had leaked keys (`scripts/purge_leaked_keys.sh`) | P0 | S |
| M-4 | **Order lifecycle event log** (append-only): intent → validated → sent → ack → fills → terminal, with guardrail decisions and broker payloads (redacted); queryable in UI | P0 | M |
| M-5 | **Trading metrics:** realised / unrealised P&L, day P&L, charges today, gross / net exposure, margin used / available, open positions, **order ack latency** histogram, rejects by reason, square-off completion time, positions open at T-5 min, slippage vs decision price | P0 | M |
| M-6 | **Data-pipeline metrics:** freshness per series (age of the last final bar), ledger completeness %, roll-up audit match %, bhavcopy diffs, provider error rate / latency / rate-limit headroom, job durations, scheduler misfires | P1 | S |
| M-7 | **Research-node metrics and alerts** (queue depth, failed runs, idea-cycle duration, LLM spend); today alerts exist only for the trader node | P1 | S |
| M-8 | **Market-hours-aware alerting** (no pages for "feed down" on holidays or after close; pre-open checks page only on trading days) and **Alertmanager → Telegram / email** routing with a runbook link per alert | P1 | S |
| M-9 | **Grafana dashboards as code** (trader ops, data pipeline, research) shipped in `deploy/grafana/` | P2 | S |
| M-10 | **Daily ops journal** (one row per trading day): pre-open checks, broker login, first bar received, square-off done, EOD reconciliation, parity, bhavcopy reconciliation. Green / red timeline on the dashboard | P1 | M |
| M-11 | **Health model:** `/healthz` (liveness) vs `/readyz` (readiness: DB, migrations, OpenAlgo, broker session, NTP, disk, Rust wheel ABI, calendar covers today) with component matrix in UI | P1 | S |
| M-12 | **In-app log viewer** (tail / filter by correlation ID / level) and an optional error tracker (self-hosted GlitchTip / Sentry, off by default) | P2 | M |
| M-13 | **Replace the hand-rolled exposition with `prometheus_client`** (histograms, correct escaping, process metrics), or keep it and add exposition-format tests | P2 | S |

---

## 6. Unified startup and operations

**Today there are many entry points:** `run.py` (lost), `start.bat` (Windows; still says "Starting Historify";
installs `requirements.txt`, which is lost), `apps/{serve,research,trader}.py` (lost), the CLI `interfaces/cli/main.py`
(lost), systemd units `tradealpha-{research,trader}.service` (lost), and `scripts/{bootstrap,setup_sandbox,sandbox_demo,run_tests}.sh`
(lost; `verify.sh` still calls `bootstrap.sh`). Environment variables are still `HISTORIFY_*`; metrics are
`historify_*`.

| ID | Enrichment | Pri | Eff |
|---|---|---|---|
| O-1 | **One entry point:** `tradealpha` console script (pyproject `[project.scripts]`) with `init` (instance dir, secrets, DB, sample settings), `doctor` (preflight, see O-3), `up [--role all|research|trader] [--no-web]`, `down`, `status`, `migrate`, `logs`. Everything else (start scripts, systemd, Docker) calls it | P0 | M |
| O-2 | **Thin cross-platform launchers** `start.sh` / `start.ps1` / `start.bat` that create the venv, install the locked deps + the `hbt-core` wheel, and exec `tradealpha up`. Plus a `Makefile`/`justfile` (`make dev`, `make test`, `make verify`, `make ui`) | P0 | S |
| O-3 | **Preflight (`doctor`) with clear fixes:** Python ≥ 3.12, deps match the lock, **Rust wheel present and ABI-compatible**, Alembic at head (auto-migrate with backup on confirm), instance dir writable, secrets backend, OpenAlgo reachable, **broker session valid**, **calendar covers today and next 30 days and is verified**, **tariffs verified** (live role), NTP offset < 1 s (trader), disk free, port free, TZ sanity | P0 | M |
| O-4 | **Process supervision in one process tree:** web, scheduler, job workers, stream, strategy host, started in dependency order with readiness waits; graceful SIGTERM (stop new entries, keep protective orders at the broker, flush journals); single-instance lock per role (`platform/runtime/singleton`) | P0 | M |
| O-5 | **Late-start policy** (started mid-session): backfill bars to now, reconcile broker state (T-8.1), then either resume deployments or stay paused until confirmed (configurable per stage; live is always "confirm") | P0 | S |
| O-6 | **One configuration story:** precedence `defaults < config file < env < settings DB (UI)`, documented; `tradealpha config show` (secrets masked); `.env.sample` regenerated from the settings registry (a `tests/repo/test_settings_audit.py` exists) | P1 | S |
| O-7 | **Rename `HISTORIFY_*` → `TRADEALPHA_*`** with aliases and a deprecation warning for one release; metrics `historify_*` → `tradealpha_*` with a recording-rule bridge in Prometheus | P1 | S |
| O-8 | **Docker Compose** (`research`, `trader`, optional `prometheus` / `grafana`) with volumes for `instance/`, health checks wired to `/readyz`, non-root user; systemd units regenerated from the same CLI | P1 | M |
| O-9 | **Scheduled daily runbook** driven by the market calendar: 08:30 doctor + login reminder → 09:00 pre-open checks → session → 15:10 square-off verification → 15:45 post-close download → 16:30 EOD reconciliation → 17:30 parity + daily report → weekly integrity audit. All visible in M-10 | P1 | S |
| O-10 | **Backup/restore**: nightly SQLite online backup + DuckDB/Parquet snapshot, retention, `tradealpha restore`, and a restore drill in the live gate | P1 | S |
| O-11 | **Demo / sandbox mode**: `tradealpha up --demo` with a mock OpenAlgo (the test mock provider and `FakeOpenAlgo` exist) and seeded synthetic data, so the UI can be explored with no broker | P2 | S |

---

## 7. UI: polish and structure

**What exists:** Flask + Jinja page shells, Alpine.js (CSP build), Tailwind + daisyUI, Lightweight Charts, Lucide
icons, Inter / JetBrains Mono, light and dark themes, strict CSP, no CDN. The data pages (Dashboard, Watchlists, Charts,
Data Manager, Jobs, Scheduler, Import/Export, Settings) are polished. Indian digit grouping (1,20,750) and IST are used
throughout. The trading / research pages (Backtests, Lab runs / compare / paper / readiness / research / strategies,
Ideas, Signals, Portfolios, Intel, Options, Scanner, Feeds, Refdata, Health) were added over several rounds. That gives
**20+ top-level pages from two eras**, branded "Historify · market data manager".

| ID | Enrichment | Pri | Eff |
|---|---|---|---|
| U-1 | **Information architecture:** regroup navigation into **Trade** (Today, Positions & Orders, Signals, Deployments) · **Research** (Ideas, Backtests, Strategies, Portfolios, Scanner) · **Markets** (Charts, Watchlists, Intel, Options/F&O) · **Data** (Manager, Health, Jobs, Scheduler, Import/Export, Reference data, Feeds) · **System** (Settings, Logs, Audit, Ops journal). Merge duplicate pages (Backtests vs Lab runs; two metric vocabularies: `total_return_pct` vs `total_return`) | P0 | M |
| U-2 | **Global status bar:** exchange session state (pre-open / open / closing / post-close / closed / **holiday** / **Muhurat**), IST clock with countdown to square-off / close, NIFTY · BANKNIFTY · India VIX ticker, broker session, OpenAlgo mode, **mode badge PAPER / LIVE** (LIVE uses a distinct colour frame), and an always-visible **kill switch** (typed confirmation) | P0 | M |
| U-3 | **Trading cockpit ("Today"):** positions with live MTM, day P&L, realised / unrealised, **charges accrued today**, margin used / available, order book with lifecycle states and guardrail decisions, square-off countdown, per-deployment pause / resume / flatten, signal inbox (S-1) | P0 | L |
| U-4 | **Backtest tearsheet and strategy-aware charts** (B-3.1–B-3.4) as the standard run page | P1 | L |
| U-5 | **Charts:** Supertrend / VWAP / Bollinger / pivots-CPR / Ichimoku, Heikin-Ashi, pattern annotations, multi-pane sync, symbol compare vs NIFTY, **raw / adjusted toggle**, corporate-action and results markers, delivery-% and OI panes, volume profile, saved layouts | P1 | M |
| U-6 | **Design system:** documented tokens, shared components (KPI card, sortable / virtualised table with sticky header, drawer, stepper, typed-confirm dialog, toast), number formatting helpers (₹, **lakh / crore toggle**, %, bps, signed colours), consistent empty / loading (skeleton) / error states, page-level help | P1 | M |
| U-7 | **Live updates via SSE** (positions, orders, jobs, bar feed) instead of polling; visible "stale" indicators when a stream stops | P1 | M |
| U-8 | **Branding:** "Historify" → "TradeAlpha" (titles, logo, favicon, footer, emails / Telegram templates); retire `historify_*.svg/png` | P1 | S |
| U-9 | **Accessibility and responsiveness:** WCAG AA contrast (red / green plus shape or icon for colour-blind users), keyboard navigation (command palette exists: Ctrl-K), ARIA on custom widgets; tablet layout for monitoring, read-only mobile view of Today | P2 | M |
| U-10 | **Onboarding flow:** first-run wizard (OpenAlgo connect → broker profile and tariffs → calendar import → first watchlist and backfill → demo backtest) | P2 | M |
| U-11 | **Visual regression tests** (Playwright screenshots exist in CI design) for every page in both themes | P2 | S |

---

## 8. README and documentation

The README on disk is the **v1 "Historify" data-manager README**. It links `design/phase4-screens/…` and
`docs/metrics/PHASE_3_4_RESULTS.md`, which have moved. The v2 README (39 KB) was not captured.

| ID | Enrichment | Pri | Eff |
|---|---|---|---|
| W-1 | **New in-depth README:** what TradeAlpha is (and is not: not investment advice, SEBI disclaimer); architecture diagram (bounded contexts, research / trader nodes); feature tour with screenshots; **quick start in one command** (O-1/O-2); configuration reference; **India market model** (sessions, products, charges with effective dates and verification status, corporate actions, surveillance); the **daily IST timeline**; backtesting guide; ideas and signals guide; promotion stages and safety model; monitoring; troubleshooting / FAQ; glossary (MIS / CNC / NRML, STT, ASM / GSM, T2T, MWPL, LPP, …); licence (AGPL-3.0); contributing | P0 | M |
| W-2 | `docs/` index: user guides (`docs/user`), operator runbooks, ADRs (restore 005–017), architecture, archive. Fix broken links; add a link checker to CI | P1 | S |
| W-3 | `CHANGELOG.md` restart from the v2 baseline; **ADR for every P0 rule change in §1** (rules 3 of `.windsurf/rules` require it for execution / risk) | P1 | S |

---

## 9. Cross-cutting

| ID | Enrichment | Pri | Eff |
|---|---|---|---|
| X-1 | **Restore the test suites and CI** with the full tree (R-0.1): `tests/invariants`, `tools/check_boundaries.py`, `.github/workflows/ci.yml` (referenced by `.windsurf/rules` but not captured) | P0 | S |
| X-2 | **Golden tests for India rules:** contract-note parity (T-1.8), session edge days (Muhurat, Budget Saturday, outage extension), ASM / T2T refusal, circuit-lock exit, short-delivery block, same-day CNC costing; each added as a regression test before the rule lands | P0 | M |
| X-3 | **Security hygiene:** confirm the leaked upstream keys were rotated; gitleaks is already in pre-commit. Stop committing tool-local files (`.claude/settings.local.json` grants `Bash(rm:*)`); add to `.gitignore` | P1 | S |
| X-4 | **Reference-data freshness SLOs:** calendar, tariffs, statutory rates, lot / tick / freeze, bands, surveillance lists, F&O ban list; each with a "last refreshed" time and an alert when stale | P1 | S |

---

## 10. Summary by component

| Component | P0 | P1 | P2 | Items | Biggest risks today |
|---|---|---|---|---|---|
| 0 Baseline | 2 (one is a fallback) | 1 | – | 3 | Tree incomplete: cannot run |
| 1 Trading system (India rules) | 24 | 30 | 7 | 61 | Sample holiday calendar; leverage up to 20×; static price band / ₹0.05 tick; MARKET exits; same-day CNC mis-costed; surveillance lists ignored |
| 2 Backtesting | 5 | 16 | 5 | 26 | 0 % risk-free Sharpe; no benchmark; nothing run on real data |
| 3 Data / roll-up | 5 | 6 | 2 | 13 | Naive-timestamp UTC bug; no completeness / finality flags; no revision cascade |
| 4 Ideas / signals | 3 | 9 | 1 | 13 | Two signal streams; no real-data cycle; filters not shared with live |
| 5 Monitoring / logging | 5 | 5 | 3 | 13 | No logging config, no correlation IDs, no P&L metrics |
| 6 Startup / ops | 5 | 5 | 1 | 11 | Many entry points, all lost; Historify naming |
| 7 UI | 3 | 5 | 3 | 11 | 20+ flat pages from two eras; no trading cockpit or global market state |
| 8 Docs | 1 | 2 | – | 3 | README is v1 |
| 9 Cross-cutting | 2 | 2 | – | 4 | Tests and CI missing from the capture |
| **Total** | **55** | **81** | **22** | **158** | |

---

## 11. Decisions needed from you

1. **R-0.1:** can you provide the full v2 working copy (git bundle or zip)? If not, approve R-0.2 (rebuild from the
   ancestor).
2. **Scope of instruments:** cash equity only, or cash + F&O (enables §1F, T-5.3, B-2.6 OI / PCR)? MCX / CDS in
   scope?
3. **Brokers:** Angel One only (docs exist), or also Zerodha / others? This sizes the capability matrix (T-5.2) and
   tariff work.
4. **Benchmark and risk-free:** NIFTY 50 TRI plus the 91-day T-bill (recommended), or another benchmark (NIFTY 500
   TRI, sector index)?
5. **Kill-switch semantics** (T-8.10): MIS flatten / CNC freeze by default. OK?
6. **Risk defaults** (T-9.2): 0.75 % risk per trade, ≤ 2 positions per sector, ≤ 1 per business group, daily loss
   stop −2R, pause at 1.5× backtest max drawdown (the ancestor proposed these).
7. **Deployment:** single machine (`--role all`), or the two-node research / trader split (ADR-004)? Docker
   acceptable?
8. **Rename:** folder `TradeAplha` → `TradeAlpha`, and all `HISTORIFY_*` → `TRADEALPHA_*` (with aliases)?

---

## 12. Proposed order of work (after R-0.1)

| Phase | Content | Why first |
|---|---|---|
| **1. Baseline green** | R-0.1/0.3, X-1, O-1/O-2/O-3 (one entry point + doctor), M-1/M-3 (logging config + redaction), O-7, U-8 (naming) | Everything else needs a running, observable system |
| **2. Correctness of money and time** | T-1.1/1.2/1.4/1.5, T-2.1/2.2/2.4/2.5, T-3.1, T-4.1/4.2/4.3, D-1/D-2/D-3/D-6/D-7, B-1.1/B-1.2, X-2 | Wrong calendars, ticks, costs or timestamps invalidate every result after them |
| **3. Safe execution** | T-3.4/3.5, T-5.1/5.4/5.5, T-8.x, T-9.2, M-2/M-4/M-5, O-4/O-5, U-2/U-3 | Live readiness: edge cases, traceability, cockpit |
| **4. Research depth** | B-4.1 real-data run, B-2.x signal vocabulary, B-3.x tearsheet, S-1/S-2/S-3/S-7 | Better and honestly measured signals |
| **5. Pipelines and polish** | S-4..S-13, U-1/U-4..U-7, M-6..M-11, O-6/O-8..O-10, D-5/D-8..D-12 | Breadth, UX, operations |
| **6. Docs** | W-1..W-3: the in-depth README written last, against the finished system | Documents what actually ships |
