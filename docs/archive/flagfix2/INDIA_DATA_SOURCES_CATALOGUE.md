# Data Sources for Indian Markets: Fundamentals, News, Big-Investor Activity, Promoter Pledges and More
### A catalogue for both tracks (long-term and intraday/swing), plus the trading-priority recommendation

| | |
|---|---|
| **Builds on** | `TWO_TRACK_SIGNALS_DESIGN.md` (tracks L and T), `INDIA_MARKET_FACTORS_PLAYBOOK.md` (factor tiers), `BACKTESTING_AND_SIGNALS_HLD.md` (`available_at`, DataPortal) |
| **Important caveat** | The sandbox has **no internet access** (NSE, BSE, Screener and GDELT all failed to connect). This catalogue comes from practitioner knowledge, not live checks. **Prices, history depth, API availability, terms of use and recent regulatory thresholds change.** Items marked 🟡 must be verified before you commit money or engineering time. No prices are quoted, only cost tiers. |
| **Not advice** | Vendor names are options to evaluate, not endorsements. |

---

## Contents
0. [Summary](#0-summary)
1. [Legend and the three kinds of source](#1-legend-and-the-three-kinds-of-source)
2. [Catalogue by data need](#2-catalogue-by-data-need)
   - 2.1 [Company fundamentals](#21-company-fundamentals-financial-statements)
   - 2.2 [Shareholding, big investors, smart money](#22-shareholding-big-investors-smart-money)
   - 2.3 [Promoter pledging and encumbrance](#23-promoter-pledging-and-encumbrance)
   - 2.4 [Insider trading and takeover disclosures](#24-insider-trading-and-takeover-disclosures)
   - 2.5 [Corporate announcements and the event calendar](#25-corporate-announcements-and-the-event-calendar)
   - 2.6 [Financial news and sentiment](#26-financial-news-and-sentiment)
   - 2.7 [Concalls, presentations, annual reports, broker research](#27-concalls-presentations-annual-reports-broker-research)
   - 2.8 [Consensus estimates and revisions](#28-consensus-estimates-and-revisions)
   - 2.9 [Flows and positioning](#29-flows-and-positioning)
   - 2.10 [F&O, tick data and market microstructure](#210-fo-tick-data-and-market-microstructure)
   - 2.11 [Index, valuation and reference data](#211-index-valuation-and-reference-data)
   - 2.12 [Macro and policy](#212-macro-and-policy)
   - 2.13 [Sector and alternative data](#213-sector-and-alternative-data)
   - 2.14 [Regulatory, legal and credit risk](#214-regulatory-legal-and-credit-risk)
   - 2.15 [Open-source libraries](#215-open-source-libraries)
3. [How "big investor buying" is actually disclosed in India](#3-how-big-investor-buying-is-actually-disclosed-in-india)
4. [Promoter pledge: definitions, metrics, signals](#4-promoter-pledge-definitions-metrics-signals)
5. [Point-in-time fundamentals: build vs buy](#5-point-in-time-fundamentals-build-vs-buy)
6. [News pipeline design](#6-news-pipeline-design)
7. [Recommended stacks by budget](#7-recommended-stacks-by-budget)
8. [Licensing and compliance](#8-licensing-and-compliance)
9. [Integration into Historify](#9-integration-into-historify)
10. [Trading priority: swing first, then 15-minute intraday?](#10-trading-priority-swing-first-then-15-minute-intraday)
11. [Decisions and verification list](#11-decisions-and-verification-list)

---

## 0. Summary

1. **The exchanges are the primary, point-in-time source for almost everything event-like, and free.** NSE and BSE filings give, each with a filing timestamp:
   - results (including XBRL);
   - shareholding patterns, with named holders above 1%;
   - promoter pledges and encumbrance;
   - takeover (SAST) and insider-trading (PIT) disclosures;
   - bulk and block deals;
   - board-meeting dates;
   - credit-rating, order-win and management-change announcements;
   - earnings-call transcripts.

   Build this once. It feeds both tracks.
2. **Aggregators** (Screener, Trendlyne, Tijori, StockEdge, Moneycontrol, Tickertape) are excellent for *your manual research*. They are **not suitable for backtests**: they show the latest or restated numbers, not what was known on the date, and their terms usually forbid scraping.
3. **Paid India-focused vendors** provide clean, deep, licensed and API-accessible history:
   - fundamentals: ACE Equity, CMIE Prowess, Capitaline, CMOTS, Trendlyne's data API;
   - intraday and expired-options data: NSE Data & Analytics, Global Datafeeds, TrueData.

   Buy these **only for the gaps that matter**: point-in-time (PIT) fundamentals for Track L, and expired intraday options history for Track T.
4. **Consensus estimates** (earnings surprise, revisions) are effectively **institutional-only** with proper PIT history (LSEG I/B/E/S, FactSet, Bloomberg, S&P). Trendlyne offers an affordable, non-PIT approximation.
5. **News:**
   - exchange announcements are the free, timestamped event stream;
   - media RSS and **GDELT** (free, history since 2015) give context and sentiment;
   - a paid real-time wire (Informist, formerly Cogencis; Reuters; Bloomberg) is only worth it if you trade *news-driven intraday*.
6. **Trading priority:** yes, **swing first, then 15-minute intraday**, with two refinements (§10):
   - add a **"swing setup + 15m entry"** bridge stage in between;
   - do the **intraday *research* in parallel** from the start, since only the *live deployment* needs to wait.

   A full-time index intraday trader could use an exception: ≤ 5 instruments on 5-minute bars.

---

## 1. Legend and the three kinds of source

| Symbol | Meaning |
|---|---|
| **Cost** | `Free` · `₹` low (retail subscription) · `₹₹` mid (professional/API licence) · `₹₹₹` institutional |
| **PIT** | ★★★ as filed, with filing timestamp (backtest-safe) · ★★ dated, but values can be revised or restated · ★ latest snapshot only (not backtest-safe) |
| **Access** | `DL` bulk download/files · `API` official API · `Web` website only (scraping needs ToS review) · `Desk` desktop terminal |
| **Track** | **L** long-term · **T** intraday/swing |
| 🟡 | Verify availability, depth, price or terms before relying on it |

| Kind | Examples | Strength | Weakness | Use it for |
|---|---|---|---|---|
| **Primary / regulatory** | NSE, BSE, SEBI, RBI, MoSPI, NSDL, AMFI, MCA | Authoritative, free, timestamped (PIT ★★★) | Messy formats (PDF/XBRL/HTML), changing websites, anti-bot measures, you do the cleaning | Ground truth and backtest data |
| **Aggregators** | Screener, Trendlyne, Tijori, StockEdge, Tickertape, Moneycontrol | Clean, convenient, derived ratios, screeners | Latest or restated values (PIT ★), ToS limits, usually no API | Manual research, idea generation, sanity checks |
| **Paid vendors** | ACE Equity, CMIE Prowess, Capitaline, CMOTS, LSEG, FactSet, Bloomberg, S&P, GDFL, TrueData, NSE Data & Analytics | Depth, cleaning, standardisation, licensed APIs, some PIT | Cost, lock-in, PIT quality varies by vendor 🟡 | Gaps the primary sources can't fill cheaply |

---

## 2. Catalogue by data need

### 2.1 Company fundamentals (financial statements)
| # | Source | What you get | History | PIT | Cost | Access | Track | Notes |
|---|---|---|---|---|---|---|---|---|
| F1 | **NSE corporate filings: financial results** (incl. XBRL) | Quarterly/annual results, standalone + consolidated, as filed, with timestamp | XBRL for roughly the last decade 🟡; PDFs earlier | ★★★ | Free | Web/DL | L, T | **The PIT ground truth.** Results must be filed within 45 days of quarter end (60 days for annual audited). |
| F2 | **BSE corporate filings** (results, XBRL) | Same, plus BSE-only companies (many small caps) | Similar 🟡 | ★★★ | Free | Web/DL (BSE has public JSON endpoints used by many tools 🟡) | L, T | Needed for full coverage |
| F3 | **MCA21** (Ministry of Corporate Affairs) | Annual financial statements (AOC-4, XBRL for larger companies), charges (secured borrowings), directors; **unlisted subsidiaries** too | Many years | ★★★ (filing dates) | Per-document fee | Web | L | Deep governance and group work; heavy to process |
| F4 | **Annual reports** (exchange filings / company websites) | Notes: contingent liabilities, related parties, segments, auditor remarks | Many years | ★★★ | Free | DL (PDF) | L | Text/NLP heavy; best for red flags |
| F5 | **Screener.in** | 10+ years P&L/BS/CF, ratios, custom screens, Excel export (premium) | ~10–12 years | ★ | Free / ₹ | Web | L (manual) | Best free research tool; no API; ToS restricts scraping |
| F6 | **Trendlyne** | Fundamentals, scores (DVM), forecasts, alerts; **data API for businesses** 🟡 | ~10 years | ★ / ★★ 🟡 | ₹ / ₹₹ | Web / API 🟡 | L, T | Strong all-rounder; ask whether the API serves history as-reported |
| F7 | **Tijori Finance** | **Segment and operating KPIs** (volumes, store counts, order book), concall notes | Varies | ★ | ₹ | Web | L | Good for "business drivers", not just accounts |
| F8 | **Tickertape, Moneycontrol Pro, StockEdge, MarketSmith India, Finology Ticker** | Screens, ratios, ratings | Varies | ★ | Free / ₹ | Web/app | L (manual) | Idea generation only |
| F9 | **ACE Equity** (Accord Fintech) | Standardised fundamentals, shareholding, corporate actions, MF data; listed + unlisted; data feed/API | Long (1990s+) 🟡 | ★★ (ask for as-reported / PIT) 🟡 | ₹₹ | Desk / DL / API | L | Widely used by Indian AMCs and brokers |
| F10 | **CMIE Prowess IQ / ProwessDx** | Very deep Indian corporate database (listed + unlisted), ownership, standardised accounts | From about 1989 🟡 | ★★ 🟡 | ₹₹ / ₹₹₹ | Desk / bulk DL | L | Academic gold standard for Indian research; bulk licence for systematic use |
| F11 | **Capitaline** | Fundamentals, shareholding, corporate actions | Long 🟡 | ★★ 🟡 | ₹₹ | Desk / DL | L | Older, established |
| F12 | **CMOTS** | API-first Indian financial data (fundamentals, corporate actions, shareholding, news) used by many fintech apps | Varies 🟡 | ★★ 🟡 | ₹₹ | API | L, T | Good fit for a developer-built system; check history depth |
| F13 | **Global: LSEG (Refinitiv) Worldscope/Datastream, FactSet, S&P Capital IQ/Compustat Global, Bloomberg, Morningstar** | Global-standard fundamentals; **proper PIT histories** at FactSet, S&P Xpressfeed and LSEG 🟡 | Long | ★★★ (PIT products) | ₹₹₹ | Desk / API / feeds | L | Institutional; overkill unless you need consensus data too (§2.8) |
| F14 | **Global retail APIs: EODHD, Financial Modeling Prep, Twelve Data, Finnhub, Alpha Vantage, Yahoo Finance (`yfinance`, unofficial)** | Some NSE/BSE fundamentals | Short to medium | ★ | Free / ₹ | API | Prototype only | Indian coverage and quality are uneven; not for production backtests |

### 2.2 Shareholding, big investors, smart money
| # | Source | What you get | Frequency / deadline | PIT | Cost | Track |
|---|---|---|---|---|---|---|
| H1 | **Shareholding pattern** (NSE/BSE filings, XBRL) | Promoter / FII / DII / MF / insurance / retail split; **named public shareholders above 1%** (the source of every "superstar portfolio") | Quarterly, within **21 days** of quarter end | ★★★ | Free | L (T filter) |
| H2 | **Bulk deals** (NSE/BSE daily) | Client name, buy/sell, qty, price: single-day trades **> 0.5% of equity** | Same day (evening) | ★★★ | Free | T, L |
| H3 | **Block deals** (NSE/BSE) | Negotiated large trades in the block windows | Same day | ★★★ | Free | T, L |
| H4 | **MF portfolio disclosures** (AMC websites, AMFI) | Every scheme's holdings | **Monthly** (published within about 10 days of month end 🟡) | ★★★ (by disclosure date) | Free | L, T |
| H5 | **SAST Reg 29 disclosures** (exchange filings) | Acquirers crossing 5% and every ±2% change thereafter | Within 2 working days | ★★★ | Free | L, T |
| H6 | **Insider (PIT Reg 7) disclosures** | Promoters, directors, key staff trades | Within 2 trading days | ★★★ | Free | T, L |
| H7 | **Preferential allotments / warrants / QIPs** (board approvals, allotment filings) | Who is putting fresh money in, and at what price (promoter warrants at a premium = conviction) | Event | ★★★ | Free | L, T |
| H8 | **NSDL FPI monitor** | FPI flows (daily, aggregate); **sector-wise FPI holdings (fortnightly)** | Daily / fortnightly | ★★★ | Free | T, L |
| H9 | **Aggregated "superstar / big shark" portfolios** (Trendlyne, StockEdge, Moneycontrol, Screener) | Named-investor holdings over time (built from H1/H2) | Lags H1/H2 | ★ (convenience) | Free / ₹ | Manual research |
| H10 | **13F-style full institutional holdings** | **Not available in India.** Individual FPI positions are visible only above 1% (H1) or through bulk/block deals (H2/H3). | — | — | — | — |

### 2.3 Promoter pledging and encumbrance
| # | Source | What you get | Deadline | PIT | Cost | Track |
|---|---|---|---|---|---|---|
| P1 | **SAST Reg 31(1)/(2) encumbrance disclosures** (NSE/BSE filings) | Creation, invocation or release of a pledge or other encumbrance (incl. non-disposal undertakings), with counterparty and reason | Within **7 working days** | ★★★ | Free | L, T |
| P2 | **NSE/BSE consolidated pledge data pages** | Per company: promoter holding, shares pledged, % pledged of promoter holding and of total, as-of date | Updated on disclosures | ★★ (as-of dates) | Free | L, T |
| P3 | **Shareholding pattern** (H1) | Promoter shares pledged or encumbered at quarter end | Quarterly | ★★★ | Free | L |
| P4 | **SAST Reg 31(4) annual declaration** | Promoter confirms no other encumbrance | Annual | ★★★ | Free | L |
| P5 | **Aggregators** (Trendlyne, Screener, Moneycontrol, StockEdge) | Pledge % and trend, alerts | Lag | ★ | Free / ₹ | Manual |
| P6 | **Paid vendors** (ACE, Prowess, CMOTS) | Cleaned pledge history | — | ★★ 🟡 | ₹₹ | L |

Details and signal design are in §4.

### 2.4 Insider trading and takeover disclosures
| # | Source | What you get | Notes |
|---|---|---|---|
| I1 | **PIT Reg 7(2) continual disclosures** | Trades by promoters, directors and designated persons above the value threshold (₹10 lakh in a calendar quarter 🟡) | Promoter open-market buying is one of the most reliable insider signals in India |
| I2 | **SAST Reg 29** (H5) | Crossing 5%, changes of 2% | Large investors building stakes |
| I3 | **Open offers** (SAST Reg 3/4), delisting offers | Change of control, floor prices | Event-driven; arbitrage |
| I4 | **Buyback announcements and letters of offer** (SEBI/exchanges) | Size, price, method (tender vs open market); promoter participation | Tax treatment changed in Oct 2024 |
| I5 | **Trading-window closure announcements** | Tells you results are near | Calendar signal |

### 2.5 Corporate announcements and the event calendar
| # | Source | What you get | Latency | PIT | Track |
|---|---|---|---|---|---|
| E1 | **NSE corporate announcements feed** | Every LODR Reg 30 disclosure (orders, contracts, M&A, management changes, litigation, ratings, results, investor meets), **timestamped to the second** | Near real time (minutes) | ★★★ | **T and L, the core event stream** |
| E2 | **BSE corporate announcements** | Same, plus BSE-only companies; category tags | Near real time | ★★★ | T, L |
| E3 | **Board-meeting intimations / results calendar** (NSE/BSE) | **Results dates in advance** (board-meeting notice) | Days ahead | ★★★ | T (event filter), L (review cadence) |
| E4 | **Corporate actions** (NSE/BSE) | Dividends, splits, bonus, rights, record and ex-dates | Days ahead | ★★★ | Both (data hygiene) |
| E5 | **Credit rating agencies** (CRISIL, ICRA, CARE, India Ratings, Acuité) | Rating actions + rationales (also filed with exchanges) | Same day | ★★★ | L, T |
| E6 | **SEBI** orders, press releases, offer documents (DRHP/RHP), circulars | Enforcement, IPO pipeline, rule changes | Same day | ★★★ | Both |
| E7 | **Official macro/policy calendars** (RBI MPC dates, MoSPI release calendar, Budget, US Fed/CPI) | Scheduled macro events | Months ahead | ★★★ | Both |
| E8 | **NSE Indices / MSCI / FTSE announcements** | Index additions and deletions, effective dates | Weeks ahead | ★★★ | T (event trades), L |
| E9 | **Government orders** (PIB, Ministry of Defence, railways, GeM / CPPP tenders) | Order pipeline for capital goods, defence and EPC | Varies | ★★ | L, T |

### 2.6 Financial news and sentiment
| # | Source | Type | History | Latency | PIT | Cost | Track | Notes |
|---|---|---|---|---|---|---|---|---|
| N1 | **Exchange announcements** (E1/E2) | Primary corporate news | Years | Minutes | ★★★ | Free | T, L | **Start here**: structured, timestamped, no licensing grey zone |
| N2 | **PIB, RBI, SEBI, Ministry of Finance press releases** (RSS/web) | Government / regulator news | Years | Minutes | ★★★ | Free | Both | Policy shocks at the source |
| N3 | **Media RSS**: Economic Times Markets, Moneycontrol, Business Standard, Mint, Financial Express, BusinessLine, NDTV Profit, CNBC-TV18, Zee Business | Headlines + summaries | Little or no archive via RSS | Minutes | ★★ (publish time) | Free | T (context), L | Redistribution not allowed; personal use; store your own timestamped archive from day one |
| N4 | **GDELT 2.0** (Global Database of Events, Language and Tone) | Global news events, themes and tone, incl. Indian outlets; 15-minute updates | From 2015 | 15 min | ★★★ | Free | Both (research) | **The best free historical news dataset for backtesting** sentiment, news volume and geopolitical risk 🟡 |
| N5 | **Google News RSS**, NewsAPI.org, NewsAPI.ai / Event Registry, Marketaux, Finnhub | News APIs | Short to medium (paid tiers longer) | Minutes | ★★ | Free / ₹ / ₹₹ | T | India coverage varies 🟡 |
| N6 | **Informist (formerly Cogencis)** | Indian real-time financial newswire (used by dealers, treasuries) | Archive 🟡 | Seconds | ★★★ | ₹₹ / ₹₹₹ | T (news-driven intraday) | Worth it only for news-reaction intraday strategies |
| N7 | **LSEG/Reuters Machine Readable News, Bloomberg Event-Driven Feeds, Dow Jones (Factiva / DNA)** | Institutional wires with timestamped archives and sentiment | Long | Seconds | ★★★ | ₹₹₹ | T, L | Institutional |
| N8 | **PTI / IANS wires** | Domestic wires | — | Fast | ★★ | ₹₹ | — | Mostly via media licences |
| N9 | **Social**: X/Twitter (expensive API), Reddit (r/IndianStreetBets, r/IndiaInvestments), YouTube, Telegram | Retail chatter | Varies | Fast | ★★ | Free – ₹₹₹ | T (froth/contrarian only) | Low signal; pump-and-dump risk (SEBI has acted against such channels) |
| N10 | **Paid Indian research-news platforms** (ETPrime, The Ken, The Morning Context, Moneycontrol Pro) | Investigative and analytical | — | — | — | ₹ | L (manual) | Governance red flags often surface here first |

### 2.7 Concalls, presentations, annual reports, broker research
| # | Source | What you get | PIT | Cost | Notes |
|---|---|---|---|---|---|
| C1 | **Exchange filings**: earnings-call audio and transcripts, investor presentations | Management commentary, guidance, KPIs | ★★★ | Free | SEBI LODR requires listed companies to publish concall recordings and transcripts, and they are filed with the exchanges (current deadlines 🟡). **A free NLP goldmine.** |
| C2 | **Company investor-relations pages** | Presentations, fact sheets, monthly business updates (autos, cement, NBFC AUM) | ★★ | Free | Monthly business updates are great T/L data |
| C3 | **Trendlyne, Tijori, Screener concall links / summaries** | Convenience | ★ | Free / ₹ | Manual |
| C4 | **Broker research** (institutional brokers' reports, via your broker accounts; aggregated target prices on Trendlyne, MarketScreener) | Estimates, target prices, ratings | ★★ | Free (as a client) / ₹ | Redistribution restricted |
| C5 | **Annual reports** (F4), **secretarial audit / related-party filings** | Governance detail | ★★★ | Free | Red-flag extraction |

### 2.8 Consensus estimates and revisions
| # | Source | PIT history | Cost | Notes |
|---|---|---|---|---|
| K1 | **LSEG I/B/E/S** | ★★★ (estimate-level history) | ₹₹₹ | The reference dataset for earnings-surprise research |
| K2 | **FactSet Estimates, S&P Capital IQ Estimates, Bloomberg BEst** | ★★★ | ₹₹₹ | Institutional |
| K3 | **Trendlyne forecasts** (broker consensus) / **MarketScreener** | ★ / ★★ 🟡 | ₹ | Affordable; ask whether history snapshots exist |
| K4 | **Build your own** from the broker reports you receive | ★★★ (you timestamp them) | Your time | Works going forward only |
| K5 | **Proxy without consensus:** "surprise vs own history" (for example YoY EPS growth vs the trailing 4-quarter trend; standardised unexpected earnings (SUE) using a seasonal random walk) | ★★★ (from F1) | Free | **A practical substitute for backtests.** Post-earnings drift research often uses SUE. |

### 2.9 Flows and positioning
| # | Source | What you get | Frequency | Cost | Track |
|---|---|---|---|---|---|
| W1 | **NSE FII/DII provisional cash data** | Net buy/sell in the cash market | Daily (evening) | Free | T, L |
| W2 | **NSE participant-wise OI and volume** | FII / DII / Pro / Client positions in index and stock futures and options | Daily (evening) | Free | T |
| W3 | **NSDL FPI monitor** (H8) | Equity/debt flows; sector-wise holdings | Daily / fortnightly | Free | T, L |
| W4 | **AMFI** | MF category flows, SIP flows, AUM | Monthly | Free | L |
| W5 | **Security-wise delivery** (NSE bhavcopy) | Delivery qty and % | Daily | Free | T, L |
| W6 | **Short-selling disclosures** (NSE/BSE) | Institutional short-sale positions reported | Daily 🟡 | Free | T |
| W7 | **MTF (margin trading) data, securities lending (SLB)** | Leverage and borrow demand | Daily 🟡 | Free | T |
| W8 | **ASM/GSM, F&O ban lists** | Surveillance and MWPL bans | Daily | Free | T (filters) |

### 2.10 F&O, tick data and market microstructure
| # | Source | What you get | History | Cost | Track | Notes |
|---|---|---|---|---|---|---|
| M1 | **Your broker via OpenAlgo** (Angel One now) | 1m+ OHLCV for active contracts, quotes, depth, WebSocket | Active contracts; depth of history 🟡 | Free with account | T | Already in Historify |
| M2 | **NSE F&O bhavcopy** (+ legacy and UDiFF formats) | EOD OHLC, settle price, OI, ΔOI, contracts, for **all** contracts incl. expired | 15+ years | Free | T | HLD phase 3 |
| M3 | **NSE Data & Analytics** (NSE's data company) | Official historical tick-by-tick, snapshots, order-level data, corporate data products | Long | ₹₹ / ₹₹₹ | T | The authoritative source for tick and depth history |
| M4 | **Global Datafeeds (GDFL), TrueData, Accelpix** (exchange-authorised vendors) | Real-time and historical data incl. **expired options intraday** | Several years 🟡 | ₹ / ₹₹ | T | The usual retail-quant route to intraday options history |
| M5 | **Other broker APIs** (Zerodha Kite Connect, Upstox, Dhan, Fyers, ICICI Breeze) | Historical candles; some now offer **expired-contract history** 🟡 | Varies | Free / ₹ with account | T | Possible second OpenAlgo provider for expired F&O; check each API's current capabilities |
| M6 | **Options analytics platforms** (Sensibull, Opstra, Quantsapp; backtest platforms such as AlgoTest, Stockmock) | IV, Greeks, OI analytics; their own backtesters | Varies | ₹ | T | Use to **cross-check** your own option backtests; not a data feed |
| M7 | **India VIX history** | NSE / Historify (`NSE_INDEX`) | Since 2008 | Free | T, L | |
| M8 | **GIFT Nifty** (NSE IX) | Overnight index futures | Since 2023 as GIFT Nifty (SGX Nifty before) | Free / ₹ 🟡 | T | Gap prediction |
| M9 | **Market depth capture** (own, via the OpenAlgo WebSocket or quotes) | Spread and depth snapshots, collected forward | From the start of collection | Free | T | Slippage calibration (HLD §10.3) |

### 2.11 Index, valuation and reference data
| # | Source | What you get | Cost |
|---|---|---|---|
| R1 | **NSE Indices (niftyindices.com)** | Index levels incl. **TRI**, **P/E, P/B, dividend-yield history**, constituents, methodology, reconstitution notices | Free |
| R2 | **BSE / Asia Index** (Sensex family) | Same for BSE indices | Free |
| R3 | **NSE/BSE security masters, ISIN master (NSDL), symbol-change and delisting lists** | Identity across renames and delistings (survivorship) | Free |
| R4 | **NSE F&O contract master, lot-size circulars, market-wide position limit (MWPL) files** | Lot, tick and freeze history | Free |
| R5 | **Holiday and special-session circulars** (NSE/BSE/MCX) | Calendar history (HLD blocker B1) | Free |

### 2.12 Macro and policy
| # | Source | What you get | Cost |
|---|---|---|---|
| X1 | **RBI DBIE** (Database on Indian Economy) | Rates, liquidity, credit, forex reserves, BoP, payment-system data | Free |
| X2 | **MoSPI** (incl. its newer data portal/API 🟡) | CPI, IIP, GDP (with revisions; keep first releases) | Free |
| X3 | **Office of the Economic Adviser** | WPI | Free |
| X4 | **CGA, GSTN, Ministry of Commerce, PPAC** | Fiscal, GST, trade, fuel | Free |
| X5 | **S&P Global (HSBC India PMI)** | PMIs (headline free; history may be paid 🟡) | Free / ₹₹ |
| X6 | **Global: FRED (US macro, yields, DXY proxies), US BLS/BEA, Fed, CME FedWatch, ECB** | Global drivers | Free |
| X7 | **Economic calendars** (Investing.com, Trading Economics) | Release dates, consensus | Free / ₹₹ (API) |

### 2.13 Sector and alternative data
| Sector | Free sources (monthly unless noted) |
|---|---|
| Autos | Company monthly sales filings (1st of month), SIAM, FADA retail data, **Vahan dashboard (near-daily registrations)** |
| Power / utilities | Grid-India daily demand, CEA, IEX prices (daily) |
| Cement / steel / coal | Company updates, Joint Plant Committee (steel), Coal India monthly production filings |
| Telecom | TRAI subscriber data |
| Insurance | IRDAI monthly premiums |
| Aviation / travel | DGCA monthly traffic |
| Logistics | Indian Ports Association, railway freight (Ministry of Railways), e-way bills (GSTN) |
| Banking / payments | RBI sectoral credit, payment-system data (UPI, cards) |
| Real estate | Company pre-sales updates; paid: PropEquity, Anarock 🟡 |
| Consumption | Google Trends, app-download trackers (paid), Naukri JobSpeak (hiring) |
| Agriculture | IMD monsoon, kharif/rabi sowing (Ministry of Agriculture), fertiliser sales |
| Pharma | **USFDA**: inspection classifications, Form 483s, warning letters, import alerts, ANDA approvals (all free) |

### 2.14 Regulatory, legal and credit risk
| Source | Why |
|---|---|
| SEBI enforcement orders (interim, final, settlement) | Entity-level bans and manipulation cases |
| RBI enforcement (penalties, business restrictions on banks, NBFCs, payment companies) | Paytm/Kotak/IIFL-type shocks |
| NCLT / IBBI (insolvency data) | Distress, group contagion |
| Credit rating actions (E5) | Early distress warning, especially for NBFCs and infrastructure |
| Auditor resignations (exchange filings, with the mandated reasons) | Governance red flag |
| Stock-exchange surveillance (ASM/GSM, T2T moves) | Manipulation and liquidity risk |

### 2.15 Open-source libraries
These are helpers for fetching primary sources. Check the licence of each one (Historify is AGPL-3.0) and each site's terms.

| Library | Covers | Note |
|---|---|---|
| `jugaad-data` | NSE bhavcopies, historical prices, some live data | Popular; check maintenance status 🟡 |
| `nselib`, `nsepython` | NSE endpoints (FII/DII, option chain, announcements, indices) | Unofficial wrappers; they break when NSE changes its site |
| `bsedata` / BSE JSON endpoints | BSE quotes and announcements | Unofficial |
| `nsepy` | Older NSE history | Largely unmaintained; avoid for new work |
| XBRL parsers (`arelle` and others) | Parse results and shareholding XBRL | Needed for the build route (§5) |
| GDELT BigQuery / `gdeltdoc` | News events and tone | Free; BigQuery has usage costs |

Wrap these behind Historify's own fetchers, with rate limits and caching. Don't make them runtime dependencies of the core app.

---

## 3. How "big investor buying" is actually disclosed in India

There is no 13F-style full disclosure. You assemble the picture from these channels. Each has a threshold, a delay and an `available_at`:

| Channel | Who / what | Threshold | Disclosed by | `available_at` | Signal quality |
|---|---|---|---|---|---|
| **Bulk deal** | Any client | Single-day trade > 0.5% of equity | Broker → exchange, same day | Same evening | ★★★ fast, but one-day snapshot; check whether the same entity sells the next day |
| **Block deal** | Large negotiated trades in the block windows | Minimum order size set by SEBI (revised over time 🟡) | Exchange | Same day | ★★ often secondary sales or PE exits (supply) |
| **SAST Reg 29** | Acquirer crossing 5%, then every 2% change | 5% / ±2% | Acquirer → exchange, within 2 working days | Filing timestamp | ★★★ stake building is visible early |
| **Insider (PIT Reg 7)** | Promoters, directors, designated persons | > ₹10 lakh in a quarter 🟡 | Within 2 trading days | Filing timestamp | ★★★ **promoter open-market buying is strong** |
| **Shareholding pattern** | Named public holders > 1% | 1% | Company, within 21 days of quarter end | Filing timestamp (lags up to about 3 weeks after the quarter) | ★★ slow but comprehensive; source of "superstar" tracking |
| **MF monthly portfolios** | Every scheme | All holdings | AMCs, monthly (within about 10 days) | Publication date | ★★★ for mid/small caps: MF accumulation is persistent |
| **FPI aggregate** | All FPIs | — | NSDL daily; sector-wise fortnightly | Publication date | ★★ market- and sector-level only |
| **Preferential allotment / warrants** | Promoters, strategic investors | Event | Company | Filing timestamp | ★★★ promoter money at a premium = conviction |

**Signals worth testing** (Tier B, from the free filings):
- **Promoter buying cluster:** ≥ 2 promoter open-market buys in 30 days, with no promoter selling. Track T: swing drift. Track L: ownership score.
- **SAST stake building** by a known long-term investor crossing 5%.
- **Bulk-deal buy by a top institutional or "superstar" investor, with no reversal within 5 days.**
- **MF accumulation breadth:** the number of MF schemes adding the stock month on month (rising for 3 months).
- **Negative:** promoter selling, block-deal secondary sales, SAST stake reduction, or an exit by a known investor.

---

## 4. Promoter pledge: definitions, metrics, signals

### 4.1 What counts
- **Pledge:** shares given as collateral for a loan (often at the promoter or holding-company level).
- **Other encumbrance:** non-disposal undertakings (NDUs), liens and similar arrangements. These are disclosed under SAST Reg 31, and they matter because lenders can control them.
- **Events:** creation → (top-up on price falls) → **invocation** (the lender sells: a price shock) → release (positive).

### 4.2 Metrics (compute daily from P1 events, reconciled quarterly with P3)
| Metric | Formula | Use |
|---|---|---|
| `pledge_pct_promoter` | pledged promoter shares ÷ promoter shares | Red flag if > 20–25%; severe if > 50% |
| `pledge_pct_total` | pledged ÷ total shares | Supply overhang if it is invoked |
| `pledge_change_90d` | Δ `pledge_pct_promoter` over 90 days | A rising trend is worse than a stable level |
| `invocation_events_1y` | count of invocations | Severe red flag |
| `encumbrance_incl_ndu` | (pledge + NDU + other) ÷ promoter shares | Full picture |
| **Pledge-at-risk proximity** | current price vs the estimated price at the last top-up or invocation event | Cascade risk during a market fall (hard to estimate: loan-to-value ratios are not disclosed) |

### 4.3 Signals
- **Track L:** exclude (red-flag filter) when `pledge_pct_promoter` > 20% **or** `pledge_change_90d` > +10 percentage points **or** any invocation in the last 12 months. Release of a pledge can raise the ownership score.
- **Track T:**
  - no longs in stocks with recent invocations;
  - an invocation filing is a **short / avoid** event (test it as an event study);
  - in a market sell-off, filter out highly pledged stocks (forced-selling risk).
- **Backtest note:** the `available_at` is the **filing timestamp of the Reg 31 disclosure**, not the event date in the disclosure (promoters have 7 working days to disclose).

---

## 5. Point-in-time fundamentals: build vs buy

| | **Build from exchange XBRL** (F1/F2 + H1 + P1) | **Buy** (ACE / Prowess / CMOTS / Capitaline; or FactSet/S&P/LSEG for global-grade PIT) |
|---|---|---|
| PIT quality | ★★★ by construction (you store what was filed and when) | Varies. **Ask for as-reported values + filing dates + restatement flags** 🟡 |
| History | About the XBRL era (roughly the last decade 🟡); PDFs before that | Often 20–30 years |
| Coverage | All listed companies on NSE/BSE | All listed (+ unlisted for Prowess/ACE) |
| Standardisation | **Your problem.** Taxonomy changes (Ind-AS from 2016–17), standalone vs consolidated, banks vs NBFCs vs insurers have different formats. | Done for you (but check the mapping) |
| Effort | **≈ 15–20 engineer-days** to a usable core (results P&L + key balance-sheet items + shareholding), ongoing maintenance as formats change | ≈ 5 days integration per vendor API |
| Cost | Free data, engineering time | Subscription |
| Risk | Broken endpoints, formats and taxonomies; parsing errors | Vendor lock-in; licence restricts sharing; PIT claims need testing |
| Best for | Results-event strategies (T) + quarterly fundamentals (L) from about 2016 | Long-horizon L research (15–25 years) and standardised ratios |

**Recommendation:**
1. **Build the filings ingestion anyway** (results timestamps, shareholding, pledges, SAST, insider, announcements). It is needed for events and PIT timing even if you buy fundamentals, and it is free.
2. **Parse only the key XBRL fields first:** revenue, EBITDA, PAT, EPS, plus half-yearly balance-sheet items. That is enough for growth, surprise proxies (K5) and simple quality.
3. **Buy a vendor only after the price-only L experiments (E1–E5) show the L track is worth it.** Then pick one through a **trial and validation**:
   - choose 30 companies, including 5 delisted, 5 with restatements and 5 banks/NBFCs;
   - take 8 quarters each;
   - compare the vendor's values and dates against the XBRL filings;
   - accept only ≥ 98% matching values, correct filing dates, delisted coverage, and documented restatement handling.

**Vendor evaluation checklist:**
- as-reported vs restated values;
- filing timestamp per value;
- consolidated and standalone;
- delisted and merged companies included;
- corporate-action-adjusted share counts;
- bank/NBFC/insurance-specific fields;
- API and bulk-download limits;
- licence for internal systematic use and for storing the data locally;
- history depth;
- update latency after filing;
- support quality;
- price escalation terms.

---

## 6. News pipeline design

```
Sources (ranked by trust & PIT):
  1. NSE/BSE announcements (E1/E2)  ──┐   polled every 1–2 min in market hours, 15 min otherwise
  2. Regulators: SEBI/RBI/PIB/MoF    ──┤   RSS / page polling
  3. Media RSS (N3)                  ──┤   store headline + link + publish time (own archive, no redistribution)
  4. GDELT (N4)                      ──┤   15-min batches; history back to 2015 for research
  5. Paid wire (N6/N7), optional     ──┘
        ▼
  Normalise → entity linking (company name/ISIN/symbol → instrument_id) → de-duplicate (same story, many outlets)
        ▼
  Classify: results (beat/miss via parsed numbers), order win (value ÷ trailing revenue), rating action,
            management change, pledge/invocation, SAST/insider, regulatory action, M&A, litigation, guidance change
            (rules + a local LLM/NLP model; keep the model version in each record for reproducibility)
        ▼
  events table (instrument_id, kind, announced_at [= available_at], severity, payload, source, url, model_version)
        ▼
  Track T: event filters & event-driven setups (S6)    Track L: thesis-breaker alerts, ownership/quality updates
```

**Practical notes**
- **Timestamps:** use the **exchange dissemination time**, not the article time or the board-meeting time.
- **Entity linking** is the hard part for media news. Company names vary ("M&M", "Mahindra"). Exchange filings avoid this, because they carry the symbol.
- **Sentiment scores** are research features (Tier B). Backtest them only on the archived history you actually have: GDELT from 2015, your own RSS archive from the day you start. **Start archiving now.**
- **Latency budget for intraday news trading:** exchange feeds via polling take about 1–3 minutes; paid wires take seconds. Indian algos react within seconds to big announcements. At retail latency, trade the **drift after the first reaction**, not the first move.

---

## 7. Recommended stacks by budget

| Stack | Contents | Covers | Gaps |
|---|---|---|---|
| **0: Free (start now)** | NSE/BSE filings (announcements, results XBRL, shareholding, pledges, SAST, insider, bulk/block, board meetings, corporate actions), bhavcopy (equity + F&O), FII/DII + participant OI, NSDL FPI, AMFI/MF portfolios, NSE Indices (TRI, P/E, constituents), RBI/MoSPI, GDELT + own RSS archive, USFDA, rating agencies. Plus Screener/Trendlyne free tiers for manual research. | T almost fully (except expired intraday options history); L events, ownership, pledges, results (XBRL era), price-only strategies | Long PIT fundamental history, consensus estimates, expired intraday options |
| **1: Low (₹)** | Stack 0 + **Trendlyne/Tijori/Screener premium** (research productivity) + **an authorised intraday data vendor (GDFL/TrueData)** for expired options history 🟡 | + options intraday backtests | PIT fundamentals depth, consensus |
| **2: Mid (₹₹)** | Stack 1 + **one India fundamentals vendor with API** (ACE Equity / CMOTS / Prowess bulk, after a validation trial) + optional Trendlyne data API for forecasts | + 15–25 years of L research | True PIT consensus, real-time wire |
| **3: Institutional (₹₹₹)** | + LSEG / FactSet / Bloomberg / S&P (PIT fundamentals + I/B/E/S-grade estimates + machine-readable news) + NSE Data & Analytics tick/order data | Everything | Cost |

**My recommendation: Stack 0 now, Stack 1 when options intraday becomes a priority, Stack 2 only after the price-only L experiments justify it.**

---

## 8. Licensing and compliance

1. **Website terms:** NSE, BSE and most aggregators restrict automated scraping and redistribution.
   - Personal, low-rate, cached fetching of public files (bhavcopies, archives) is common practice, but **not a licence**. Respect rate limits and robots directives.
   - For commercial or redistributed use, licence the data (NSE Data & Analytics, BSE, vendors).
2. **Aggregators** (Screener, Trendlyne, etc.): use their UI exports or paid APIs within their terms. Don't build production pipelines on scraping them.
3. **Media content:** store headlines, links and your own derived labels. Don't republish articles.
4. **Open-source licences:** check every library's licence against AGPL-3.0 (Historify's licence).
5. **Signals to other people:** if signals or recommendations are ever shared with others (paid or even free, publicly), SEBI's Research Analyst / Investment Adviser rules may apply 🟡. For your own account this isn't an issue.
6. **Algo trading through broker APIs:** SEBI's framework for retail algorithmic trading (2025) adds broker-side requirements, such as order tagging, registration of certain strategies and API access controls. Check the current rules with your broker before automating live orders 🟡. Signals + manual or paper execution is unaffected.

---

## 9. Integration into Historify

| New ingestion job (BackgroundRunner kind) | Sources | Store | `available_at` | Track | Effort |
|---|---|---|---|---|---|
| `filings_sync` (every 2 min in market hours, 15 min otherwise) | E1, E2, E3, E5 (via filings) | `events` (meta) + raw documents in `instance/filings/` | dissemination timestamp | T, L | 5 d |
| `results_xbrl_parse` | F1, F2 | `fundamentals(instrument_id, period_end, statement, item, value, consolidated, filed_at, source, revision_of)` | `filed_at` | L (+T surprise) | 10–15 d (core fields first) |
| `shareholding_sync` | H1, P3 | `shareholding(instrument_id, quarter_end, category, holder_name, shares, pct, pledged, filed_at)` | `filed_at` | L | 3 d |
| `encumbrance_sync` | P1, P2 | `encumbrance_events(...)` → daily `pledge_metrics` REF series | `filed_at` | L, T | 2 d |
| `deals_insider_sync` | H2, H3, H5, H6 | `deals`, `insider_trades` | same evening / `filed_at` | T, L | 3 d |
| `mf_portfolio_sync` | H4 | `mf_holdings(scheme, instrument_id, month, qty, weight, published_at)` | `published_at` | L | 4 d (AMC formats vary) |
| `flows_sync` | W1–W8, H8 | REF series (FII/DII, participant OI, FPI sector holdings, ban/ASM) | publication time | T, L | 4 d |
| `news_sync` | N2, N3, N4 | `news_items` + classified `events` | publish / dissemination time | T, L | 5 d (excl. an NLP model) |
| Corporate actions, index data, calendar history | E4, R1–R5 | as in HLD phase 0 | — | both | in HLD |

**Total to add to the roadmap:** about **36–41 engineer-days** for the full free stack, excluding a vendor integration (about 5 days each).

**Suggested order:**
1. `filings_sync` + `encumbrance_sync` + `deals_insider_sync` + `flows_sync` (≈ 14 d; feeds both tracks' event and red-flag filters).
2. `shareholding_sync` + `news_sync` (RSS/GDELT archive), started early so history accumulates.
3. `results_xbrl_parse` + `mf_portfolio_sync`.

---

## 10. Trading priority: swing first, then 15-minute intraday?

**Short answer: yes, it is the right order for this system and this data stack, with two refinements.**

### 10.1 Why swing first
| Reason | Detail |
|---|---|
| **Data is complete and final** | Swing runs on end-of-day data that Historify already gets right: the official close from the 15:45 run, plus the bhavcopy's delivery % and OI in the evening. No latency problem, no partial bars. |
| **Latency doesn't matter** | Signals are computed after the close and executed the next day. REST polling is perfectly adequate. |
| **Better edge-to-cost ratio** | Swing trades target 3–10% moves. Costs are larger in *rupees* (delivery STT is 0.1% on each side, about 0.25–0.3% round trip with charges, vs about 0.05–0.1% for intraday), but much smaller **relative to the move**. Intraday strategies on 0.5–1% moves lose a large share of their edge to costs and slippage. Swing via stock futures is cheaper in STT, but brings lot sizes, margin and rolls. |
| **Operational simplicity** | You can execute manually the next morning. Angel One sessions **expire daily and need a TOTP re-login** (documented in `docs/providers/angelone.md`). Unattended intraday automation must handle that every day; swing doesn't depend on it. |
| **Easier, more honest research** | Fewer modelling assumptions: no intrabar fill ambiguity and no latency model. Your most valuable India-specific features (delivery %, OI build-up, FII data, results calendar, pledges) are EOD data anyway. |
| **Reuses everything** | The same machinery (regime, event calendar, MTF, costs) is later used by intraday |

### 10.2 Why 15-minute (not 1m/5m) for intraday
| Reason | Detail |
|---|---|
| **Fits the REST capacity** | A 200-symbol universe at 15m uses about 200 requests per 15 minutes, well inside the 144/min budget. 1m needs a WebSocket feed (HLD §12.3). |
| **Latency is a small fraction of the bar** | Bars are final 60 s after they close (`INTRADAY_FINAL_BUFFER`); the fetch takes about 1–2.5 minutes. That is about 10–15% of a 15m bar, vs 30–50% of a 5m bar. Live results will match backtests better. |
| **Less noise, fewer trades, lower cost drag** | 15m ORB, VWAP-pullback and relative-strength strategies are well-established on Indian stocks |
| **Realistic competition** | At sub-5-minute horizons you compete with co-located algos. At 15m, retail latency is not the deciding factor. |

### 10.3 Two refinements I'd make
1. **Add a bridge stage: "swing setup on D, entry trigger on 15m".** The daily setup (for example an S1 pullback or S2 breakout) is confirmed by a 15m trigger the next day, such as an opening-range break or a VWAP reclaim. This:
   - improves entry prices and tightens stops (better R-multiples);
   - **exercises the entire intraday live pipeline** (15m bars, latency, the TOTP/auth circuit, parity checks) on positions sized for swing, where a missed bar is not fatal.

   By the time you go pure intraday, the plumbing is proven.
2. **Run intraday *research* in parallel from the beginning; only the *live deployment* comes later.**
   - Intraday backtests need only 1m data, which you already have. Experiment E14 (15m ORB) can run in the first research batch.
   - **Start intraday paper trading early:** intraday produces many signals a day, so parity validation is *faster* than for swing. Swing needs 2–3 months of paper trading to collect about 50 trades; intraday collects that in a few weeks.

### 10.4 When I would change the order
| Situation | Then |
|---|---|
| You are a full-time, screen-based **index** trader (Nifty/Bank Nifty futures or options only) | Intraday first is fine. It covers ≤ 5 instruments, so Historify's existing "follow live" mode (max 5 series) supports **5m** bars over REST. Options research needs the forward-collected chain or a vendor (§2.10). |
| Your edge idea is **news/event-reaction intraday** | You need a fast news source (paid wire or very frequent filings polling) and probably a WebSocket feed. Build those first, or choose a different first strategy. |
| You can't monitor or automate during market hours | Swing only. Intraday stays in backtest or paper until automation is proven. |

### 10.5 Recommended sequence
| Stage | Live mode | What | Depends on |
|---|---|---|---|
| 1 | Research only | Swing (E9 S1, E10 S2 + delivery, E11 S4) **and** intraday (E14 ORB 15m) backtests in parallel | HLD phases 0–2 + bhavcopy |
| 2 | Paper | **Swing EOD signals** (the winners of stage 1); event-risk rule E16 | Signal service, event calendar |
| 3 | Paper | **Swing D setup + 15m entry trigger** (the bridge) | Intraday 15m pipeline |
| 4 | Paper → small live | **Pure 15m intraday** on F&O stocks / index futures | Stage 3 parity proven |
| 5 | Paper → small live | **Options intraday** (E15) | F&O data (forward collection + vendor), F&O simulator |

---

## 11. Decisions and verification list

### 11.1 Decisions for you
| # | Decision | Recommendation |
|---|---|---|
| 1 | Data budget stack (§7) | **Stack 0 now**, then Stack 1 when options intraday is prioritised |
| 2 | PIT fundamentals | **Build filings + core XBRL fields now.** Trial one India vendor after E1–E5 (price-only L) results. |
| 3 | Start archiving news and filings now (even before using them) | **Yes.** History you don't record can't be backtested later. |
| 4 | Consensus estimates | **Use the SUE proxy (K5) first.** Institutional estimates only if earnings-revision strategies become central. |
| 5 | Second provider for expired F&O (another broker API via OpenAlgo, or GDFL/TrueData) | Decide when options move up the priority list; verify current capabilities first |
| 6 | Trading sequence | **Swing → swing + 15m entry → 15m intraday → options**, with intraday research in parallel from day one |

### 11.2 Verify (🟡) before committing
- [ ] Current NSE/BSE endpoints, terms of use and rate limits for announcements, XBRL, pledge, SAST, insider, bulk/block, and the participant OI archives
- [ ] XBRL history depth for results and shareholding (which year each starts)
- [ ] Current regulatory thresholds and deadlines: block-deal minimum size, insider disclosure threshold, concall-transcript deadline, MF portfolio disclosure timeline
- [ ] Vendor trials: ACE Equity / CMOTS / Prowess (PIT, delisted coverage, API limits, licence), GDFL / TrueData (expired options depth, price)
- [ ] Whether any broker API reachable through OpenAlgo serves expired-contract intraday history
- [ ] GDELT coverage quality for Indian company news (entity-linking hit rate)
- [ ] SEBI retail algo-trading requirements with your broker (before any automated live orders)
