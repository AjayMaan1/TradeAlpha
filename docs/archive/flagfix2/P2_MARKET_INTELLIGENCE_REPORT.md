# P2 · Market Intelligence: Implementation Report

| | |
|---|---|
| **Phase** | P2 of `PROJECT_ROADMAP_6_PHASES.md`. It builds on the News & Filings archive and P1 reference data. |
| **Status** | ✅ Built, self-reviewed and tested in this environment. Two steps need your machine: the live self-test and capturing fixtures from real data (§7). |
| **Scope / test plan** | `P2_TEST_STRATEGY.md`. This report maps each planned test to the test that now implements it (§4). |
| **User docs** | `historify-master/docs/intel.md` |
| **Code** | `app/core/intel/` (1,586 lines), `app/web/intel_api.py`, the `/intel` page, the `flask historify intel` CLI, Alembic `0006_intel` and feeds.db schema v2 |
| **Tests** | 64 new tests (unit, property, golden, integration, browser). Full suite: **446 passed, 9 xfail** (pre-existing v1 characterisations) **+ 39 browser tests passed**. |

---

## 1. What was built

```
NSE/BSE sites ──► News & Filings archive (raw, append-only) ─┐
NSE archives  ──► P1 reference data (bhavcopy, F&O, part. OI) ┤
NSE pre-open page (09:08) ────────────────────────────────────┤
BSE scrip list (weekly) ──────────────────────────────────────┘
                                   │   extract / link / build (every 15 min · 08:30 · 20:45 · 09:08)
                                   ▼
  typed disclosures   insider_trades · bulk_block_deals · sast_disclosures · pledge_snapshots · encumbrance_events
  event calendar      market_events (available_at, estimated | confirmed | superseded)
  flows               ref_series flows.fii_cash.* · flows.dii_cash.* · flows.participant.<who>.*
  identity            securities (ISIN ↔ NSE ↔ BSE) · item_entities (BSE code / ISIN / headline → symbol)
  pre-open            preopen (appended captures with captured_at)
                                   │
                ┌──────────────────┼──────────────────────────┬──────────────────────┐
                ▼                  ▼                          ▼                      ▼
     09:10 pre-market scan   Market Intelligence page   /api/v2/intel/*  CLI   P3 backtests (next)
     (+ 09:31 confirmation)  (6 tabs)                   quality report → Data Health
```

| # | Component | Delivered |
|---|---|---|
| P2.1 | **Typed disclosures** | Five tables are extracted from archived raw records. Runs are incremental (only records stored or re-seen since the last run); a full rebuild is a background job. The parsers tolerate renamed fields (several candidate names per field, Indian number grouping such as 1,05,00,000) and record any missing key field per row. Transaction types are normalised to buy, sell, pledge, revoke, invoke or other. Pledge records that carry only a company name are matched to a symbol by the cleaned name. |
| P2.1 | **Signals** | `insider_summary`: promoter market buys and sells, net ₹ Cr, and a *cluster* flag (≥ 2 buys, no sells). `pledge_status`: pledged % of promoter holding as known at `as_of`, 90-day change and invocations, with a red flag when pledged > 20 %, up more than 10 pp in 90 days, or any invocation (thresholds configurable). |
| P2.2 | **Event calendar** | Sources: results dates from board-meeting intimations, ex-dates from corporate actions, F&O expiries from the bhavcopy (monthly = last expiry of the month), and rule-based estimates for Budget, India CPI and GDP. A CSV import adds confirmed RBI MPC, FOMC, US CPI, election or rebalance dates; a confirmed date replaces the estimate. When a company re-schedules a board meeting, the old date is *superseded* but stays visible to `as_of` queries made before the revision. |
| P2.3 | **Flows** | FII/DII cash series from the archive. Participant-OI series: index-futures net, long ratio, call/put net and stock-futures net for FII, DII, Pro and Client. `snapshot(scan_date)` reads only dates before the scan date. |
| P2.4 | **Entity resolution** | The `securities` map joins the NSE master, instruments and the BSE scrip list. BSE filings, which carry only a scrip code, become NSE symbols. A headline linker connects news to companies, with precision 0.99 and recall 0.99 on 93 labelled headlines (at least 20 of them must link to nothing). The symbol filter on News & Filings now includes linked items. |
| P2.5 | **Pre-open** | The NSE pre-open page is captured at 09:08 on trading days, and raw responses are kept. The pre-market scan moved from **09:05 to 09:10**. New features: `preopen_gap_pct` and `preopen_gap_atr`. |
| — | **Scanner integration** | Every scan row now records: pre-open gap, promoter buys/sells/net/cluster, pledge % / flag / reasons, next results or ex-date, and linked-headline count. The run's regime gets market events for today and a flow snapshot, and a note is added when a high-impact event falls on the scan day. **Only the pre-open gap enters the score** (weight 0.15). It is zero for every row when no snapshot exists, so rankings without pre-open data do not change. The other features are recorded so P3 can validate them before anyone weights them. |
| — | **Quality report** | Metrics: parse coverage per source, deal price within the bhavcopy day range (a cross-source check), pledge arithmetic, insider direction, BSE codes resolved, and freshness. A breached threshold raises a Data Health issue; recovery resolves it. |
| — | **UI / API / CLI** | The Market Intelligence page has six tabs: overview & quality, insiders & deals, pledges, event calendar (with CSV import), flows & pre-open, and a symbol view. A scanner *Intel* column links each row to the symbol view. There are 19 API endpoints and 9 CLI commands, including `selftest` and `capture-fixtures`. |

