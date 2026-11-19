# Intraday: Choosing What to Trade, and Where India VIX Fits

| | |
|---|---|
| **Your proposal** | "For intraday we need volatile stocks or indices, so pick them using VIX and run the models on those." |
| **Short answer** | Use VIX, but for the **market regime** (whether to trade, which strategy, what size, how wide the stops), **not to pick stocks**. Pick stocks with **stock-level** measures of volatility and "in-play-ness": ATR %, relative volume, gap, news/filings catalyst, and beta to Nifty, inside a liquid, shortable universe (F&O stocks + index futures). Both layers are backtestable, and the experiments are below. |
| **Builds on** | `TWO_TRACK_SIGNALS_DESIGN.md` (Track T, strategies I1–I6, experiment E14), `INDIA_DATA_SOURCES_CATALOGUE.md`, and the new **News & Filings archive** (catalysts). |

---

## 1. What India VIX is, and what it is not

- India VIX is the **annualised 30-day expected volatility of the Nifty 50**, computed from Nifty option prices. It is a forecast for **one index**, made by option traders.
- It tells you **how much the market as a whole is expected to move**. It says nothing about *which* stocks will move: a stock's own volatility comes from its own news, results, sector and beta.
- **Rule of thumb: expected 1-σ daily Nifty move ≈ VIX ÷ √252 ≈ VIX ÷ 15.9**. Weekly ≈ VIX ÷ √52. Real returns have fat tails, so treat these as "typical", not "maximum".

| India VIX | Expected daily move (1σ) | ≈ Nifty points at 23,500 | Expected weekly move (1σ) |
|---|---|---|---|
| 10 | 0.63% | 148 | 1.39% |
| 12 | 0.76% | 178 | 1.66% |
| 14 | 0.88% | 207 | 1.94% |
| 16 | 1.01% | 237 | 2.22% |
| 20 | 1.26% | 296 | 2.77% |
| 25 | 1.57% | 370 | 3.47% |
| 30 | 1.89% | 444 | 4.16% |

**Where VIX is genuinely useful for intraday**
1. **Regime switch.** Decide which strategy family is on, for example trend/breakout vs mean-reversion vs premium selling. These are hypotheses to test, not facts.
2. **Position size and stop width.** Higher VIX means wider stops and smaller quantity for the same rupee risk.
3. **Index options directly.** VIX is the IV level of Nifty options: it sets premium levels, expected ranges for straddles and strangles, and whether selling premium is paying enough.
4. **Realised vs implied.** Compare Nifty 10-day realised volatility with VIX. Realised > implied means options are cheap (favours buying gamma); realised well below means options are rich (favours selling, with tail-risk caps).
5. **Direction of VIX.** A spike with the market falling means fear, gap risk and wider spreads. VIX sliding lower while the market grinds up is typical of calm trends.

**Use VIX percentiles (trailing 1 year), not fixed levels.** VIX "normal" levels shift across years: for example 2020 vs 2023–24. A fixed "VIX > 20" rule can be on for a year and then never trigger.

---

## 2. A stock's own "VIX": how to find volatile, tradeable stocks

Volatility alone is not enough. A stock is a good intraday candidate when it is **liquid, moving more than usual, and moving for a reason**.

### 2.1 Base universe (filters, decided the evening before)
| Filter | Why | Source |
|---|---|---|
| **NSE F&O stocks** (≈ 180–220) + **Nifty / Bank Nifty futures** | Deep liquidity, tight spreads; intraday shorts via MIS or futures. Dynamic price bands instead of fixed 2–20% circuits. | NSE F&O contract master (refdata job) |
| Not in the **F&O ban list** | No fresh positions allowed | NSE ban file (add as a feeds source) |
| Not under **ASM/GSM**, not T2T | Margins and trade restrictions | NSE lists |
| Price ≥ ₹100, median 20-day turnover ≥ ₹50–100 Cr | Costs and slippage per trade stay small | Historify D bars |
| **Results / board meeting today?** | Separate bucket: event days behave differently (gap and drift); trade them only with event-specific rules | Board-meeting calendar (**now archived**) |

