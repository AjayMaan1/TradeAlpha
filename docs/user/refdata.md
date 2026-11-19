# Reference data (P1 data foundation)

**Data → Reference Data** in the UI · `flask --app run.py historify refdata …` · `/api/v2/refdata/…`.

This layer makes prices, dates and universes correct and point-in-time before any strategy is tested. It has three
parts:
1. NSE end-of-day files: bhavcopies, participant OI, the F&O ban list, the equity master and index lists.
2. The trading calendar: derived from evidence, with official lists always winning.
3. Corporate actions: with adjusted and raw price views, and a check of whether your broker's history is already
   adjusted.

It also collects option chains going forward.

> **Verify on your machine.** The archive URLs are the publicly used NSE paths but could not be checked from the
> development sandbox (no internet). Run **Self-test sources** (Reference Data page) or
> `flask --app run.py historify refdata selftest`. Fix a failing source under **Reference Data → Datasets → Endpoints**
> (host bases and URL templates) without code changes.

## Datasets

| Kind | What | Table (refdata.duckdb) | Default schedule (IST, NSE trading days) |
|---|---|---|---|
| `cm_bhav` | Security-wise bhavcopy: OHLC, official close, previous close (adjusted by NSE on ex-dates), volume, turnover, trades, **delivery qty / %** | `cm_eod` | 18:45 / 20:15 / 22:15, plus a retry next morning |
| `fo_bhav` | F&O bhavcopy, **legacy** format until 2024-07-05 and **UDiFF** from 2024-07-08 (includes lot size and underlying price) | `fo_eod` | same |
| `participant_oi` | Participant-wise OI (FII / DII / Pro / Client) | `participant_oi` | same |
| `fo_ban` | Securities in the F&O ban for a trade date | `fo_ban` | evening (next trading day) + 08:10 |
| `equity_master` | Symbols → ISIN, listing date (fills `instruments.isin`) | `security_master` | Mondays 08:10 (and first run) |
| `index_list` | Current constituents of NIFTY 50/100/200/500/BANK (configurable) | meta `index_membership` | 08:10 daily |
| `holiday_master` | NSE official trading holidays (current year, CM/FO/CD) | meta `market_calendar` ("official") | Mondays 08:10 |

**Behaviour**
- Every attempt is logged in `files` (ok / missing = HTTP 404 / error / parse_error), and loads are idempotent: a date
  is replaced, never appended.
- A file whose dates don't match the requested day is rejected.
- The raw equity/participant/ban files are kept under `instance/refdata/raw/`. The large F&O files are not kept by
  default; they can be re-downloaded, and `keep_raw` in the settings changes this.

**Backfill** (Datasets tab or `refdata backfill --start 2016-01-01 --end 2024-12-31`):
- It walks weekdays newest-first at the polite pace (about 2 s per file, shared with the news archive).
- Days that 404 twice are settled as "no file": usually holidays, and evidence for the calendar.
- It then computes breadth, parses and verifies corporate actions, and checks the broker's price basis.

## Trading calendar
The shipped calendar only knew 2025. **Calendar → Preview** derives history from evidence:
- NIFTY and BANKNIFTY daily bars;
- bhavcopy availability (settled 404s);
- ledger rows confirmed missing.

| Outcome | Rule |
|---|---|
| Holiday | No source shows trading on a weekday |
| Special session | Data shows trading on a weekend or listed holiday (Muhurat, Budget Saturdays, DR drills); open/close come from the first and last 1m bar |
| Conflict | Sources disagree; never applied |
| Official disagrees | An imported official list entry differs from the data; reported, never overwritten |

- **Apply** writes the high-confidence changes (≥ 2 sources, or a settled/confirmed signal) for NSE, BSE, NFO, BFO,
  NSE_INDEX and BSE_INDEX, then queues a **ledger rebuild**.
- Future holidays come from the **official holiday master**, or from an official CSV import (Settings → Calendar). Both
  are marked "official" and always win over derived data.
- Per-year trading-day counts are shown with a plausibility check (235–256).
- Not covered: MCX holidays (different list), and currency-only holidays before the current year.

## Corporate actions
1. **Source:** NSE corporate-action records archived by the News & Filings archive. Enable and backfill the source
   `nse_corporate_actions_hist` for history; the daily source stays current.
2. **Parse:** splits/consolidations (`from Rs 10 to Rs 2` → factor 0.2), bonus `a:b` (b/(a+b)), dividends (₹ or % of
   face value), rights (TERP factor from the prior close), demerger/merger (recorded; factor via manual entry).
