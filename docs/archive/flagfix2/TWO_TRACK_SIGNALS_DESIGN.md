# Two-Track Signal Design: Long-Term Investing vs Intraday/Swing Trading
### Signals, analysis pipelines and backtesting methods for the Indian market, on the Historify platform

| | |
|---|---|
| **Builds on** | `BACKTESTING_AND_SIGNALS_HLD.md` (the platform: DataPortal, `available_at`, simulator, signal service) and `INDIA_MARKET_FACTORS_PLAYBOOK.md` (the factor catalogue and tiers A–D) |
| **Answers** | How to split the analysis into a **Long-term (L)** track and an **Intraday/Swing (T)** track, what signals each produces, what data each needs, and **how to backtest ideas in each track** |
| **Status** | Design. No code changed. The thresholds below are *starting hypotheses to test*, not rules to trust. |
| **Not advice** | A framework for building and validating your own system. |

---

## Contents
1. [The two tracks side by side](#1-the-two-tracks-side-by-side)
2. [Shared foundation (build once, used by both)](#2-shared-foundation-build-once-used-by-both)
3. [Track L: long-term investing](#3-track-l-long-term-investing)
4. [Track T: intraday and swing trading](#4-track-t-intraday-and-swing-trading)
5. [How to backtest an idea: the two research funnels](#5-how-to-backtest-an-idea-the-two-research-funnels)
6. [How the tracks interact (capital, conflicts, tax)](#6-how-the-tracks-interact-capital-conflicts-tax)
7. [Platform additions to the HLD](#7-platform-additions-to-the-hld)
8. [Experiment backlog (first 16 ideas to test)](#8-experiment-backlog-first-16-ideas-to-test)
9. [Roadmap for both tracks](#9-roadmap-for-both-tracks)
10. [Decisions for you](#10-decisions-for-you)

---

## 1. The two tracks side by side

![Two-track architecture](backtesting_hld_diagrams/two_track_architecture.png)

| Dimension | **Track L: long-term investing** | **Track T: intraday & swing trading** |
|---|---|---|
| Goal | Compound capital; beat Nifty 500 TRI after costs and tax | Positive expectancy per trade; smooth equity curve; low correlation to the core portfolio |
| Holding period | 1–5+ years per position; portfolio rebalanced monthly or quarterly | Intraday: minutes to hours (flat by square-off). Swing: 2–15 trading days. |
| Decision unit | **Portfolio weights** (which 15–30 stocks, how much of each) | **Trades** (entry, stop, target, size, time limit) |
| What decides | Business quality, growth, valuation, governance; price only to time entries | Price, volume, positioning (OI, delivery), regime; fundamentals only as filters |
| Signal outputs | Model portfolio, Buy / Accumulate / Hold / Trim / Exit with target weights, thesis-breaker alerts, market equity-allocation % | Daily watchlist, setups with entry/stop/target/size, intraday triggers, exit/trail alerts |
| Signal cadence | Monthly rebalance + quarterly results review + daily evening alerts (filings) | Swing: every evening after the post-close sync. Intraday: at each 15m bar close (REST) during the session. |
| Key data | PIT fundamentals, shareholding/pledge, filings, D prices, valuation history, macro | 1m + D OHLCV, F&O OI/chain, delivery %, India VIX, FII/participant OI, event calendar |
| Backtest engine | **Cross-sectional rebalance simulator** + factor research (IC, quintiles) | **Event-driven trade simulator** on 1m bars + event studies |
| Main metrics | CAGR, alpha / information ratio vs TRI, max DD, turnover, factor IC, quintile spread, tax drag | Expectancy (R), profit factor, win rate, max DD, trades/month, MAE/MFE, cost drag, results by regime |
| Sample size | Small: 10–15 years = 2–3 market cycles, 40–60 rebalances | Large: hundreds to thousands of trades |
| Typical failure | Look-ahead in fundamentals, survivorship, value traps, overfit factor mixes | Costs and slippage, overfitting parameters, regime change, latency |
| Tax treatment (check with a CA) | LTCG 12.5% above ₹1.25 lakh after 12 months; STCG 20% below 12 months | Intraday equity = speculative business income; F&O = non-speculative business income; swing delivery = STCG |
| Data readiness today | **Low.** Fundamentals not in Historify; PIT data must be bought or built. Price-only long-term signals can start now. | **High.** Historify already has 1m + D. Bhavcopy, events and F&O collection are next. |

**Key insight:**
- Track T can start producing tested signals soonest.
- Track L can start with **price-based long-term signals** (momentum, trend-filtered allocation, low volatility, relative strength). These are Tier A and need no fundamentals, so they can run while you decide how to source PIT fundamentals.

---

## 2. Shared foundation (build once, used by both)

| Component | Why both tracks need it | HLD reference |
|---|---|---|
| Point-in-time DataPortal (`available_at` on every value) | L: results known only from the filing time. T: a higher-timeframe bar is known only once it closes. | HLD §5.2 |
| PIT universe (index membership, delisted stocks kept) | Survivorship bias hurts both, and long-term results most | HLD §5.5 |
| Corporate actions (adjusted view for signals, raw view for fills) | Splits and bonuses are common in Indian mid caps | HLD §5.4 |
| Versioned cost schedule (delivery, intraday, F&O) | L: delivery STT of 0.1% on both sides. T: intraday and F&O charges dominate small edges. | HLD §10.5, Playbook App. B |
| **Event calendar** (results dates, RBI, Budget, Fed, CPI, expiry, ex-dates, index changes, elections) | L: quarterly review cadence, thesis-breaker checks. T: "don't hold through X" risk rules. | New, §7 |
| **Market regime layer** (daily) | L: equity-allocation %. T: strategy on/off and size multiplier. | New, §2.1 |
| Snapshots, run registry, signal service, paper tracking | Reproducibility and live-vs-backtest parity for both tracks | HLD §5.1, §12 |

### 2.1 Shared market regime layer (computed daily after the close)
| Input | Measure | Tier |
|---|---|---|
| Trend | Nifty 50 / Nifty 500 close vs 50 and 200 DMA; slope of the 200 DMA | A |
| Breadth | % of the PIT Nifty 500 above their 50 and 200 DMA; new 52-week highs − lows | A |
| Volatility | India VIX level and its 1-year percentile | A |
| Flows | FII cash 20-day sum; FII index-futures long/short ratio | A |
| Valuation (slow) | Nifty P/E percentile (10 years); earnings yield − 10-year G-sec yield | B |
| Event proximity | Days to the next major event (Budget, RBI, election results, Fed) | A (calendar) |

**Output (one row per day, stored as a REF series):**
- `regime ∈ {bull_trend, bear_trend, range, high_vol_stress}`;
- `risk_multiplier ∈ [0, 1]`;
- `equity_allocation ∈ [40%, 100%]`.

Both tracks read the same row, aligned on `available_at` = that day's close + 15 min.

---

## 3. Track L: long-term investing

### 3.1 Analysis pipeline (the funnel)
```
PIT universe (Nifty 500 members on date t, liquidity ≥ ₹X cr ADV)
  → Hygiene / red-flag filters             (governance, pledge, auditor exits, ASM/GSM, losses, negative CFO)
  → Quality score                          (ROCE/ROE, cash conversion, leverage, margin stability)
  → Growth score                           (revenue/EPS growth, acceleration, order book for capex names)
  → Valuation score                        (earnings/FCF yield, P/E vs own 10-year band, PEG; P/B for financials)
  → Ownership & momentum confirmation      (promoter/MF buying, 12-1 month momentum, price above 200 DMA)
  → Sector / theme / macro overlay         (sector caps, cycle position, structural themes; judgement allowed)
  → Portfolio construction                 (N stocks, weights, caps by stock/sector/group)
  → Entry timing                           (staggered buys, valuation band, regime-based allocation %)
  → Monitoring & exits                     (quarterly review, thesis breakers, rank decay)
```

### 3.2 Factor definitions (starting point)
Compute each factor **sector-relative** (z-score within sector); a bank and an IT company can't be ranked on the same ratios. Treat financials as a separate sub-model.

| Pillar | Non-financials | Financials (banks / NBFCs) | `available_at` |
|---|---|---|---|
| **Quality** | 5-year median ROCE; 5-year cumulative CFO/PAT; net debt/EBITDA; interest coverage; EBITDA-margin stability (std dev); other income / PBT (penalise) | ROA, ROE, NNPA, provision coverage, CASA, cost/income, capital adequacy | Results filing timestamp (annual report for 5-year items) |
| **Growth** | 3-year revenue and EPS CAGR; last-4Q YoY; growth acceleration (last 2Q vs prior 2Q); order book / revenue (capital goods, defence) | Loan and deposit growth, NII growth, credit-cost trend | Filing timestamp |
| **Valuation** | Earnings yield (E/P), FCF yield, EV/EBITDA; **P/E percentile vs own 10-year history**; PEG | P/B vs own history, P/B ÷ ROE | Price: daily. Earnings: filing. |
| **Ownership** | Promoter holding change (4Q), **pledge %** and its change, MF + FII holding change, promoter open-market buys | Same | Shareholding: filed within 21 days of quarter end. Pledge: SAST disclosures. |
| **Momentum / trend** | 12-1 month return, distance from 52-week high, price vs 200 DMA | Same | Daily close |
| **Risk** | 1-year volatility, beta, maximum drawdown, liquidity (ADV) | Same | Daily |

**Red-flag filters (exclude, don't score):**
- promoter pledge > 20% or rising sharply;
- auditor resignation or qualified opinion in the last 2 years;
- CFO negative in 3 of the last 5 years (non-financials);
- ASM/GSM or T2T membership;
- loss-making in the last 4 quarters;
- ongoing SEBI/ED action (manual flag);
- ADV below the liquidity floor.

### 3.3 Composite score and signals
- **Composite** = weighted sum of pillar z-scores, for example Quality 35%, Growth 25%, Valuation 20%, Momentum 15%, Ownership 5%. These weights are **research outputs, not inputs** (§5.1). Start equal-weighted, then test.
- **Signal states at each rebalance:**

| State | Rule (starting point) |
|---|---|
| **Buy / Accumulate** | Passes filters, composite rank in the top N (for example top 25), and price above 200 DMA or in the lower half of its valuation band |
| **Hold** | Still in the top 2N (a buffer avoids churn) |
| **Trim** | Weight above cap after a rally, or valuation above the 90th percentile of its own band |
| **Exit** | Drops out of the top 2N for 2 consecutive reviews, **or** a thesis breaker fires |

**Thesis breakers** are checked daily, after the evening filings sync:
- new pledge or pledge jump;
- auditor resignation;
- rating downgrade;
- CFO/CEO exit;
- regulator action;
- two consecutive quarters of revenue decline with margin compression;
- ROCE falling below its cost-of-capital threshold.

A breaker triggers an **alert for review**, not an automatic sale. This is where judgement (Tier D) stays in the loop.

**Market-level equity allocation** (from the regime layer, §2.1):
- 100% equity when Nifty is above its 200 DMA and its valuation percentile is below 70%;
- scale down to 60–40% when it is below the 200 DMA *and* expensive;
- the new cash is deployed in tranches when VIX spikes and breadth recovers.

This rule is **Tier A (price-only) and very backtestable.**

### 3.4 Signals you would receive (example)
```
2025-06-02  MODEL PORTFOLIO REBALANCE (Quality-Growth-Value, 25 stocks, equal weight, cap 6%)
  BUY   ABC Ltd    target 4.0%  composite 1.82 (Q +2.1, G +1.4, V +0.9, M +1.1)  P/E pct 38%
  TRIM  XYZ Ltd    6.9% → 4.0%  valuation pct 93%
  EXIT  PQR Ltd    rank 71 (2nd review outside top 50)
  HOLD  22 others
  Equity allocation: 90% (Nifty > 200 DMA, valuation pct 72%)
2025-06-11  THESIS ALERT  XYZ Ltd  promoter pledge 0% → 14% (SAST filing 18:42) → review
```

### 3.5 Data needed for Track L
| Data | Source | Tier | Status |
|---|---|---|---|
| Daily prices, total-return adjustment (dividends) | Historify D + corporate actions | A | D ✅; corporate actions = HLD phase 0 |
| PIT universe (Nifty 500 membership history) | NSE Indices | B | New |
| **PIT fundamentals (as reported, with filing timestamps)** | **Buy** (CMIE Prowess, ACE Equity, Capitaline) **or build** from NSE/BSE XBRL results filings | B | New: **the key decision** (§10) |
| Shareholding pattern, pledge, SAST / insider trades | NSE/BSE filings | B | New |
| Corporate announcements (auditor, ratings, management) | NSE/BSE announcement feeds (timestamped) | B | New |
| Valuation history (P/E, P/B per stock) | Derived: price ÷ trailing PIT EPS / book value | B | Derived |
| Nifty P/E, P/B, dividend yield history | NSE Indices | A/B | New REF series |
| 10-year G-sec yield | RBI / market data | B | New REF series |

### 3.6 How to backtest Track L
The engine is a **cross-sectional rebalance simulator**, new in §7; it reuses the HLD cost and execution model.

```python
for t in rebalance_dates:                                # e.g. first trading day of each month
    U = universe_pit("NIFTY500", t)                      # members on t, delisted stocks included
    X = features(U, as_of=t)                             # ONLY values with available_at <= t (close of t)
    X = apply_red_flags(X)
    score = composite(X, weights)                        # sector-relative z-scores
    target = construct(score, n=25, cap=0.06, sector_cap=0.25, buffer=2)
    orders = rebalance(current, target, min_trade=0.5%)  # no churn on tiny drifts
    fills = execute(orders, at="next_open", slippage=by_adv_bucket, costs="CNC")
    # between rebalances: mark-to-market daily, apply dividends, splits, delistings, thesis-breaker exits
```

**Mandatory realism for Track L:**
- Trade at the **next open after the decision**, not at the decision-day close.
- **Delistings, suspensions and mergers:**
  - delisting: exit at the last traded price, or with a haircut;
  - merger: convert at the swap ratio;
  - suspension: hold at zero liquidity.
- **Total return:** dividends are reinvested or held as cash. The benchmark is **Nifty 500 TRI** or Nifty 50 TRI, never the price index.
- **Capacity:** position size ≤ x% of 20-day ADV. Small caps break here.
- **Tax drag (optional view):** apply the STCG/LTCG regime by holding period and date, since the rules changed in July 2024.

**Research steps** (in order; see §5.1):
1. **Factor IC / quintile study** per factor (is the factor predictive at all?).
2. Composite portfolio backtest.
3. Robustness:
   - rebalance frequency;
   - N;
   - weighting;
   - **start-date offsets**: run 12 versions starting in different months, because rebalance timing luck is real;
   - sub-periods.
4. Hold-out: the last 3 years.
5. Paper model portfolio: at least 2 quarters.

### 3.7 Price-only long-term strategies to start now (Tier A, no fundamentals needed)
| ID | Idea | Rule sketch |
|---|---|---|
| L-P1 | **Momentum portfolio** | Top 20–30 of Nifty 200 by risk-adjusted 12-1 month momentum; monthly/quarterly rebalance; buffer rule. Benchmark: Nifty200 Momentum 30. |
| L-P2 | **Trend-filtered allocation** | Hold a Nifty 50/500 index ETF when the index is above its 200 DMA; move partly to liquid/G-sec when below (with hysteresis). Measure the drawdown reduction vs CAGR cost. |
| L-P3 | **Low-volatility quality proxy** | Lowest 1-year volatility + positive 12-month return within Nifty 200; quarterly rebalance |
| L-P4 | **Relative-strength sector allocation** | Overweight the top 3 sectoral indices by 6-month RS; monthly |
| L-P5 | **Valuation-band SIP** (index) | Monthly SIP, plus extra tranches when Nifty's P/E percentile < 30% or the drawdown from its high > 15% |

Once PIT fundamentals arrive, add **L-F1 Quality-at-reasonable-price**, **L-F2 Quality + momentum**, **L-F3 Growth acceleration + revisions** and **L-F4 Promoter/MF accumulation** (§8).

---

## 4. Track T: intraday and swing trading

### 4.1 Analysis pipeline
```
Regime (daily, shared §2.1)            → which strategies are ON and at what size multiplier
  → Universe & tradability filters     (liquidity, F&O list, ban list, ASM/GSM, T2T, circuit proximity, lot terms)
  → Event filter                       (results / ex-date / RBI / Budget / expiry inside the holding window?)
  → EOD scanner (swing) / pre-open scanner (intraday)  → ranked watchlist of setups
  → Setup on the signal timeframe      (D for swing; 15m for intraday) with higher-timeframe trend alignment
  → Trigger on the execution timeframe (next bar open / break of level on 1m–5m)
  → Risk: stop (ATR or structure), size (risk % ÷ stop distance, rounded to lots), R:R check
  → Management: trailing stop, partial exit at 1R/2R, time stop, MIS square-off
  → Journal: every signal, fill and exit recorded for live-vs-backtest parity
```

### 4.2 Swing strategy families (2–15 days), with testable specs
| ID | Family | Setup (signal TF = D unless stated) | Trigger / stop / exit (starting point) | Key filters |
|---|---|---|---|---|
| **S1** | **Trend pullback (multi-timeframe)** | Close > EMA50 > EMA200, ADX(14) > 20, RS vs Nifty rising (20-day); pullback to EMA20 or 38–50% of the last swing on falling volume | Entry: break of the previous day's high (or a 1h Supertrend flip up). Stop: below the pullback low or 1.5 × ATR(14). Exit: 2R target or trail EMA20; 10-day time stop. | Regime ≠ bear; no results in the next 5 sessions |
| **S2** | **Volume + delivery breakout** | Close at a 55-day high (Donchian) after volatility contraction (ATR% in the bottom 30% of its 6-month range); volume > 2 × 20-day average; **delivery % > its 20-day average** | Entry: next open (or a pullback to the breakout level within 2 days). Stop: below the base low / 2 × ATR. Exit: trail the 10-day low. | Liquidity; not within 5% of the upper circuit |
| **S3** | **OI build-up swing** (F&O stocks, via futures) | Long build-up on 2 consecutive days (price ↑, futures OI ↑ > 5%) above the 20 DMA; mirror for shorts (short build-up below the 20 DMA) | Entry: next open. Stop: 1.5 ATR. Exit: OI unwinding (price ↓ OI ↓) or 2R. | Not in the ban list; roll before expiry week |
| **S4** | **Mean reversion in an uptrend** | Close > 200 DMA and RSI(2) < 10 (or 3 down days) | Entry: next open. Exit: RSI(2) > 70 or 5 days. Stop: wide (3 ATR) or none plus the time stop; test both. | Large caps only; regime ≠ high_vol_stress |
| **S5** | **Sector rotation swing** | Top-3 sectoral indices by 1-month RS; within them, the stocks with the best RS and an S1/S2 setup | As S1/S2 | Sector cap ≤ 2 positions |
| **S6** | **Event drift** | (a) Post-results: gap up > 3% on results with close in the top 25% of the day's range → drift. (b) Index inclusion: buy after the announcement, sell before the effective date. | (a) Entry next open, stop below the gap-day low, exit at 10 days. (b) Exit the day before the effective date. | Filing timestamps; PIT announcement dates |

### 4.3 Intraday strategy families (flat by square-off)
| ID | Family | Setup | Trigger / stop / exit (starting point) | Key filters |
|---|---|---|---|---|
| **I1** | **Opening range breakout (ORB)** | 15m opening range (09:15–09:30) | Entry: 5m close beyond the opening range, in the direction of the daily trend and the VWAP side. Stop: the other side of the range or 1 × ATR(5m × 14). Exit: 2R or 15:10. | Gap within ±1.5 ATR; **narrow CPR** (trending-day tendency); regime trend |
| **I2** | **VWAP trend pullback** | Price above session VWAP, 15m EMA20 > EMA50, higher highs | Entry: pullback to VWAP ± 0.1% and a 5m bullish close. Stop: below the pullback low. Exit: trail 5m swing lows; 15:10. | Stock RS vs Nifty > 0 since the open |
| **I3** | **Gap strategies** | Gap size vs ATR, pre-open strength, GIFT Nifty / global cues | Gap-and-go if the gap holds through the first 15m; gap-fill fade if the first 15m reverses inside the previous day's range | Separate statistics by gap bucket |
| **I4** | **Relative-strength leaders** | In a bull regime, the top 10 stocks by % change vs Nifty at 09:45 with volume > 1.5 × normal for that time of day | Entry: break of the 09:15–09:45 high. Stop: VWAP. Exit: 15:10. | F&O stocks (liquidity) |
| **I5** | **Index options: short premium** (for example a 09:20 short straddle or strangle on Nifty) | Time-based entry; IV percentile filter | Stop loss per leg (for example 25–40% of premium), exit at 15:15 | **Regime changes Nov 2024 / Feb 2025** (split the backtest); costs; **tail-risk caps**; no trading on event days |
| **I6** | **OI-shift directional (index)** | Call writers moving strikes down + put writers holding/adding, PCR rising intraday, price above VWAP | Trade futures or buy ATM options. Stop: VWAP break. | Needs intraday OI history: verify availability; forward collection otherwise |

**Execution and latency reality** (HLD §12.3): over REST, live intraday signals are reliable on **15m bars** (5m for small universes), about 1–2.5 minutes after the bar closes. Backtest **I1–I4 on 15m signals with that delay** in live-parity mode. Strategies that only work on 1m signals need the WebSocket add-on.

### 4.4 Risk rules (apply to every T strategy; each rule is itself backtestable)
- **Per trade:** risk 0.5–1.0% of the strategy's capital ÷ stop distance; round **down** to lots or shares.
- **Portfolio:**
  - at most 5–8 concurrent swing positions;
  - at most 2 per sector;
  - at most 1 per business group;
  - gross exposure ≤ capital × regime `risk_multiplier`.
- **Daily loss limit (intraday):** stop trading for the day after −2R (or −1.5% of capital).
- **Events:**
  - no new swing positions when results fall inside the expected holding window;
  - half size the day before RBI, Fed and CPI;
  - flat or hedged into Budget and election-result days;
  - no naked short options overnight into those events.
- **Overnight gaps (swing):** stop fills at the open when the price gaps past the stop (the simulator does this, HLD §10.2). Size for gaps, not just stops.
- **Drawdown circuit breaker:** pause a strategy when its live drawdown exceeds 1.5 × the backtest maximum drawdown, or 2 × the worst losing streak.

### 4.5 Signals you would receive (example)
```
EOD 2025-06-02 16:20  SWING (regime: bull_trend, risk x1.0)
  S2 BREAKOUT  ABC Ltd  BUY next open ~1,242 | stop 1,188 (2xATR) | trail 10-day low | size 160 sh (0.75% risk)
               vol 2.6x, delivery 61% vs 44% avg, no results for 23 sessions
  S1 PULLBACK  DEF Ltd  BUY above 878.5 (prev high) | stop 851 | target 2R 933.5 | valid 2 sessions
  EXIT         GHI Ltd  trail hit (close < 10-day low)
INTRADAY 2025-06-03 09:30  ORB watch: NIFTY CPR narrow (0.18%), gap +0.4% (0.5 ATR)
  10:01 I1 LONG  JKL Ltd  5m close above OR-high 412.3 | stop 405.9 | 2R 425.1 | exit by 15:10
```

### 4.6 Data needed for Track T
| Data | Source | Tier | Status |
|---|---|---|---|
| 1m + D OHLCV (stocks, indices), session VWAP, opening range, CPR | Historify | A | ✅ (roll-up audit pending) |
| India VIX | Historify (`NSE_INDEX`) | A | ✅ downloadable |
| Delivery %, official close, EOD futures/options OI | NSE bhavcopy | A | HLD phase 3 |
| Intraday F&O OI / option chain | Forward collection (+ vendor for history) | A/B | HLD phase 3 (start collecting now) |
| FII/DII cash, participant-wise OI | NSE archives | A | New refdata job |
| Ban list, ASM/GSM, lot/tick history, circuit bands | NSE | A/B | HLD phases 0/3 |
| **Event calendar** (results dates, ex-dates, RBI, CPI, Fed, Budget, expiry, index changes) | Exchange filings + official calendars | A/B | New (§7) |
| GIFT Nifty / global cues (for I3) | Vendor / public | B | Optional |

### 4.7 How to backtest Track T
- **Engine:** the HLD **hybrid event-driven simulator**. Setups are computed vectorised; fills, stops and targets are simulated on 1m bars, with next-bar fills, gap-through stops, circuit locks, intraday square-off, costs and slippage.
- **Research steps** (in order; see §5.2):
  1. Event study / conditional returns.
  2. Full simulation.
  3. Robustness (parameter plateau, 2×/3× slippage, regime splits at **Nov 2024** for F&O, removal of the top 5% of trades).
  4. Walk-forward.
  5. Hold-out (the last 12 months).
  6. Paper trading for 20–40 sessions with a daily parity check.

---

## 5. How to backtest an idea: the two research funnels

Both funnels share one principle: **cheap tests first, expensive tests only for survivors.** Each idea also gets a **hypothesis card** before any code. This stops "data mining until something works".

```
HYPOTHESIS CARD
  Name / track / horizon:
  Why should this work in India? (behavioural, structural or flow reason)
  Who is on the other side of the trade, and why do they lose?
  Data needed (tier, available_at):
  Expected frequency (trades/year or rebalances):
  What result would REJECT the idea?  (written before testing)
  Regimes where it should fail:
```

![Research funnels](backtesting_hld_diagrams/research_funnels.png)

### 5.1 Track L funnel (factor → portfolio)
| Stage | Test | Tooling | Pass criterion (starting point) |
|---|---|---|---|
| L0 | Hypothesis card + data-availability check (PIT? how many years?) | Data Readiness page | PIT data ≥ 10 years, or accept lower confidence |
| L1 | **Factor IC:** Spearman rank correlation between the factor at *t* and the forward 3/6/12-month return, across the PIT universe, at each month *t* | Factor toolkit (§7) | Mean IC ≥ 0.03–0.05 with t-stat ≥ 2; positive in ≥ 60% of months |
| L2 | **Quintile study:** forward returns of Q1…Q5 portfolios | Factor toolkit | Roughly monotonic; Q1 − Q5 spread positive after costs; not driven by one sector (check sector-neutral) |
| L3 | **Portfolio backtest** (N stocks, rebalance, costs, capacity) | Rebalance simulator | Beats Nifty 500 TRI by ≥ 3%/year net; IR ≥ 0.5; max DD ≤ benchmark DD + 5 pts; turnover ≤ 100%/year |
| L4 | **Robustness:** 12 start-month offsets, N ∈ {15, 25, 40}, equal vs score weight, monthly vs quarterly, sub-periods (2010–14, 2014–18, 2018–21, 2021–now), small caps in vs out | Sweep runner | Outperformance in most variants and sub-periods; no single variant carrying the result |
| L5 | **Out-of-sample hold-out** (last 3 years, untouched until L4 is frozen) | Run registry (locked) | Same sign and ≥ 50% of in-sample alpha |
| L6 | **Paper model portfolio** (live signals, no money) for 2+ quarters | Signal service | Tracking vs the backtest for the same period within a tolerance |

### 5.2 Track T funnel (setup → trade system)
| Stage | Test | Tooling | Pass criterion (starting point) |
|---|---|---|---|
| T0 | Hypothesis card | — | — |
| T1 | **Event study:** forward returns after the setup (1/3/5/10 days for swing; 15/30/60 min and to the close for intraday) vs the unconditional base rate over the same universe and regime; counts | Event-study tool (§7): vectorised, seconds | Edge > round-trip cost × 2; ≥ 200 events (intraday) or ≥ 100 (swing); consistent in ≥ 2 of 3 sub-periods |
| T2 | **Full simulation** with stops, targets, sizing, costs, slippage, square-off | Hybrid simulator | Profit factor ≥ 1.3; expectancy ≥ 0.15R; max DD acceptable for the capital |
| T3 | **Robustness:** parameter heatmap (plateau, not a peak), slippage 2×/3×, remove the best 5% of trades, different universes (Nifty 100 vs F&O stocks), regimes, day of week, expiry vs non-expiry | Sweep runner | Still profitable at 2× slippage; neighbours of the chosen parameters within 30% of its performance |
| T4 | **Walk-forward** (for example 2 years train → 6 months test, rolling) | Walk-forward runner | Walk-forward efficiency (OOS/IS return) ≥ 50%; stitched OOS equity positive |
| T5 | **Hold-out** (last 12 months) | Run registry (locked) | Profit factor ≥ 1.2 |
| T6 | **Paper trading** 20–40 sessions in live-parity mode | Signal service + OpenAlgo Analyzer | Signals match the backtest 1:1 (parity check); slippage within the model |
| T7 | **Small live** (¼ size) with kill criteria | Deployment limits | Drawdown < 1.5 × backtest max DD; else stop and re-research |

### 5.3 Rules that protect you from fooling yourself (both tracks)
1. **Count your trials.** Every parameter combination and every variant is logged in the run group. Reports show a **deflated Sharpe ratio** that accounts for how many things you tried.
2. **Hold-outs are locked.** The UI won't run on the hold-out period until the strategy version is frozen. Re-opening it means the hold-out is spent.
3. **Split by regime by default.** Pre/post Nov 2024 (F&O), COVID 2020, high/low VIX, bull/bear (200 DMA).
4. **Costs are never optional.** Show gross and net; cost drag above 50% of gross edge means reject.
5. **Fix the benchmark in advance.** Track L: Nifty 500 TRI and the matching NSE factor index. Track T: the same capital in a Nifty ETF, and a random-entry strategy with the same exits (is the entry adding anything?).
6. **Run the look-ahead property test** (HLD §14.2) on every strategy before it can be deployed.

### 5.4 Two research tools, sketched
**Factor IC (Track L, stage L1)**
```python
ic = []
for t in month_ends:
    U = universe_pit("NIFTY500", t)
    f = factor(U, as_of=t)                               # available_at <= t
    r = forward_return(U, t_next_open, horizon="6M")     # entry at the next open, includes delisted (-100% or last price)
    ic.append(spearman(f, r))
report(mean(ic), tstat(ic), hit_rate(ic), ic_by_year, ic_by_sector)
```

**Event study (Track T, stage T1)**
```python
events = setup_mask(panel)                               # e.g. S2: 55-day high & vol>2x & delivery%>avg (as of close t)
entry  = next_open(panel, events)                        # never the signal bar's close
fwd    = {h: price_at(entry_time + h) / entry_px - 1 for h in (1, 3, 5, 10)}   # trading days
base   = same horizons, all days, same universe & regime
report(mean/median/hit-rate of fwd − costs vs base, n_events, by_year, by_regime, by_liquidity_bucket)
```

---

## 6. How the tracks interact (capital, conflicts, tax)

| Topic | Recommendation |
|---|---|
| **Capital buckets** | Separate them, for example 70% Track L (core) / 30% Track T. Each track has its own equity curve, drawdown limits and kill rules. Never let a swing loss be "fixed" by turning it into a long-term holding. |
| **Conflicting signals** | A T short or exit signal on an L holding **does not sell the core**. Optionally hedge via futures, if the stock is in F&O. A T buy on an L holding counts against the combined per-stock cap. |
| **Trade around core** (optional) | Use the L portfolio as a swing universe with only S1/S4 long setups, in a separate T sub-account, so results stay attributable. |
| **Shared regime** | The same daily regime row drives L's equity allocation and T's risk multiplier. One source of truth. |
| **Correlation check** | Report the correlation of the T equity curve with L's. T should add diversification, not more of the same beta. |
| **Tax and accounting** | Tag every signal and order with a track/strategy. Intraday (speculative), F&O (non-speculative business) and delivery (capital gains) are taxed differently. Keeping them separate simplifies the ITR and audit. Separate demat/trading sub-accounts or strategy tags in OpenAlgo help. Confirm the treatment with a CA. |
| **Review cadence** | L: monthly rebalance + quarterly deep review. T: daily journal + weekly strategy review + monthly parity and drawdown check. |

---

## 7. Platform additions to the HLD

The HLD covers the trade simulator, the data layer and the signal service. These two tracks need these **additional components**:

| Component | Track | What it does | Effort |
|---|---|---|---|
| **Regime service** | L + T | Computes the daily regime row (§2.1) and stores it as a REF series; exposed in the DataPortal and on the UI dashboard | 2 d |
| **Event calendar + `events` table** | L + T | `(instrument/market, kind, announced_at, effective_date, payload, source)`: results dates and filings, ex-dates, RBI/CPI/Fed/Budget, index changes, expiries, elections. Has an "events inside holding window" query. | 4 d (NSE/BSE announcements + static calendars) |
| **Market-data extras ingestion** | T (+ L) | NSE archives: FII/DII cash, participant-wise OI, bulk/block deals, ban list, ASM/GSM → REF series + `events` | 4 d |
| **Scanner / ranking job** | T (+ L) | Cross-sectional screens on the PIT universe after the close: setup detection, ranking, watchlist output | 3 d |
| **Event-study tool** | T | Vectorised conditional forward-return analysis with base rates, costs and splits (§5.4) | 3 d |
| **Cross-sectional rebalance simulator** | L | Rebalance dates, target weights → orders (reuses the execution and cost model), buffers, caps, capacity, dividends/delistings/mergers, TRI benchmark | 5 d |
| **Factor research toolkit** | L | IC, quintiles, sector-neutralisation, factor correlation, turnover per factor | 4 d |
| **PIT fundamentals store** | L | `fundamentals(instrument, period_end, item, value, filed_at, source, is_restated)` + `shareholding`, `pledges`. **Vendor adapter (≈ 5 d) or XBRL builder (≈ 15–20 d)**, §10. | 5–20 d |
| **Thesis-breaker monitor** | L | Evening job over new filings and events for L holdings → alerts | 2 d |
| **Model-portfolio + journal UI** | L + T | L: portfolio, scorecards, rebalance trade list. T: watchlist, active setups, trade journal with R-multiples. | 5 d |

---

## 8. Experiment backlog (first 16 ideas to test)

| ID | Track | Idea | Data tier | Needs (phase) | Reject if… |
|---|---|---|---|---|---|
| E1 | L | L-P2 trend-filtered Nifty allocation (200 DMA, with hysteresis) | A | Foundation | DD reduction < 30% or CAGR cost > 2%/yr |
| E2 | L | L-P1 momentum top-25 of Nifty 200 (12-1, risk-adjusted) | A | Foundation + rebalance sim | Fails to beat the Nifty 200 TRI net in L4 variants |
| E3 | L | L-P3 low-volatility + positive momentum, Nifty 200 | A | same | IR < 0.3 |
| E4 | L | L-P4 sector RS rotation (sectoral indices) | A | same | Turnover cost eats > 50% of the excess return |
| E5 | L | L-P5 valuation-band SIP vs plain SIP (index) | A/B | Nifty P/E REF series | No improvement in IRR across start dates |
| E6 | L | L-F1 quality-at-reasonable-price (ROCE, CFO/PAT, D/E + P/E percentile) | B | PIT fundamentals | L1 IC not significant |
| E7 | L | L-F2 quality filter + momentum ranking | B | PIT fundamentals | No improvement over E2 |
| E8 | L | L-F4 promoter buying + MF accumulation | B | Shareholding/SAST ingestion | Q1–Q5 spread ≤ 0 |
| E9 | T | S1 MTF trend pullback on the Nifty 200 | A | Foundation + MTF | PF < 1.3 at 2× slippage |
| E10 | T | S2 breakout with volume + **delivery %** filter vs without the delivery filter | A | Bhavcopy | Delivery filter adds no expectancy |
| E11 | T | S4 RSI(2) mean reversion in an uptrend (large caps) | A | Foundation | Edge < 2 × costs |
| E12 | T | S6a post-results drift (gap + strong close) | B | Events (filing timestamps) | < 100 events or no drift vs base |
| E13 | T | S6b index inclusion run-up (announcement → effective) | B | Index-change events | Edge not positive in most years |
| E14 | T | I1 15m ORB with CPR-width + regime filters, on F&O stocks | A | Foundation | PF < 1.2 in live-parity mode (15m + latency) |
| E15 | T | I5 09:20 Nifty short straddle, split pre/post Nov 2024 | A (chain) | F&O data + sim | Post-2024 regime negative, or tail loss > 3 months of profit |
| E16 | L+T | **Event-risk rule:** flat/half size into results, RBI, Budget and election days vs always-in | A/B | Event calendar | No DD reduction for the same return |

Suggested order:
- E1, E2, E9, E11 and E14 need only data you nearly have.
- Then E10 and E16 once the bhavcopy and the event calendar exist.
- The fundamentals experiments (E6–E8) come once PIT data is decided.

---

## 9. Roadmap for both tracks

Effort is for one senior engineer. The shared foundation comes from the HLD, plus the additions in §7.

| Step | Content | Track | Effort | Unlocks |
|---|---|---|---|---|
| **1. Foundation** | HLD phases 0–2 (calendar, `available_at`, corporate actions, backtest core, MTF + features) + regime service | L + T | ≈ 26 d | E1, E9, E11, E14 |
| **2. Swing + intraday research kit** | Event-study tool, scanner, event calendar, market-data extras; the equity bhavcopy importer (delivery %) is pulled forward from HLD phase 3 (its effort stays counted in step 5) | T (+L) | ≈ 14 d | E10, E12, E13, E16 |
| **3. Long-term kit (price-only)** | Cross-sectional rebalance simulator, factor toolkit, TRI benchmarks, Nifty P/E REF series | L | ≈ 9 d | E2–E5 |
| **4. Signals live (both tracks)** | HLD phase 6 + model-portfolio and journal UI + paper tracking | L + T | ≈ 12 d | Paper trading for the winners of steps 1–3 |
| **5. F&O** | HLD phases 3–4 (start chain collection in week 1 regardless) | T | ≈ 18 d | E15, S3, I6 |
| **6. Fundamentals** | PIT store + vendor adapter (≈ 5 d) **or** XBRL builder (≈ 15–20 d) + shareholding/pledge + thesis-breaker monitor | L | ≈ 9–24 d | E6–E8, full L signals |
| **7. Research UI / walk-forward polish** | HLD phase 5 | L + T | ≈ 8 d | Productivity |

**Totals**

| Path | Steps | Effort |
|---|---|---|
| Equity-only | 1–4 + 7 | ≈ 69 engineer-days |
| Full | 1–7 | ≈ 96–111 engineer-days |

**Milestones**
- **About week 6:** first tested swing/intraday and price-only long-term ideas (E1, E2, E9, E11, E14).
- **About week 10:** both tracks produce paper signals daily.
- **F&O and fundamentals** then run in parallel, depending on your decisions below.

---

## 10. Decisions for you

| # | Decision | Options | My recommendation |
|---|---|---|---|
| 1 | Capital split between tracks | e.g. 70/30, 80/20, 50/50 | **70 L / 30 T** until T has 6 months of live parity |
| 2 | PIT fundamentals source for Track L | (a) buy a PIT database, (b) build from NSE/BSE XBRL filings, (c) price-only long-term for now | **(c) now, then (a)** if E2/E3 show the L track is worth it. Building XBRL is a large, fragile project. |
| 3 | Long-term universe | Nifty 100 / 200 / 500 (PIT) | **Nifty 500 for research, Nifty 200 for live** (liquidity) |
| 4 | Rebalance frequency (L) | Monthly / quarterly | Test both (E2); **quarterly** for lower turnover and tax unless monthly is clearly better |
| 5 | Swing vs intraday priority (T) | Swing first / intraday first | **Swing first**: EOD data is complete, latency is irrelevant, costs are lower. Intraday on 15m next. |
| 6 | F&O in T | Not now / futures only / options too | **Futures for S3 now; options (E15) after the F&O phase**, with strict tail-risk caps |
| 7 | Signal delivery | UI only / + webhook / + OpenAlgo paper | **UI + webhook + OpenAlgo Analyzer (paper)**; live only after auth + parity |
| 8 | Judgement overlay in L | Fully systematic / systematic + manual veto | **Systematic ranking + manual veto on thesis-breaker alerts**, logged so the veto's value can be measured |

Reply with your choices for these 8 decisions. Also say which 3–5 experiments from §8 you want first, and I'll detail their exact specifications: data fields, `available_at` rules, parameter grids, pass/fail thresholds and reports.