Screenshots: `diagrams/intel_overview.png`, `diagrams/intel_insiders.png`, `diagrams/intel_events.png`. The HLD and
roadmap diagrams are updated: *Market intelligence* is now built, and P1 and P2 are ticked.

---

## 2. Point-in-time rules (how look-ahead is prevented)

| Data | Known from | Enforced in |
|---|---|---|
| Disclosures | `broadcast_at`: the exchange's own timestamp if it has a time of day, else the time we first archived the record | `insider_summary`, `pledge_status`, the symbol API `?as_of=` |
| Deals | The time the file was archived on the trade date; if archived later, 17:30 IST of the trade date | same |
| Events | `available_at`: the intimation's broadcast time, the corporate-action announcement, the first day the contracts traded, or the `announced` column of an import | `events.query(as_of=)`, `for_symbols` |
| Re-scheduled meetings | The old date is visible until the revision's `available_at`; the new date from then on | `events.query` |
| Flows | Day *t* is usable from *t + 1* | `flows.snapshot` |
| Pre-open | `captured_at ≤ as_of`. Captures are **appended**, so a later manual capture never erases what an earlier scan used. | `preopen.snapshot` |
| Headlines | `published_at` | `linked_items` |

---

## 3. Self-review: gaps found and fixed

The tests found these problems; each is now fixed and guarded by a test.

| # | Gap | Consequence | Fix |
|---|---|---|---|
| 1 | **A later pre-open capture replaced the day's rows.** The corrupt-the-future test exposed this. | A manual re-capture at 09:20 silently changed every earlier 09:10 replay: a look-ahead and reproducibility bug. | Captures are appended, and snapshots pick the latest capture ≤ `as_of`. The test now proves an identical replay and that a later `as_of` sees the new capture. |
| 2 | Re-scheduled board meetings left two results dates. | Wrong "results within 5 days" flags. | The old date is marked *superseded*, and stays visible only to queries made before the revision. |
| 3 | API endpoints used the wall clock, not the app clock. | Wrong "last 30 days" windows in tests and replays. | All endpoints use the app clock. |
| 4 | API date filters treated IST dates as UTC days. | Filings after 18:30 IST fell into the next day. | IST calendar-day boundaries are converted to UTC; tested with a 02:00 IST record. |
| 5 | An unescaped `+05:30` in `?as_of=` arrives as a space. | HTTP 400 for a natural URL. | The offset is repaired before parsing. |
| 6 | Concurrent writers (scheduler tick, API "extract now", rebuild job) | Possible duplicate-key errors. | One re-entrant lock serialises all writers; a test runs 4 concurrent full extractions. |
| 7 | NaN values in pre-open rows reached the JSON response. | The browser rejected the response (empty table). | NaN is converted to null; the test asserts strict JSON. |
| 8 | `1e7` in a template expression | Alpine's CSP parser error. | Moved into a component method; the browser test covers every tab with data. |
| 9 | A full rebuild kept every ORM object in one session, and linking decompressed every raw record. | High memory and slow rebuilds on a large archive. | Commit and expunge every 2,000 rows; `iter_items(raw=False)` for linking. |
| 10 | No guard against ORM vs migration drift | A model change without a migration would pass the tests. | New test: Alembic head must equal the models. |
| 11 | Pledge records carry no symbol | The pledge table would have been mostly unusable. | Resolution by ISIN, then cleaned company name. |
| 12 | The freshness check said "ok" when the scheduler was off. | Misleading status. | The detail now says "scheduler not running". |
| 13 | Existing users who saved `premarket_at = 09:05` scan before the capture. | Silently no pre-open feature. | An explicit run note explains it and tells the user to move the scan to 09:10. |
| 14 | Event CSV import size was unbounded. | Memory abuse. | Limit of 20,000 rows or 1 MB (HTTP 413). |
| 15 | Linker false positives found while building the labelled set ("Suzlon" treated as a group word, "Torrent Pharma" missing, "Life Insurance" as an alias). | Lower precision and recall. | Separate group-word and common-word lists, a two-word-prefix rule and more curated aliases. One known miss remains and is documented. |

---

## 4. How we test it (as implemented)