3. **Verify:** NSE adjusts the bhavcopy's previous close on ex-dates, so `implied = PREV_CLOSE(ex) / CLOSE(day before)`.
   A parsed factor matching within 1% is **verified**; otherwise it is a **conflict**, and the exchange's implied factor
   is used. Adjustments we have no announcement for (> 2%, e.g. demergers) become **implied** actions.
4. **Views:** `factors(symbol, as_of)` uses only actions with ex-date ≤ as_of (point-in-time).
   - **split:** prices × cumulative factor, volumes ÷ factor.
   - **total:** also dividends, (P − D)/P.
   - **raw:** as traded.
5. **Broker price basis:** `reconcile` compares the broker's daily closes with bhavcopy closes, including around
   ex-dates. It records `instruments.price_basis` (raw / adjusted / unknown), so adjustments are never applied twice,
   and it raises a Data Health issue when more than 2% of closes differ from the official close.

| Where the views are used | Default |
|---|---|
| `GET /api/v2/bars?...&adjust=raw\|split\|total` (W/M are rolled up from adjusted daily bars) | raw |
| Scanner features (daily bars and the 1m history for relative volume) | split |
| Exports, snapshots | raw (reproducible source data; adjust when reading) |

## Universes & F&O
| Topic | How it works |
|---|---|
| **Index membership** | Forward history from daily snapshots of the constituent lists. The first snapshot's entry dates are lower bounds. Import exact history from NSE reconstitution notices as CSV `index,symbol,effective_from,effective_to`. `members(index, date)` reports whether the answer is exact. The scanner can use a point-in-time index as its universe. |
| **F&O eligibility** | Underlyings with futures on a date (`fo_eod`), and periods per stock |
| **Ban list** | Securities in the ban on a date |
| **Lot sizes by date** | From UDiFF F&O files (July 2024 onwards); earlier lot sizes are not in the bhavcopy |

## Option-chain forward collection
- Brokers only serve intraday history while a contract is listed, so every day not collected is lost.
- After each day's F&O bhavcopy, the collector selects, per underlying:
  - the nearest 2 option expiries × ATM ± 15 strikes × CE/PE;
  - plus the nearest 2 futures;
  - at most 300 contracts per day.
- It creates the NFO instruments with contract metadata and queues their 1m bars (backfill priority).
- Defaults: NIFTY and BANKNIFTY, about 130 requests per day.
- These contracts are hidden from the Data Manager unless they are in a watchlist (`/api/v2/coverage?contracts=1` shows
  them).
- Expired contracts are never "stale", and the weekly maintenance archives them 30 days after expiry.

## Reference series
`ref_series` holds named daily series:
- breadth from the equity bhavcopy, split-safe because it uses NSE's adjusted previous close:
  `breadth.nse_eq.advances|declines|unchanged|ad_ratio|pct_above_50dma|pct_above_200dma|new_highs_52w|new_lows_52w`;
- anything you import as CSV, for example `rf.tbill_91d` (91-day T-bill, % p.a.) via
  `POST /api/v2/refdata/series/import`.

## Storage & backup
- `instance/refdata/refdata.duckdb` is a separate file, so the bar store's rewrite-compaction never touches it.
- Size, measured with random (poorly compressible) values, so real data is smaller:
  - equity bhavcopy (about 3,000 rows/day): **≤ 85 MB per year**, under 1 GB for 10 years;
  - F&O bhavcopy (about 60,000 rows/day): **≤ 300 MB per year**.

  Backfill F&O only for the years you will research (e.g. 3–5).
- The weekly backup writes every reference table as Parquet (`backups/<id>/refdata/*.parquet`) and verifies row counts.
- Raw files are not backed up; they can be re-downloaded.
- `HISTORIFY_REFDATA_AUTOSTART=0` keeps the scheduler from starting with the server.

## Known limits
- The URLs and the ban-file date convention are unverified; run the self-test.
- BSE-only companies have no bhavcopy verification (NSE files only).
- Symbol changes break `cm_eod` continuity by symbol; ISIN-based stitching is a follow-up.
- MCX holidays are not derived.
- Lot sizes before July 2024 are unknown from bhavcopies.
- Illiquid option contracts often have minutes without trades. Their sessions may show as "short" until confirmed
  (one extra re-check request per contract-day).