### 2.2 Pre-market score (by 09:10, from data known by then)
| Feature | Definition | Why |
|---|---|---|
| **ATR %** | ATR(14) ÷ close, ranked across the universe | How much the stock normally moves |
| **Volatility expansion** | ATR(5) ÷ ATR(20); or NR7 / inside day (contraction before expansion) | Moving *more than usual* |
| **Beta to Nifty** | 60-day beta of daily returns | How strongly it amplifies a high-VIX market: "VIX-sensitive" stocks (metals, PSU banks, realty, capital-market stocks typically have high beta) |
| **Catalyst in the last 18 h** | Results, order win, rating change, bulk/block deal, pledge or insider event, big news | **This is why stocks are "in play"**. Comes from the **News & Filings archive** (built this round). |
| **Gap** | Pre-open / first-print price vs previous close, in ATR units | Gaps ≥ 0.5 ATR carry information (gap-and-go or gap-fill) |
| **Relative strength** | 5- and 20-day return vs Nifty and vs the sector | Leaders stay leaders intraday more often in trending regimes |
| **F&O positioning** | Previous day's long/short build-up (price Δ × OI Δ), IV percentile | Crowding, expected move (after bhavcopy ingestion) |

### 2.3 Early-session confirmation (09:15–09:30/09:45)
| Feature | Definition | Why |
|---|---|---|
| **Relative volume (RVOL)** | Volume in the first 15 minutes ÷ the 20-day average volume of the *same* 15 minutes | **The best single "in play" measure**: unusual participation right now |
| Opening-range size | 09:15–09:30 high − low, in ATR units | Too small = no interest; too big = move already done |
| VWAP position, RS since the open | Price vs session VWAP; % change vs Nifty since 09:15 | Direction and leadership |

**Output:** a ranked shortlist of **10–15 stocks** per day, plus the index futures. The intraday strategies (I1 ORB, I2 VWAP pullback, I4 RS leaders) run only on this list, with the regime layer setting size and which strategies are on.

---

## 3. Putting both layers together

```
                       ┌───────────────── Regime layer (market) ─────────────────┐
   India VIX level & percentile · VIX change · Nifty realised vol · breadth · event calendar (RBI, Fed, results, expiry)
                       └──────────┬──────────────────────────────────────────────┘
                                  │  strategy ON/OFF · size multiplier · stop width (ATR multiples)
                                  ▼
  Base universe (F&O stocks + index futures, filters) ──► Pre-market score ──► 09:30 confirmation (RVOL, OR, VWAP)
                                                                                        │
                                                                                        ▼
                                              Top 10–15 "in play" ──► I1 ORB · I2 VWAP pullback · I4 RS leaders
                                                                                        │
                                                      risk: 0.5% per trade · daily loss limit · flat by 15:10
```

**Regime → strategy mapping (starting hypotheses, to be tested in E19):**

| Regime (VIX 1-year percentile) | Typical market behaviour | Candidate tilt |
|---|---|---|
| Low (< 33rd pct) | Narrow ranges, more mean reversion, fewer trend days | Fewer ORB trades (require narrow CPR + RVOL ≥ 2); VWAP mean-reversion; index premium selling with strict tail caps |
| Normal (33rd–66th) | Mixed | Full strategy set |
| High (> 66th pct) | Bigger ranges, more trend days, bigger gaps | ORB / trend-following on in-play stocks, wider ATR stops, **half size**; no naked short options overnight |
| Stress (VIX spike > +20% in a day, or top 5%) | Gap risk, spreads widen, correlations go to 1 | Trade the index only, or stand aside; no new short-premium positions |