| Layer (from `P2_TEST_STRATEGY.md`) | Implemented by | What it proves |
|---|---|---|
| **1 · Unit: pure logic** | `tests/unit/test_intel_units.py` (txn typing table, encumbrance events, broadcast rule, pre-open features, rule helpers, linker rules, name cleaning) | Mapping and normalisation rules |
| **2 · Property (hypothesis)** | Indian and western number grouping; pct within 0–100; **the linker never links inside a longer word**; **no event is returned before `available_at`** for 40 random `as_of` values; idempotent full re-extraction | Invariants for any input |
| **3 · Golden / contract** | `tests/fixtures/intel/*.json` (insider, SAST, pledge, deals, pre-open, BSE scrips, 93 labelled headlines). **Live contract test** `test_live_captures_parse` runs automatically once `tests/fixtures/intel/live/` exists. | Parsers handle the published layouts and reject changed ones (HTML, missing data, missing IEP) |
| **4 · Integration** | `tests/integration/test_intel.py`: mock NSE/BSE sites, then the archive, typed tables and API. Covers revisions, incremental runs, name resolution, BSE and headline links, events `as_of` and superseding, flows, pre-open → scanner score (exact weight arithmetic), the **corrupt-the-future replay**, schedule slots (weekday/weekend, once per slot), the rebuild job, concurrent writers, quality → Data Health raise/resolve, API, CLI and fixture capture | End-to-end behaviour and point-in-time correctness |
| **5 · Browser E2E** | `tests/ui/test_intel_page.py` plus `/intel` in the all-pages CSP test | Every tab renders real data; no console errors or CSP violations; an XSS payload renders as text |
| **Regression** | Full suite | 446 passed + 39 browser; existing scanner tests updated for 09:10 |
| **Schema** | `test_migrations_match_the_models` | Migrations equal the models |

**Quality metrics as tests, and in production.** The linker must keep precision ≥ 0.90 and recall ≥ 0.80 on the
labelled set, or CI fails. In production, the quality report computes coverage and consistency on your real archive
and raises Data Health issues.

Two planned checks changed during implementation:
- **"qty × price ≈ value"** was dropped because we compute value ourselves, so the check would always pass. It is
  replaced by a stronger cross-source check: the deal price must fall within that day's bhavcopy range.
- **"before ± change ≈ after"** became a direction check (after ≥ before for buys). The change-% field is not reliably
  present.

**Acceptance criteria:**

| Criterion | Status |
|---|---|
| Typed tables rebuild from the archive with one job, identically when run twice | ✅ |
| Scanner rows carry intel features; as-of replays are unchanged by later data | ✅ |
| Event calendar covers results, ex-dates, expiries, estimated and imported dates; `?as_of=` filtering works | ✅ |
| Flow series are present | ✅ |
| BSE filings carry NSE symbols; news carries linked companies | ✅ |
| Pre-open capture at 09:08 feeds the 09:10 scan | ✅ |
| Quality report with Data Health issues | ✅ |
| All test layers green; docs updated | ✅ |

---

## 5. Known limitations and risks

| Risk | Mitigation | Residual |
|---|---|---|
| NSE/BSE website JSON endpoints are not official APIs. The pre-open page and BSE scrip-list field names are modelled, **not verified** from this sandbox (no internet). | Tolerant parsers; the raw data is kept for replay; `intel selftest`; live contract fixtures; coverage metrics raise issues | First run on your machine may need a field-name tweak. A rebuild then repairs history. |
| FII/DII history exists only from the day the archive started (NSE shows only the latest day). | Archived daily from now on | Older history needs NSE's monthly reports or a vendor (catalogue §2) |
| Participant-OI series need P1 bhavcopy backfill. | `refdata backfill --kind participant_oi` | — |
| Macro dates are estimates until confirmed dates are imported. | *Estimated* status is shown; a confirmed import replaces it | Import the RBI MPC / FOMC calendars yearly (CSV) |
| The headline linker uses today's company names. | Documented; the scanner only asks about point-in-time universe symbols | Renamed companies may miss old headlines |
| Insider sells are counted regardless of mode; buys only when they are market purchases. | Conservative for the buy signal | P3 should test alternative definitions |
| Intel features other than the pre-open gap are not in the score. | Intentional, to avoid unvalidated weights | P3 event studies decide (E-series) |

---

## 6. Effort and size

| Item | Result |
|---|---|
| Estimate | ≈ 14 d |
| Delivered | Typed disclosures, calendar, flows, entities, pre-open, quality report, scanner integration, UI/API/CLI, tests and docs |
| New code | 2,236 lines (core, API, UI) |
| New tests | 818 lines |

---

## 7. What you need to do on your machine

1. `flask --app run.py historify intel selftest`. Both the pre-open and BSE scrip checks should report `ok`. If not,
   the output shows the URL and error; override the host with `feeds/hosts`. The field mapping lives in
   `preopen.py` / `entities.py`.
2. `flask --app run.py historify intel securities --bse`, then `intel extract --full`.
3. After a few trading days, run `intel capture-fixtures` and commit `tests/fixtures/intel/live/`. From then on, CI
   checks real formats on every commit.
4. Check **Market Intelligence → Overview** (quality) once a week. Breaches also appear on **Data Health**.
5. Once a year, import the RBI MPC and FOMC calendars (Event calendar → Import). This needs about 20 CSV lines.
6. If you previously saved scanner settings, make sure the pre-market scan time is 09:10 (Scanner → Settings).
