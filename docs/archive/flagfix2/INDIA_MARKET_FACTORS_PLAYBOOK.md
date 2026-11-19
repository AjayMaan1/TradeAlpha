# What I Check Before a Trade: An Indian-Market Factor Playbook
### Trading vs investing, what can be backtested, and a worksheet to set your priorities

| | |
|---|---|
| **Voice** | An Indian-market practitioner, 15 years across cash, F&O and long-term portfolios (NSE/BSE) |
| **Purpose** | A complete list of the factors that move Indian stocks and indices: what each tells you, on which horizon it matters, where the data comes from, **whether it can be backtested honestly**, and whether it belongs in trading or in long-term investing |
| **Companion doc** | `BACKTESTING_AND_SIGNALS_HLD.md`. The tiers and `available_at` rules below map directly onto that design. |
| **Not advice** | This is a framework for building systems and setting priorities, not a recommendation to buy or sell anything. Dates and figures in the examples are approximate and should be checked before you use them in research. |

---

## Contents
0. [How to read this](#0-how-to-read-this)
1. [The mental model: top-down, then price decides](#1-the-mental-model-top-down-then-price-decides)
2. [Factor catalogue (15 layers)](#2-factor-catalogue-15-layers)
3. [Shocks: a history, and how to handle them](#3-shocks-a-history-and-how-to-handle-them)
4. [Horizon matrix: which factors matter for which holding period](#4-horizon-matrix-which-factors-matter-for-which-holding-period)
5. [What can honestly be backtested](#5-what-can-honestly-be-backtested)
6. [Long-term investing framework](#6-long-term-investing-framework)
7. [My pre-trade routine and per-trade checklist](#7-my-pre-trade-routine-and-per-trade-checklist)
8. [Data-source map (India) and fit with Historify](#8-data-source-map-india-and-fit-with-historify)
9. [Priority worksheet (fill this in)](#9-priority-worksheet-fill-this-in)

---

## 0. How to read this

**Horizons**
- **I** = intraday (minutes to hours)
- **S** = swing (2 days to 3 weeks)
- **P** = positional (1 to 6 months)
- **L** = long-term investing (1 year or more)

**Backtestability tiers.** These match the build phases in the HLD.

| Tier | Meaning | Typical use |
|---|---|---|
| **A: Direct** | Quantitative, long point-in-time history, cheap or free, exact timestamps. Backtest as a rule. | Signal generation |
| **B: With effort** | Quantitative, but needs point-in-time (PIT) data: announcement dates, first-release values, as-reported figures. Often paid, or needs careful scraping. | Filters, ranking, event strategies |
| **C: Event study only** | Too rare or too different each time to fit rules. Study the average reaction and the drawdowns. | Risk rules, position sizing, "don't hold through X" |
| **D: Judgement** | Qualitative, or no usable history. | Discretionary overlay, vetoes, long-term conviction |

**The golden rule** (from HLD §5.2): every data point has an **`available_at`**, the moment you could actually have known it. Most false backtest results in India come from using quarterly results, macro prints or index changes before they were public.

---

## 1. The mental model: top-down, then price decides

```
 Global risk appetite  ──►  India macro & policy  ──►  Flows (FII / DII / retail)
        │                         │                            │
        ▼                         ▼                            ▼
 Geopolitics / shocks ──►  Sector cycle & policy  ──►  Company fundamentals & events
                                                               │
                                                               ▼
                       Positioning (OI, options, participant data, delivery)
                                                               │
                                                               ▼
                       PRICE + VOLUME (trend, levels, breadth)  ◄── final arbiter for trading
                                                               │
                                                               ▼
                       Risk: size, stop, event calendar, liquidity, margin
```

Fifteen years have taught me these principles:
1. **For trading, price is the final arbiter.** Macro and fundamentals tell you *where to look* and *which side to prefer*. Price and volume tell you *when*. A great story with a falling price is a falling price.
2. **For investing, the business decides, and price only sets the entry.** Good businesses with clean balance sheets compound; the market eventually prices that in.
3. **Events are mostly a risk-management input, not an alpha input.** You rarely predict RBI, election or war outcomes. You *size* for them.
4. **India is flow-driven in the short run.** FII selling, SIP inflows, index rebalances and F&O positioning often matter more for the next four weeks than earnings do.
5. **Nifty is a financials index with an IT and energy tail.** Financials are about a third of the weight. Bank Nifty, HDFC Bank, ICICI Bank, Reliance and the IT majors tell you most of what Nifty will do. Always know what the heavyweights are doing.
6. **Concentration and governance risk are higher than in developed markets.** Promoter-run groups, pledges and regulatory actions can cut a stock in half overnight, with no technical warning.

---

## 2. Factor catalogue (15 layers)

Each table lists: what to watch · why it matters in India · how to measure it · horizon · tier · `available_at` notes.

### 2.1 Global and regional / geographic
| Factor | Why it matters for India | How to measure | Horizon | Tier |
|---|---|---|---|---|
| **US overnight session** (S&P 500, Nasdaq, Dow) | Sets the opening gap. IT follows the Nasdaq and US tech spending. | % change at the US close (about 01:30–02:30 IST) | I, S | A |
| **GIFT Nifty** (formerly SGX Nifty) | Trades almost round the clock; the best estimate of the Nifty open | GIFT Nifty vs the previous Nifty close at 08:45 IST | I | A (needs a GIFT data source) |
| **Indian ADRs** (Infosys, Wipro, HDFC Bank, ICICI Bank) | Overnight reaction to news that broke after the Indian close | ADR % change, converted with USD/INR | I | A |
| **Asian open** (Nikkei, Kospi, Hang Seng) | Regional risk tone at 09:00 IST | % change at 09:00 | I | A |
| **US Fed / FOMC, US CPI and payrolls** | Rate differential → FII flows → INR → Nifty. US CPI/NFP land around 18:00–19:00 IST; the FOMC around 23:30–00:30 IST, so the **reaction shows up in the next day's gap**. | Event calendar plus surprise vs consensus | I, S, P | B (surprise needs consensus data) / C |
| **US 10-year yield, DXY** | A rising DXY and US yields usually mean FII outflows from emerging markets and a weaker INR | Level, 20-day change | S, P | A |
| **China** (stimulus, PMI, property, steel output) | Sets metals prices (Tata Steel, JSW, Hindalco, Vedanta, NMDC). "Sell India, buy China" rotations: stimulus in late Sep–Oct 2024 came with record FPI selling in India in Oct 2024. | China PMI, LME/SGX metal prices, Chinese stimulus announcements | S, P | A (prices) / C (policy) |
| **Crude oil** (Brent) | India imports about 85% of its oil. Drives the current account, INR, inflation, OMC margins, and costs for paints, tyres, aviation and chemicals. | Brent in USD and in INR, 20-day change | S, P, L | A |
| **Gold** | India is a major importer. Affects the trade deficit and gold-loan NBFCs (Muthoot, Manappuram) and jewellers (Titan). | MCX gold, USD gold | S, P | A |
| **Middle East / Red Sea** | Crude and freight costs, Gulf remittances, shipping times for exporters | Brent, freight indices, news | S, P | C |
| **US trade policy / tariffs** | 2025 US tariffs on India (reported as rising to 50% in Aug 2025) hit textiles, gems and jewellery, shrimp, auto parts and chemicals. | Tariff schedule, sector export share to the US | P, L | C / D |
| **US visa policy** (H-1B) | IT services costs and onsite model. The Sept 2025 H-1B fee announcement hit IT stocks. | Event | S, P | C |
| **Europe** (demand, CBAM carbon border tax) | IT deal flow (BFSI, retail clients); CBAM affects steel and aluminium exports from 2026 | Euro-area PMI, policy timelines | P, L | C / D |
| **Yen carry trade / global deleveraging** | Sudden global sell-offs (5 Aug 2024) hit even "domestic" India | VIX, USDJPY, cross-asset moves | I, S | A (prices) / C |
| **Neighbourhood** (Pakistan, China border, Bangladesh, Sri Lanka) | War-risk gaps and defence rallies. Galwan (2020) led to import curbs and Chinese app bans. Bangladesh unrest (2024) shifted textile orders to Indian firms. | Event, sector exposure | S, P | C |
| **Domestic regional** (state elections, floods, local strikes, state policies on liquor, power tariffs, mining) | Single-stock and sector shocks: liquor makers, discoms, miners, plant-level strikes | Event, company's revenue by state | S, P | C / D |

### 2.2 Domestic macro
| Factor | Why it matters | Release rhythm | Horizon | Tier | `available_at` caveat |
|---|---|---|---|---|---|
| **CPI inflation** (MoSPI) | Drives RBI policy, which drives rate-sensitive sectors (banks, NBFCs, autos, real estate) | Monthly, around the 12th, **after market hours** | S, P | B | Use **first-release** values (later revisions are look-ahead); reaction appears the next day |
| **WPI** | Input-cost pressure on margins | Monthly, around the 14th | P | B | Same |
| **GDP** | Earnings-cycle backdrop | Quarterly, end of the second month after the quarter | P, L | B | Heavily revised; first release only |
| **IIP** (industrial production) | Capital goods, manufacturing | Monthly, lagged | P | B | Revised |
| **PMI** (manufacturing, services) | Early read on the cycle | 1st and 3rd business day of the month | S, P | B | |
| **GST collections** | Consumption and formalisation proxy | Around the 1st of the month | P | B | |
| **Credit and deposit growth** (RBI) | Bank NIMs, liquidity, loan-to-deposit stress | Fortnightly | P, L | B | |
| **Forex reserves, INR** | Ability to defend the currency; FII comfort | Weekly (Fridays); INR live | S, P | A (INR) / B | |
| **Trade deficit, current account deficit** | INR pressure; oil and gold imports | Monthly / quarterly | P, L | B | |
| **Fiscal deficit, government capex** | Capital goods, infrastructure, defence, railway order flow | Monthly (CGA) plus the Budget | P, L | B | |
| **Monsoon** (IMD forecast, progress, spatial spread) + kharif sowing | Rural demand: FMCG, 2-wheelers, tractors (M&M, Escorts), fertilisers, agrochemicals, rural lenders. Also food inflation. | Forecasts Apr–Jun; weekly progress Jun–Sep | S, P | B | |
| **Auto sales** (company monthly, SIAM/FADA, **Vahan registrations**) | Demand proxy; Vahan gives near-daily registrations, useful alternative data | Monthly (1st); Vahan daily | S, P | B | |
| **Power demand, e-way bills, fuel consumption** (PPAC) | Activity proxies | Daily to monthly | P | B | |

### 2.3 Monetary policy and liquidity
| Factor | Why it matters | Horizon | Tier |
|---|---|---|---|
| **RBI MPC decisions** (repo rate, stance, CRR; 6 meetings a year, announced around 10:00 IST *during market hours*) | The biggest scheduled domestic event for banks, NBFCs, real estate and autos. The surprise vs consensus, the stance wording and any CRR move matter more than the decision itself. | I, S, P | B (decision) / C (stance nuance) |
| **System liquidity** (LAF surplus/deficit, VRR/VRRR auctions, OMOs) | Tight liquidity pushes up short rates and pressures banks and NBFCs; surplus liquidity supports risk-taking | S, P | B |
| **Government bond yields** (10-year G-sec), corporate bond spreads | Valuation discount rate; NBFC funding costs. India's inclusion in the JPMorgan bond index (from June 2024) brought FPI debt flows. | P, L | A (if you collect the series) |
| **RBI regulatory actions** (risk weights on unsecured loans, Nov 2023; business curbs on specific lenders) | Sector re-rating and single-stock crashes: the Paytm Payments Bank curbs (Jan 2024), Kotak's digital-onboarding ban (Apr 2024) and IIFL's gold-loan ban (Mar 2024) | S, P | C |

### 2.4 Fiscal, policy and regulatory
| Factor | Examples | Horizon | Tier |
|---|---|---|---|
| **Union Budget** (1 Feb; July in an election year) | Capex allocation (infra, railways, defence), taxes (Jul 2024: STCG 15→20%, LTCG 10→12.5%, higher F&O STT), PLI schemes, customs duties | I, S, P | C (day volatility) / B (sector allocations) |
| **Tax and GST changes** | The Sept 2019 corporate tax cut (Nifty about +5% in a day). The GST rate rationalisation effective 22 Sept 2025 lifted autos and consumer durables. | S, P | C |
| **Sector policy** | PLI (electronics, pharma APIs, solar), defence indigenisation lists, EV subsidies (FAME/PM E-DRIVE), export bans and duties (rice, sugar, wheat, onion), the windfall tax on crude (2022), coal and power rules, telecom AGR | P, L | C / D |
| **SEBI market rules** | F&O tightening (Nov 2024 onwards), the surveillance frameworks, the SME IPO crackdown, and the July 2025 interim order against Jane Street, which cut expiry-day volumes | S, P | C (regime change: split backtests at these dates) |
| **Disinvestment / PSU policy** | OFS supply overhang; PSU re-rating cycles (2021–24) | P, L | C / D |
| **Administered prices** (APM gas, fertiliser subsidy, fuel pricing) | ONGC, OIL, OMCs, fertiliser companies, city gas distributors | P, L | C |

### 2.5 Political and geopolitical shocks
| Type | What I do | Horizon | Tier |
|---|---|---|---|
| **General election results** | Reduce gross exposure and overnight short options into results day. 2009: upper circuit. 4 June 2024: Nifty fell about 6% on the day, then recovered within weeks. Exit polls add a gap the day before. | I, S | C |
| **State elections** (especially big states, or those signalling the national mood) | Smaller, sector-specific moves (PSUs, infrastructure, freebies → fiscal worries) | S | C |
| **Wars and terror events** (Kargil 1999, Uri 2016, Balakot 2019, Pahalgam / Operation Sindoor Apr–May 2025) | India's market usually dips and **recovers quickly** when the conflict stays limited. The May 2025 ceasefire was followed by one of Nifty's biggest one-day gains in years. Defence stocks rerate. Hedge gap risk; don't panic-sell quality. | I, S | C |
| **Policy shocks** (demonetisation, Nov 2016; COVID lockdown, Mar 2020; farm-law protests) | Demonetisation hit consumption and NBFCs; the COVID crash took Nifty down about 38% from peak to trough in weeks. Liquidity matters more than valuation in a crash. | S, P | C |
| **Scandal / contagion** (Satyam 2009, IL&FS 2018 → NBFC crisis, Yes Bank 2020, Adani–Hindenburg Jan 2023) | Group and sector contagion, funding freezes. Check lender exposure, group holdings and pledges. | S, P | C |
| **Government stability / coalition politics** | Risk to policy continuity feeds into valuation multiples | P, L | D |

### 2.6 Flows and positioning
| Factor | Why it matters | Source | Horizon | Tier |
|---|---|---|---|---|
| **FII / FPI cash flows** | The marginal price-setter for large caps; persistent selling caps rallies | NSE provisional FII/DII data (evening, same day); NSDL (daily, by asset class) | S, P | **A** (long daily history) |
| **DII / mutual-fund flows, SIP inflows** (AMFI monthly; SIPs now a structural monthly inflow, above ₹25k crore at 2025 levels) | Domestic money has absorbed FII selling since 2021, so corrections are shallower | AMFI monthly | P, L | B |
| **Participant-wise OI** (FII / DII / Pro / Client positions in index futures and options) | **FII index-futures long/short ratio** is a widely used swing indicator; extremes mark turns | NSE daily archives (evening) | S | **A** |
| **FPI sector-wise holdings** (NSDL, fortnightly) | Which sectors FPIs are adding or cutting | NSDL | P | B |
| **MF portfolio disclosures** (monthly) | Smart-money accumulation in mid and small caps | AMC/AMFI monthly files | P, L | B |
| **Index rebalancing** (Nifty semi-annual: Mar/Sep; MSCI quarterly: Feb/May/Aug/Nov; FTSE) | Forced passive flows; inclusions run up before the effective date, exclusions get sold | NSE/MSCI announcements, with announcement and effective dates | S | **B** (very backtestable with PIT announcement dates) |
| **Bulk and block deals, promoter / insider trades** (SAST / insider-trading disclosures) | Promoter buying is a strong signal; a PE exit creates supply; the block-deal discount matters | NSE/BSE daily | S, P | B |
| **Margin trading book (MTF), retail F&O participation, IPO frenzy** (subscription multiples, grey-market premium) | Sentiment and froth gauge; extremes precede corrections, especially in small caps | Exchange / broker data, IPO data | P | B / C |
| **Lock-in expiries** (IPO anchor 30/90 days, pre-IPO shareholders) | Supply overhang in new listings | IPO documents, calendar | S | B |

### 2.7 Sector factors (a driver map)
| Sector (≈ Nifty weight order) | Key drivers | Leading / monthly indicators | Macro sensitivity |
|---|---|---|---|
| **Private banks** | Credit growth, NIM, CASA, asset quality (GNPA/NNPA, slippages, credit cost), LDR | RBI credit/deposit data, quarterly results | Rates ↑ NIM short-term, then asset quality; liquidity |
| **PSU banks** | Same, plus government policy, recoveries, dividend payout | Same | Capex cycle; recapitalisation history |
| **NBFCs / housing finance** | Cost of funds, AUM growth, asset quality, unsecured-loan stress, RBI rules | Bond spreads, RBI actions | Very rate- and liquidity-sensitive |
| **IT services** | US/EU tech spend, deal wins (TCV), guidance, attrition, USD/INR, GenAI disruption | US bank and tech earnings, Accenture results (early signal), Nasdaq | INR ↓ helps; US recession hurts |
| **Oil & gas / Reliance** | GRMs, marketing margins, petchem spreads, telecom ARPU (Jio), retail | Singapore GRM, crude, cracks | Crude ↑ hurts OMCs, helps upstream (ONGC, OIL) |
| **Auto** | Volumes (2W/PV/CV/tractor), mix, commodity costs, EV transition, rural demand | Monthly sales on the 1st, Vahan, dealer inventory (FADA) | Rates, monsoon, steel and aluminium prices |
| **FMCG** | Volume growth (rural vs urban), input costs (palm oil, crude derivatives, wheat), distribution | Nielsen commentary, monsoon | Inflation, monsoon |
| **Pharma / healthcare** | US generics pricing, USFDA inspections (483s, warning letters, OAI), domestic formulation growth, CDMO demand, hospitals (ARPOB, occupancy) | USFDA database, company filings | INR ↓ helps exporters |
| **Metals & mining** | China demand, LME prices, steel spreads, import duties and safeguard duties, coking-coal costs | LME, China PMI, domestic HRC prices | Global cycle, INR |
| **Cement** | Realisations per tonne, volumes, fuel costs (petcoke, coal), consolidation (Adani vs UltraTech) | Dealer price checks, monsoon (construction slows) | Infrastructure capex, housing |
| **Capital goods / defence / railways / EMS** | Order inflows and **order book to bill ratio**, execution, margins, government capex | Ministry orders, exchange filings of order wins | Government capex, PLI |
| **Power / utilities / renewables** | Peak demand, plant load factor (PLF), merchant tariffs (IEX), capacity additions, coal | Grid-India daily demand, IEX prices | Heatwaves, rates (capital-intensive) |
| **Telecom** | ARPU, tariff hikes (Jul 2024), subscriber adds, spectrum and AGR dues | TRAI monthly data | Regulatory |
| **Real estate** | Pre-sales, collections, launches, inventory months | Quarterly business updates, PropEquity-type data | Rates |
| **Consumer durables / retail / QSR** | Urban discretionary demand, GST changes, seasonality (summer for ACs, festive season) | Company updates | Inflation, income tax changes |
| **Chemicals** | China+1, Chinese dumping, inventory cycles, agrochemical channel inventory | Chinese chemical prices, export data | Global cycle |
| **Capital-market stocks** (exchanges, brokers, AMCs, depositories, registrars) | Volumes (especially F&O), demat additions, AUM, SEBI rules | Daily exchange volumes, AMFI | SEBI F&O rules (Nov 2024) |
| **Insurance** | VNB margin, APE growth, regulatory changes (surrender rules, GST on premiums) | IRDAI monthly | Rates, regulation |
| **Textiles / exports** | Cotton prices, US/EU tariffs, Bangladesh and Vietnam competition | Export data, tariff news | INR, US trade policy |

**Sector rotation signal (Tier A):** sector index relative strength vs Nifty (ratio and its slope) on weekly bars, plus breadth inside the sector. This is the most backtestable and reliable sector tool I use.

### 2.8 Company fundamentals
**Income statement**
- Revenue growth: YoY and QoQ, organic vs acquired, volume vs price.
- Gross and EBITDA margins, operating leverage.
- **Other income as a share of PBT** (a low-quality earnings flag).
- Exceptional items, effective tax rate, PAT growth, EPS growth.
- Segment mix.

**Balance sheet**
- Net debt, **debt/equity**, **net debt/EBITDA**, interest coverage.
- Working capital: receivable, inventory and payable days; the cash conversion cycle.
- **Contingent liabilities** (often large in India: tax disputes, guarantees to group companies).
- Goodwill, capital work in progress (CWIP).
- Loans to related parties.

**Cash flow**
- **CFO/EBITDA** and **CFO/PAT** over 3–5 years. Persistent low conversion is the single best accounting red flag.
- Free cash flow after capex, and how capex is funded.
- Dividend payout.

**Return and efficiency**
- ROE, **ROCE**, ROIC (vs cost of capital, roughly 12–13% in India), asset turnover.
- DuPont breakdown: is ROE coming from leverage?

**Valuation**
- P/E, EV/EBITDA, P/B (for financials), PEG, FCF yield, dividend yield.
- Each **vs the stock's own 5–10-year band** and vs peers.
- Market cap / GDP and Nifty P/E bands for the market as a whole.

**Bank / NBFC-specific**
- NIM, cost of funds, CASA ratio, credit growth, GNPA/NNPA, provision coverage ratio (PCR), slippage ratio, credit cost, restructured book, capital adequacy (CAR / CET1), liquidity coverage ratio (LCR), loan-to-deposit ratio (LDR).
- Unsecured-loan share; microfinance (MFI) stress.

**Order-book businesses** (capital goods, defence, EPC, railways)
- Order book / trailing revenue (years of visibility).
- Order inflow growth, execution rate, margin on new orders.
- Customer concentration (government share).

**Earnings dynamics**
- **Earnings surprise** vs consensus.
- **Analyst estimate revisions** (upgrades and downgrades over 1–3 months).
- Guidance changes.
- Post-results drift: stocks that beat and are revised up tend to keep drifting for weeks.

| Use | Horizon | Tier |
|---|---|---|
| Result-day trading (gap and reaction) | I, S | B (needs the filing timestamp and PIT numbers) |
| Earnings momentum / revisions | S, P | B (needs PIT consensus, usually paid) |
| Quality, value and growth screens | P, L | B (needs PIT, as-reported fundamentals, not restated) |

**`available_at` = the exchange filing timestamp of the results** (SEBI LODR requires filing within 30 minutes of the board meeting ending). Results often come **during market hours**. Never assume "results known at quarter end".

### 2.9 Governance and ownership (India-specific; heavy weight for me)
| Factor | Red / green flags | Horizon | Tier |
|---|---|---|---|
| **Promoter holding trend** | Steady or rising is green. Steady selling is a yellow-to-red flag. | P, L | B (quarterly shareholding with filing dates) |
| **Promoter pledge** (% of promoter holding pledged) | High or rising pledges are a **red flag**: forced selling in a downturn (several group collapses started here) | S, P, L | B |
| **Related-party transactions**, loans to group companies, complex structures | Red | L | D (or B with effort) |
| **Auditor resignation or qualification**, frequent auditor changes, delayed results | Strong red flag, often an early warning | S, P, L | B (filings) / D |
| **Equity dilution history** (QIPs, preferential allotments, warrants to promoters) | Frequent dilution is red; preferential allotment to promoters at a premium is green | P, L | B |
| **Capital allocation** (unrelated diversification, acquisitions, buyback and dividend habits) | | L | D |
| **Management credibility** (guidance vs delivery, conference-call consistency) | | L | D |
| **Regulatory and legal** (SEBI orders, ED/CBI/tax raids, NCLT cases) | Event risk | S, P, L | C |
| **MF / FII holding changes** | Institutional accumulation is green | P, L | B |

### 2.10 Corporate announcements and events
| Event | Typical pattern | Horizon | Tier |
|---|---|---|---|
| Quarterly results | Gap, then drift in the direction of the surprise; the conference-call tone matters | I, S | B |
| Large order wins (capital goods, defence, EPC, IT deal wins) | Positive gap; size vs revenue matters | I, S | B (filing timestamp; parse the order value) |
| Buyback (tender vs open market; buyback taxation changed in Oct 2024) | Support for the price, arbitrage around the record date | S | B |
| Dividends, bonus issues, splits | Mostly cosmetic; bonus and split run-ups come from retail enthusiasm | S | B (needs corporate-action adjustment) |
| Rights issue / QIP / OFS | Supply and dilution; a QIP discount caps upside | S | B |
| M&A, demerger (e.g. Reliance → Jio Financial, 2023), restructuring | Re-rating, special pre-open price discovery on demerger | S, P | C |
| Credit rating change (CRISIL, ICRA, CARE, India Ratings) | Downgrades, especially to below investment grade, are a severe negative for NBFCs and infrastructure | S, P | B |
| Management change (CEO or CFO exit) | Often negative, especially an unexpected CFO exit | S | B / C |
| USFDA outcomes (483, warning letter, import alert, clearance) | Big moves for pharma | S, P | B (with the FDA database) |
| Index inclusion / exclusion (Nifty, Sensex, MSCI, F&O segment entry/exit) | Pre-effective-date run-up; exclusion selling | S | **B, one of the best event strategies** |
| Surveillance framework inclusion (ASM/GSM), move to T2T | Liquidity crunch, margin up to 100%; avoid or exit | S | **B** (NSE publishes lists) |
| F&O ban (MWPL > 95%) | No new positions; squeezes can happen | I, S | **A** |
| Ex-date of a corporate action | Mechanical price drop; adjust your data | all | A (data hygiene, not alpha) |

**Timestamp sources:** NSE and BSE corporate-announcement feeds carry exact filing times. **Exchange filings are the best point-in-time "news" database in India, and it's free.**

### 2.11 Derivatives and OI (my core intraday and swing toolkit)
| Factor | Interpretation | Horizon | Tier |
|---|---|---|---|
| **Futures price + OI build-up** | Price ↑ OI ↑ = long build-up; price ↓ OI ↑ = short build-up; price ↑ OI ↓ = short covering; price ↓ OI ↓ = long unwinding | I, S | A (with OI history) |
| **Options OI concentration** | Highest call-OI strike = resistance; highest put-OI strike = support. Shifts in these strikes during the day are more informative than their levels. | I, S | A (chain history) |
| **PCR** (OI and volume; index and stock) | Contrarian at extremes: a very high PCR means too many puts sold or bought, depending on context. Best read as a change, not a level. | I, S | A |
| **Max pain** | Weak pinning tendency near expiry; I use it as context only | I | A |
| **India VIX** | Level and direction. Above about 20 is high-risk; a spike often marks capitulation; a low VIX favours premium selling but with tail risk. | I, S, P | A |
| **IV, IV rank / percentile, skew, term structure** | Option pricing regime; put skew steepening = hedging demand | S | A (needs IV computation) |
| **Basis / cost of carry, rollover % and rollover cost** (expiry week) | A high rollover with rising cost shows conviction in the longs; a discount to spot shows bearish positioning | S | A |
| **Participant-wise OI** (FII index futures long/short) | See §2.6 | S | A |
| **Expiry dynamics** (weekly Nifty expiry on NSE; Sensex on BSE; the weekday has changed several times, so check the current calendar) | Gamma moves, pinning, premium decay; expiry-day rules changed in Nov 2024 and Feb 2025 | I | A (split backtests by regime) |

### 2.12 Order book and market microstructure
| Factor | Reality check | Horizon | Tier |
|---|---|---|---|
| **Market depth** (top-5 bid/ask publicly; 20 levels from some brokers) | Useful for **execution** (spread, slippage, iceberg detection). Unreliable as a signal: displayed orders get pulled, and spoofing exists. | I | **D for history.** No historical depth in Historify or from the broker. Tick/depth history is a paid NSE product. Forward collection is possible. |
| **Total buy vs sell quantity** | Noisy; pending orders, not executed ones | I | D |
| **Pre-open session** (09:00–09:08 order entry; equilibrium price at 09:08) | Gap estimate for stocks; large pre-open imbalances on news | I | B (needs pre-open data capture) |
| **Bid-ask spread / impact cost** | Decides whether a strategy is tradeable at size; options spreads widen far OTM and near expiry | I, S | B (forward collection calibrates the slippage model, HLD §10.3) |
| **Upper / lower circuit** (2/5/10/20% bands for non-F&O stocks) | Locked stocks can't be exited; small caps can hit lower circuit for days | I, S | A (detect in 1m bars) |
| **Block-deal window, closing-session trades** | Large trades at the close or at open windows | I | B |
| **Volume profile / VWAP** | Where volume traded: fair value, intraday support and resistance | I, S | A |
| **Delivery %** (NSE bhavcopy) | **A high delivery % on an up-move means genuine accumulation**, a strongly India-specific and backtestable factor | S, P | **A** |

### 2.13 Price, volume, technicals, breadth and sentiment
- **Trend:** moving averages (SMA, EMA, WMA, HMA), MA stack, Supertrend, ADX, higher highs and higher lows, trend alignment across D / 1h / 15m. **Tier A.**
- **Levels:** previous day's high, low and close; CPR and pivots; opening range; 52-week high and low; round numbers (Nifty 100s and 500s, where big option OI sits). **Tier A.**
- **Momentum and relative strength:** RS vs Nifty and the sector, 3/6/12-month momentum, RS ranking. **Tier A.** This is also the best-documented systematic factor in India; NSE runs momentum indices such as Nifty200 Momentum 30.
- **Volatility:** ATR for stops and sizing, Bollinger squeezes, volatility contraction patterns. **Tier A.**
- **Volume:** volume expansion on breakouts, delivery %, OBV. **Tier A.**
- **Breadth:** advance/decline, % of stocks above the 50/200 DMA, new highs vs new lows, small-cap vs large-cap ratio. **Tier A** (compute across a point-in-time universe).
- **Sentiment:** India VIX, PCR, IPO subscription frenzy, retail F&O participation, media tone. **A / B / C** depending on the measure.

### 2.14 Seasonality and calendar
| Effect | Notes | Tier |
|---|---|---|
| Expiry week / expiry day | Volatility, rollover, pinning | A |
| Budget day / week | Pre-Budget run-up and sector bets; high intraday volatility | C (few events) |
| Results season (mid-Jan, Apr–May, Jul, Oct) | Stock dispersion rises | B |
| Festive season, Diwali / Muhurat trading | Consumption stocks; Muhurat is a symbolic, low-volume session | C |
| Fiscal year-end (March) | Tax-loss selling, advance-tax liquidity squeeze, window dressing | B / C |
| Month-end / quarter-end, SIP dates | Flow effects | B |
| Index rebalancing dates | See §2.6 | B |
| Monsoon season | Rural-theme seasonality | B / C |

### 2.15 Market-structure rules (must-know filters, not signals)
- **ASM / GSM surveillance:** stocks under surveillance get higher margins and trade restrictions. Filter them out of systematic strategies.
- **T2T / trade-for-trade segment:** no intraday netting. Avoid for intraday.
- **F&O ban list:** no fresh positions.
- **Circuit bands** and price-band revisions.
- **MIS square-off times; no overnight short in cash.**
- **Physical settlement of stock F&O:** close or roll before expiry week.
- **Lot-size and contract-value revisions:** your position size changes mechanically.

---

## 3. Shocks: a history, and how to handle them

Approximate figures for Nifty; check the exact numbers in your data before you use them in research.

| Date | Shock | Type | Immediate reaction | What followed | Lesson |
|---|---|---|---|---|---|
| May 2004 | Election surprise (UPA win, Left support) | Political | Intraday circuit, a crash of about 15% | Recovered within months | Election surprises gap hard; liquidity disappears |
| Sep 2008 – Mar 2009 | Global financial crisis | Global | More than 50% drawdown | Multi-year recovery | Leverage kills; cash is a position |
| 18 May 2009 | Election result (stable government) | Political | Upper circuit (+17% on Sensex) | Continued rally | Positive surprises also gap; shorts get trapped |
| Jan 2009 | Satyam fraud | Governance | Stock −78% in a day | Sector contagion was limited | Governance risk is binary; diversify |
| Aug 2013 | Taper tantrum / INR crisis | Global + macro | INR at record lows; banks crushed | Recovery after RBI action | Watch INR, CAD and FII flows together |
| 8 Nov 2016 | Demonetisation | Policy | About −6% over weeks; NBFCs and consumption hit | Recovered in months | Policy shocks hit specific sectors more than the index |
| Sep 2018 | IL&FS default → DHFL crash | Credit | NBFC and housing-finance crash, liquidity freeze | Long NBFC derating | Funding structure (ALM mismatch) matters |
| 20 Sep 2019 | Corporate tax cut | Policy (positive) | Nifty about +5% in a day | Sustained for some months | Shorts must respect policy-surprise risk |
| Mar 2020 | COVID crash | Global / health | About −38% peak to trough; a −13% day | V-shaped recovery with liquidity | Stay solvent; staggered buying in crashes works for long-term money |
| Feb 2022 | Russia–Ukraine | Geopolitical | Sharp sell-off; crude spike | Recovered, but crude-sensitive stocks lagged | Map the crude channel |
| Jan 2023 | Adani–Hindenburg report | Governance / group | Group stocks −50 to −80%; index impact limited | Partial recovery | Group contagion, pledges, FPI holding structure |
| 4 Jun 2024 | Election results (no single-party majority) | Political | About −6% on the day | Recovered within weeks | Exit polls can mislead; size down into results |
| 5 Aug 2024 | Yen carry unwind | Global | About −2.7% | Recovered quickly | Global deleveraging hits India too |
| Oct 2024 – Feb 2025 | Record FPI outflows + earnings slowdown | Flows | Nifty correction (roughly −15% peak to trough); small caps much worse | Recovery from Mar 2025 | Flows plus earnings downgrades together are powerful |
| Apr – May 2025 | Pahalgam attack → Operation Sindoor → ceasefire | Geopolitical | Brief dip | Sharp rally after the ceasefire; defence stocks rerated | Limited conflicts are usually buying opportunities for the index |
| Jul 2025 | SEBI interim order vs Jane Street | Regulatory | Capital-market stocks hit; expiry volumes dropped | | Regulatory regime shifts change option dynamics |
| Aug – Sep 2025 | US tariffs (reported 50%), H-1B fee | Trade / global | Exporters and IT hit | | Map revenue exposure by geography |

**How I handle shocks systematically**

Shocks are **Tier C**: use event studies for **risk rules**, not for entries.
1. **Known-date events** (Budget, RBI, election results, Fed, big results): reduce gross exposure, avoid naked short options overnight into the event, widen stops or cut size, and don't initiate new swing positions a day before. **This is backtestable as a rule** ("flat or half size into event X"): compare equity curves with and without it.
2. **Unknown-date shocks** (war, fraud, pandemic): you cannot time them. Protect with:
   - position-size caps;
   - per-stock and per-group concentration limits;
   - no single-group exposure above X%;
   - a portfolio stop or drawdown circuit breaker;
   - long options as tail hedges when India VIX is cheap.
3. **After the shock:** a VIX spike plus extreme PCR plus FII capitulation is usually where long-term money should be *adding*, in stages, to quality names. Traders should wait for price to confirm: a reclaim of the 20-day MA and breadth recovery.
4. **In the backtester:** tag shock windows (table above) as **regimes**, and report strategy performance inside and outside them (HLD §11.2).

---

## 4. Horizon matrix: which factors matter for which holding period

●●● = primary driver · ●● = important · ● = context only · (blank) = ignore

| Factor group | Intraday | Swing | Positional | Long-term |
|---|---|---|---|---|
| Global overnight cues, GIFT Nifty, ADRs | ●●● | ●● | ● | |
| US Fed / yields / DXY / crude / INR | ● | ●● | ●●● | ●● |
| China, metals, global cycle | ● | ●● | ●●● | ●● |
| Domestic macro prints (CPI, GDP, IIP, PMI) | ● (event days) | ● | ●● | ●● |
| RBI policy and liquidity | ●● (policy day) | ●● | ●●● | ●● |
| Budget and government policy | ●● (Budget day) | ●● | ●●● | ●●● (structural themes) |
| Political / geopolitical shocks | ●●● (risk) | ●●● (risk) | ●● | ● (opportunity) |
| FII/DII flows, participant OI | ●● | ●●● | ●●● | ● |
| Index rebalance, block deals, lock-ins | ● | ●●● | ●● | |
| Sector cycle and rotation | ● | ●● | ●●● | ●●● |
| Company fundamentals (P&L, BS, CF) | ● (result day) | ● | ●● | ●●● |
| Earnings surprise and revisions | ●● (result day) | ●●● | ●●● | ●● |
| Governance, promoter pledge, ownership | ● (filter) | ●● (filter) | ●● | ●●● |
| Corporate announcements | ●●● | ●●● | ●● | ● |
| F&O OI, PCR, VIX, IV | ●●● | ●●● | ●● | ● |
| Order book / depth / spread | ●●● (execution) | ● | | |
| Price trend, levels, volume | ●●● | ●●● | ●●● | ●● (entry timing) |
| Delivery %, breadth | ●● | ●●● | ●● | ● |
| Valuation (P/E vs history, peers) | | ● | ●● | ●●● |
| Seasonality / calendar | ●● | ●● | ● | |
| Market-structure filters (ASM, ban, circuits) | ●●● | ●●● | ●● | ● |

---

## 5. What can honestly be backtested

### 5.1 Five tests a factor must pass
1. **Quantifiable:** a number, not an opinion.
2. **Point-in-time history:** values **as they were known then**, not revised or restated.
   - GDP, IIP and CPI get revised.
   - Company financials get restated (Ind-AS transition in 2016–17, mergers).
   - Screener-style databases show the *latest* figures, not as-reported ones.
3. **Exact `available_at`:** results filing timestamp, macro release time, index-change announcement date (not the effective date).
4. **Enough occurrences:**
   - 10 years of daily bars is about 2,500 observations per stock, which is good.
   - RBI policy gives about 60 events, which is borderline.
   - General elections give 3 events, which is not a strategy.
5. **Regime stability:** market rules changed (F&O rules Nov 2024 and Feb 2025, lot sizes, expiry weekdays, taxes in Jul 2024, T+1 settlement in 2023). Split backtests at these dates.

### 5.2 Tier summary (what goes where)
| Tier A: backtest directly | Tier B: backtest with PIT effort | Tier C: event study → risk rules | Tier D: judgement / not backtestable |
|---|---|---|---|
| OHLCV on all timeframes, indicators (MA/WMA, RSI, ATR, Supertrend, VWAP, CPR) | Quarterly results (as reported, with filing timestamps) | Elections (general, state) | Management quality, credibility |
| Relative strength, momentum, sector rotation | Earnings surprise and estimate revisions (needs paid PIT consensus) | Wars, terror events, border tensions | Policy direction, political judgement |
| Breadth (A/D, % above DMA) over a PIT universe | Shareholding pattern, promoter pledge, MF holdings (quarterly/monthly with filing dates) | Pandemics, crashes, frauds | Conference-call tone (possible with NLP, but data is hard) |
| India VIX, F&O OI / PCR / basis / rollover / IV (forward collection + bhavcopy) | Macro releases (first-release vintages + release dates), RBI decisions vs consensus | Policy shocks (demonetisation, tax cuts) | Governance "smell" (related parties, complexity) |
| FII/DII cash flows, participant-wise OI | Index inclusion/exclusion (announcement → effective) | Regulatory actions on specific entities | Live order-book reading (no history) |
| Delivery %, bulk/block deals | Corporate actions, buybacks, QIPs, rating changes, order wins (filings) | Global shocks (carry unwind, tariffs) | Moat, industry structure |
| Calendar effects (expiry, month-end, pre-holiday) | ASM/GSM lists, F&O ban history, lot/tick history | | |
| Circuit detection, gap statistics | Commodity/FX cross-asset signals (crude, gold, INR, DXY, US yields) | | |

### 5.3 Common India-specific backtest traps
- **Results look-ahead:** using Q3 numbers on 31 Dec, when they were actually filed on 20 Jan.
- **Restated fundamentals** from current-snapshot databases.
- **Survivorship:** testing on today's Nifty 50 or today's F&O list. Use point-in-time membership, and keep delisted stocks (Jet Airways, DHFL, Yes Bank's reconstruction).
- **Unadjusted splits and bonuses** (very common among Indian mid caps).
- **Ignoring costs:** STT, stamp duty and GST make high-turnover intraday strategies unprofitable, especially options buying after Oct 2024.
- **Ignoring circuits and liquidity** in small caps: the backtest "sells" at a price nobody could get.
- **Using today's lot sizes** for historical F&O P&L.
- **Option prices from illiquid strikes** (zero-volume bars).
- **Macro revisions:** using final GDP instead of the first print.
- **Event-date confusion:** index-change *effective* date vs *announcement* date; ex-date vs record date.

---

## 6. Long-term investing framework

### 6.1 Where the return comes from
For long-term money, returns come from **earnings growth × change in valuation multiple + dividends**. In India the main long-term drivers have been:
- nominal GDP growth (about 10–12%);
- formalisation;
- financialisation of savings (SIP flows);
- infrastructure and manufacturing capex;
- premiumisation of consumption.

### 6.2 My long-term checklist (Tier B / D)
| Pillar | What I need | Quick thresholds (starting points, adjust by sector) |
|---|---|---|
| **Business quality** | ROCE / ROE consistently above the cost of capital; stable or rising margins; pricing power; moat | ROCE > 15% for 5+ years (non-financials); ROE > 14–15% (financials) with stable asset quality |
| **Growth runway** | Addressable market, market share, reinvestment opportunity | Revenue/EPS CAGR above nominal GDP |
| **Cash conversion** | CFO/PAT and CFO/EBITDA consistently high | 5-year cumulative CFO/PAT > 80% |
| **Balance sheet** | Low leverage, or leverage well covered | Net debt/EBITDA < 2 (non-financials); interest coverage > 5× |
| **Governance** | Clean audits, no or low pledge, fair related-party transactions, sensible capital allocation, steady promoter holding | Pledge ≈ 0%; no auditor exits |
| **Valuation** | Price vs own history, peers and growth (PEG); a margin of safety | Enter in the lower half of the 10-year P/E band, or on market-wide corrections |
| **Structural theme fit** | Manufacturing (PLI, China+1), defence, power and energy transition, financialisation, healthcare, digital, premium consumption, infrastructure | Thematic, judgement-driven |
| **Macro fit** | Interest-rate cycle, commodity cycle (for cyclicals, buy at peak P/E on trough earnings, not the reverse) | Cyclicals need cycle-aware valuation |
| **Portfolio construction** | Diversification by sector and group, position caps, rebalancing | No stock > 8–10%; no group > 15%; staggered entries |

### 6.3 What can be backtested for long-term investing
- **Factor portfolios** (quality, value, momentum, low volatility, or a mix), rebalanced quarterly or semi-annually over a point-in-time universe:
  - This is backtestable **if** you have PIT fundamentals.
  - NSE factor indices (Nifty200 Momentum 30, Nifty200 Quality 30, Nifty Alpha 50, Nifty100 Low Volatility 30) are useful **benchmarks and sanity checks**; they already encode backtested rules.
- **Entry and rebalance discipline:** staggered buying vs lump sum, valuation-band entries, trend-filtered allocation (for example, equity weight reduced when Nifty < 200 DMA). This is **very backtestable with price data alone (Tier A)**.
- **Limits:** 15–20 years of Indian data covers only 2–3 full cycles. Treat long-term backtests as **evidence, not proof**, and weight judgement (Tier D) more heavily than for trading.

---

## 7. My pre-trade routine and per-trade checklist

### 7.1 Evening before (after 18:00 IST)
1. **FII/DII cash data, participant-wise OI** (FII index futures long/short), F&O ban list for tomorrow, ASM/GSM changes.
2. **Bhavcopy:** delivery % leaders, OI build-up leaders (long build-up / short build-up lists).
3. **Corporate filings:** results declared after hours, order wins, block deals, pledge changes, rating actions **for everything I hold or plan to trade**.
4. **Event calendar for tomorrow and the whole holding period:** results dates, RBI, CPI, Fed, expiry, ex-dates, Budget, election dates, index-rebalance effective dates.
5. **Global:** US session, US yields, DXY, crude, gold, and the US data calendar for tonight.

### 7.2 Morning (08:30–09:15 IST)
1. GIFT Nifty vs the previous close; Asian markets; ADR moves; overnight news.
2. **Pre-open (09:00–09:08):** indicative opens of the heavyweights and my watchlist; gap size.
3. India VIX at the open; the option-chain OI map (max call/put OI strikes) for Nifty and Bank Nifty.
4. Plan: **trend day vs range day hypothesis**; key levels (previous day high/low/close, CPR, opening range).

### 7.3 During the session
- Breadth (A/D), sector leadership (which sectors lead relative to Nifty), heavyweight behaviour (HDFC Bank, ICICI Bank, Reliance, Infosys), VWAP position.
- OI shifts: are writers moving their strikes? Is the PCR changing?
- No new intraday positions in the last 15–20 minutes before MIS square-off; respect the 15:00–15:30 closing-price dynamics.

### 7.4 Per-trade checklist (before pressing buy or sell)
| # | Question | Must be |
|---|---|---|
| 1 | Is the **market regime** supportive? (Nifty trend, VIX, breadth) | Aligned, or the trade is sized down |
| 2 | Is the **sector** in relative strength (or weakness, for shorts)? | Aligned |
| 3 | Is the **stock's trend aligned on a higher timeframe** (D / 1h) with the signal timeframe? | Aligned |
| 4 | Is there a **clear level and a stop** (ATR-based or structure-based)? Reward-to-risk ≥ 2? | Yes |
| 5 | Does **positioning** support it? (OI build-up type, delivery %, PCR change) | Not contradicting |
| 6 | Is there an **event inside my holding period** (results, RBI, Budget, expiry, ex-date, election)? | Known and sized for, or avoided |
| 7 | **Liquidity:** spread, depth, circuit proximity, ASM/T2T/ban status | Tradeable at my size |
| 8 | **Governance / news flags** on this name? (pledge, auditor, regulatory) | None, or it's a deliberate event trade |
| 9 | **Position size** = risk budget (for example 0.5–1% of capital) / stop distance, rounded to lots | Within per-stock and per-sector caps |
| 10 | **Costs** (STT, charges, slippage) vs expected edge | Edge ≫ costs |
| 11 | **Portfolio correlation:** am I already long three banks? | Within exposure limits |
| 12 | **Exit plan:** target, trailing rule, time stop, what invalidates the idea | Written down |

Each line maps to a backtester feature:
- 1–3: the MTF trend filters (HLD §6, §8).
- 5: F&O analytics (HLD §7.5).
- 6: the event calendar with `available_at` (HLD §5.2).
- 7: execution rules (HLD §10.2).
- 9–11: portfolio and risk (HLD §10).

---

## 8. Data-source map (India) and fit with Historify

| Data | Source (free unless noted) | History | Historify today | HLD phase |
|---|---|---|---|---|
| OHLCV (1m, D), indices, India VIX | Broker via OpenAlgo | Years (1m depth to verify) | ✅ | — |
| Delivery %, official close, EOD F&O (OI, settle price) | NSE bhavcopy (legacy + UDiFF format) | 15+ years | ❌ | 3 |
| Intraday F&O incl. expired contracts | Forward collection (broker) / paid vendors | From the start of collection / vendor | Partial (active contracts only) | 3 |
| FII/DII cash, participant-wise OI, bulk/block deals, ban list, ASM/GSM | NSE archives | Many years | ❌ | New "market-data extras" refdata job |
| Corporate actions, announcements (with timestamps), results filings (XBRL), shareholding, pledge, SAST/insider trades | NSE/BSE filings | Many years | ❌ | 0 (corporate actions) / new "filings" ingestion |
| Index constituents and rebalance announcements | NSE Indices, MSCI press releases | Many years | ❌ | 0 / 1 |
| Macro (CPI, WPI, IIP, GDP, PMI, GST, credit, forex reserves, policy rates) | MoSPI, RBI DBIE, S&P Global, GSTN | Many years; **vintages need care** | ❌ | New "macro" refdata (REF instruments) |
| FPI flows and FPI sector holdings | NSDL | Many years | ❌ | Macro/flows refdata |
| MF flows and MF portfolios | AMFI, AMC disclosures | Many years | ❌ | Later |
| Auto registrations, power demand, fuel | Vahan, Grid-India, PPAC | Varies | ❌ | Optional alternative data |
| Global (US indices, yields, DXY, crude, gold, LME, USDINR) | Public sources / data vendors; some (MCX gold/crude, USDINR on CDS) are via OpenAlgo | Many years | Partial (MCX/CDS instruments possible) | Macro refdata |
| GIFT Nifty | NSE IX / vendors | Since 2023 under the GIFT name (SGX Nifty before) | ❌ | Optional |
| **PIT fundamentals** (as reported) | **Paid**: CMIE Prowess, ACE Equity, Capitaline; or build your own from XBRL filings | 15+ years | ❌ | Later (long-term module) |
| **Consensus estimates / revisions** | **Paid**: Bloomberg, LSEG/Refinitiv, FactSet; some Indian platforms | Varies | ❌ | Later / optional |
| Market depth / tick history | **Paid** (NSE Data & Analytics) or forward capture | From capture | ❌ | Optional (slippage calibration) |
| News sentiment | **Paid** archived news feeds; exchange filings are the free PIT alternative | Varies | ❌ | Optional |

**Storage pattern:**
- Time series such as FII flows, macro prints and breadth are stored as **REF instruments** in the existing bar store (HLD §5.6), each value stamped with its `available_at`.
- Event-type data (filings, corporate actions, index changes, rating actions) goes into an **`events` table** with `(instrument, kind, announced_at, effective_date, payload)`.

---

## 9. Priority worksheet (fill this in)

Score **Importance** 0–5 (0 = don't need, 5 = must-have) and mark your **horizons**. My default suggestions, from a trader's point of view with a systematic bias, are pre-filled in *italics*. Change them freely; this table becomes the build order.

### 9.1 What do you trade / invest in?
| Question | Your answer | My suggestion |
|---|---|---|
| Main horizon(s)? (I / S / P / L) | | *S + P for systematic; L for a separate core portfolio* |
| Instruments? (large-cap cash, mid/small cash, index futures, index options, stock F&O, MCX, currency) | | *Nifty 200 cash + index options* |
| Style? (trend-following, mean-reversion, breakout, event-driven, option selling, option buying, factor investing) | | *Trend/breakout (S/P) + option selling with strict event rules* |
| Capital per strategy and max drawdown tolerance? | | *Max DD target of 15–20% per strategy* |
| Budget for paid data? (none / < ₹X per month / institutional) | | *Start free (bhavcopy + filings); add paid PIT fundamentals only for the L module* |
| Manual execution, alerts only, or automated? | | *Signals + alerts first; paper trading; then semi-automated* |

### 9.2 Factor priorities
| # | Factor group | Tier | Default importance | Your importance (0–5) | Horizons you care about |
|---|---|---|---|---|---|
| 1 | Price/volume technicals, MTF trend (MA/WMA/Supertrend/ADX), levels | A | *5* | | |
| 2 | Relative strength, momentum, sector rotation | A | *5* | | |
| 3 | Breadth (A/D, % above DMA) over a PIT universe | A | *4* | | |
| 4 | India VIX, F&O OI / PCR / basis / rollover / IV | A | *5 if you trade F&O, else 3* | | |
| 5 | Delivery % and bhavcopy extras | A | *4* | | |
| 6 | FII/DII flows + participant-wise OI | A | *4* | | |
| 7 | Calendar/event awareness (expiry, results, RBI, Budget, Fed, elections) → risk rules | A/C | *5* | | |
| 8 | Market-structure filters (ASM/GSM, ban, circuits, T2T, lot/tick history) | A/B | *5 (hygiene)* | | |
| 9 | Corporate actions & survivorship-safe universe | B | *5 (hygiene)* | | |
| 10 | Index inclusion/exclusion events | B | *3* | | |
| 11 | Corporate announcements (order wins, buybacks, QIPs, ratings) | B | *3* | | |
| 12 | Quarterly results (PIT, filing timestamps) → result-day / drift strategies | B | *3* | | |
| 13 | Earnings surprise and estimate revisions (paid) | B | *2 (P), 3 (L)* | | |
| 14 | Shareholding, promoter pledge, MF/FII holding changes | B | *3 (filter), 4 (L)* | | |
| 15 | PIT fundamentals: quality/value screens (paid or built from XBRL) | B | *1 (trading), 5 (L)* | | |
| 16 | Macro prints (CPI, GDP, PMI, credit, GST) with vintages | B | *2* | | |
| 17 | Global cross-asset (crude, DXY, US yields, gold, INR, Nasdaq, GIFT Nifty) | A/B | *3* | | |
| 18 | RBI policy and liquidity | B/C | *3* | | |
| 19 | Political / geopolitical shock regimes (event studies, stress tests) | C | *4 (as risk rules)* | | |
| 20 | Sector-specific monthly data (auto sales, Vahan, power, telecom) | B | *2* | | |
| 21 | Order book / depth / spreads (forward capture) | D→B | *2 (execution quality)* | | |
| 22 | News / sentiment (filings-based first) | B/D | *1* | | |
| 23 | Governance judgement, management quality, themes | D | *1 (trading), 5 (L)* | | |

### 9.3 How your scores turn into a build plan
- **Factors 1–9 scored 4–5:** covered by HLD phases 0–3. Start with these; they are free-data, Tier A/B, and carry the most trading edge for the effort.
- **Factors 6, 10, 11, 12 or 14 scored ≥ 3:** add a **"market-data extras + filings" ingestion** (NSE archives and exchange announcements) with an `events` table. About 6–8 engineer-days.
- **Factors 13 or 15 scored ≥ 4:** start a **long-term / fundamentals module**. Either buy a PIT database or build XBRL parsing (substantial: 3–4 weeks). This is a separate track from trading.
- **Factors 16–18 scored ≥ 3:** add a **macro/flows REF-series** ingestion with release-date vintages. About 4–5 engineer-days.
- **Factor 19 scored ≥ 3:** add **regime tagging and event-risk rules** to the backtester (largely in HLD §11.2). About 2 engineer-days extra.
- **Factor 21 scored ≥ 3:** add **depth/spread forward capture** for slippage calibration (HLD phase 7).

Send back the filled worksheet (even just the importance column and your answers in 9.1). From it I'll produce a re-prioritised roadmap with the exact data sources, schemas and backtest experiments for your top factors.