**Why not simply "pick the most volatile stocks"?**
- Without liquidity you get slippage and circuit locks.
- Without a catalyst or RVOL you get noise: high ATR stocks that chop.
- Volatility also widens stops, so **ATR-based sizing** is required to keep rupee risk constant. More volatility does not mean more profit per unit of risk. The backtest decides whether it does (E18).

---

## 4. How to backtest this (new experiments, added to the backlog)

Everything here is computed **point-in-time**:
- pre-market features use data up to the previous close (plus the open print);
- the 09:30 confirmation uses bars up to the 09:30 bar's close;
- catalysts use the archive's `published_at` ≤ decision time.

| ID | Question | Method | Data | Reject if… |
|---|---|---|---|---|
| **E17** | Does VIX predict *intraday* behaviour? | Event study by VIX tercile (1-year percentile): Nifty intraday range, trend-day frequency (close in the top/bottom 20% of the range), ORB follow-through rate, gap size | INDIAVIX D + Nifty 1m (both downloadable via OpenAlgo now) | No monotonic relation between VIX tercile and range / trend-day frequency |
| **E18** | Does "in-play" selection beat trading the whole universe? | Run I1 ORB (15m signals, live-parity latency) on (a) all F&O stocks, (b) top-15 by pre-market score, (c) top-15 by 09:30 RVOL, (d) (b)+(c) | 1m bars for the F&O universe; ATR, RVOL from Historify | (b)–(d) don't improve expectancy after costs by ≥ 0.1R, or trade count too low (< 300) |
| **E19** | Does a VIX regime switch improve risk-adjusted returns? | Split E14/E18 results by VIX tercile (buckets fixed **before** looking); compare always-on vs regime-filtered (Sharpe, max DD, profit factor) | as above | Improvement only in-sample, or depends on the exact thresholds |
| **E20** | Do filings/news catalysts make stocks trend intraday? | Compare intraday range, RVOL and ORB results for stocks with an important filing in the prior 18 h vs without | **News & Filings archive** (use `nse_announcements` backfill for history) + 1m bars | No difference in range/RVOL, or no edge after costs |
| **E21** | Beta-aware sizing in high VIX | Same strategy, size ∝ 1/ATR vs size ∝ 1/(ATR × β) on high-VIX days | D + 1m bars | No reduction in drawdown |

**Minimum evidence before going live** (same funnel as Track T):
- ≥ 300 trades out of sample;
- profit factor ≥ 1.2 at 2× slippage;
- no VIX tercile with a large negative contribution;
- 20–40 sessions of paper trading with a 1:1 signal parity check.

---

## 5. Data you can start collecting today

| Data | How | Status |
|---|---|---|
| **India VIX** (D and 1m) | Add `INDIAVIX` on `NSE_INDEX` to a watchlist and download (verify the exact symbol spelling in your OpenAlgo search) | Available now |
| Nifty / Bank Nifty (D, 1m) + F&O stocks (D, 1m) | Watchlist "Intraday universe" → download D (10 years) + 1m (as deep as Angel One serves) | Available now (the roll-up audit is still pending) |
| **Catalysts** (results, orders, ratings, deals, pledges, news) | **News & Filings archive**, running automatically; use backfill for `nse_announcements` history | **Built this round** |
| F&O list, ban list, lot sizes | Refdata job (HLD phase 0/3). The ban list can be added as a feeds source now (Settings JSON). | Next |
| Pre-open prices (09:00–09:08) | Not in broker history: capture with a scheduled quote snapshot at 09:08 | Next (small) |
| Stock IV | From option chains (forward collection, HLD phase 3) | Later |

**Implementation estimate for the scanner:** about **5–6 engineer-days**. That covers:
- the feature code, shared by backtest and live;
- a scheduled pre-market run at 09:05 and a confirmation run at 09:31;
- a "Today's in-play list" view;
- E17–E21 as backtests on the HLD simulator.

It can start as soon as HLD phases 0–2 land; E17 can run even earlier, since it only needs INDIAVIX and Nifty bars.
