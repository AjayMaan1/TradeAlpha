# P2 · Market Intelligence: Scope and Test Strategy

| | |
|---|---|
| **Phase** | P2 of `PROJECT_ROADMAP_6_PHASES.md` (builds on P1 reference data and the News & Filings archive) |
| **Goal** | Turn archived filings, disclosures, flows and calendars into **typed, point-in-time, tested** data that the scanner (now) and the backtester (P3) can use |
| **Principle** | Nothing new is downloaded for history: typed tables are *derived* from the raw records already archived, so every parser fix can be replayed over the full history |

---

## 1. What P2 builds

| # | Component | Output | Consumers |
|---|---|---|---|
| P2.1 | **Typed disclosures** | `insider_trades`, `bulk_block_deals`, `sast_disclosures`, `pledge_snapshots`, `encumbrance_events` (meta DB), extracted incrementally from archived raw records | Scanner catalysts, promoter-buying / pledge signals, Track L ownership scores (P5) |
| P2.2 | **Event calendar** | `market_events` with **`available_at`** (when the event became known), from: <br>• board meetings (results dates); <br>• corporate-action ex-dates; <br>• F&O expiries (from bhavcopies); <br>• rule-based macro dates (Budget, India CPI, GDP), marked *estimated*; <br>• CSV import for RBI MPC / FOMC / US CPI / elections (*confirmed*). | Scanner (results within the holding window, market events today); risk rules (P4) |
| P2.3 | **Flow series** | FII/DII cash (from the archive) and participant-OI derived series (FII index-futures net and long ratio, etc.) in `ref_series` | Regime layer, experiments |
| P2.4 | **Entity mapping** | `securities` (ISIN ↔ NSE symbol ↔ BSE code), BSE filings linked to NSE symbols, **headline → company** links for news (`item_entities`) | Feeds search by symbol across exchanges, news catalysts, P3 features |
| P2.5 | **Pre-open snapshot** | 09:08 IST equilibrium prices (NSE pre-open page; 1 request for all F&O stocks) → `preopen` table; the pre-market scan moves to 09:10 and gains a *pre-open gap* feature | Scanner, gap strategies (I3) |
| — | **Quality report** | Parse coverage per source, link rates, unmatched BSE codes, stale sources | You, monitoring, live verification |

---

## 2. How we test it

P2 sits on top of free, unofficial public data, so the main risks are **format drift**, **wrong linking** and
**look-ahead**, not crashes. The strategy therefore has five layers, plus a live-verification loop on your machine.

### 2.1 Test pyramid
| Layer | What is tested | How | Runs |
|---|---|---|---|
| **1. Unit: pure logic** | Field mapping per source (insider, SAST, pledge, deals, FII/DII, pre-open), transaction normalisation (Buy/Sell/Pledge/Invocation…), promoter detection, pledge metrics, event rules (CPI on the 12th or the next working day, GDP month-ends, expiry classification), alias normalisation, headline linker | Table-driven tests over hand-built records in the published layouts | Every commit (about 2 s) |
| **2. Property-based** | Invariants that must hold for *any* input: <br>• extraction is idempotent (twice = once); <br>• a revised record updates, never duplicates; <br>• pledge % stays in 0–100; <br>• deal value ≈ qty × price; <br>• linker never links inside a longer word; <br>• events never appear before `available_at` | `hypothesis` (already a dev dependency) | Every commit |
| **3. Golden / contract** | Each parser against **fixture files in the exact published shape** (JSON/CSV) with expected typed rows. **Live fixtures captured on your machine** (`flask historify intel capture-fixtures`) are picked up automatically when present. | Parametrised tests over `tests/fixtures/intel/*` (+ `live/` if captured) | Every commit |
| **4. Integration** | End to end through Services: <br>• mock sites → feeds archive → extractor → typed tables → API; <br>• incremental runs; <br>• revisions; <br>• BSE→NSE linking; <br>• event build + PIT queries; <br>• flows after bhavcopy import; <br>• pre-open → scanner gap; <br>• scheduler slots; <br>• rebuild job; <br>• quality report; <br>• CLI. | Mock NSE/BSE/news server + mock broker, frozen clock | Every commit (about 1 min) |
| **5. Browser E2E** | Market Intelligence page: tables, filters, import, quality tab; no console errors or CSP violations | Playwright + Chromium | Every commit |
| — | **Regression** | The whole existing suite (383 tests + 37 browser tests) stays green | Every commit |

### 2.2 Point-in-time tests (look-ahead guard)
Every P2 table carries the time the information became public: `broadcast_at` for disclosures, `available_at` for
events. Tests assert:
- a scan *as of* 09:10 never sees a filing broadcast at 10:00, nor an event announced after that time;
- an expiry is known only from the first day its contracts traded;
- a results date is known only from the board-meeting intimation's broadcast time;
- replaying a past date gives identical results before and after later data is added. This is the same
  "corrupt-the-future" technique used for the scanner in P1.

### 2.3 Quality metrics as tests
| Metric | Threshold (fixtures) | Why |
|---|---|---|
| Disclosure parse coverage (share of records with all key fields) | ≥ 95% | Format drift shows up as falling coverage |
| Headline linking precision / recall on a labelled set of Indian market headlines | ≥ 0.90 / ≥ 0.80 | Wrong links are worse than missing ones (they create false catalysts) |
| BSE filings linked to an NSE symbol (when the security is dual-listed) | 100% on fixtures | Cross-exchange search |
| Deal consistency (qty × price vs value) | within 1% | Catches column mix-ups |
| Promoter holding arithmetic (before ± change ≈ after) | within 0.05 pp when all fields exist | Catches unit errors (shares vs %) |

The same metrics are computed **in production** by the quality report (`/api/v2/intel/quality`). A source that drops
below threshold raises a Data Health issue. Production data is tested continuously, not just the fixtures.

### 2.4 Live verification loop (on your machine)
1. **Self-tests:** Feeds self-test + Reference Data self-test (P1), which check that endpoints answer.
2. **`flask historify intel capture-fixtures`:** saves 1–3 real records per source (raw JSON/CSV, no credentials) to
   `tests/fixtures/intel/live/`.
3. **Run `pytest tests/unit/test_intel_units.py`:** the golden tests now also run against real formats. Failures point at a field mapping
   to adjust (Settings or code).
4. **Intelligence → Quality:** coverage and link rates on your real archive; anything below threshold is listed with
   example records.

### 2.5 Acceptance criteria (definition of done for P2)
Status after implementation: all met. `P2_MARKET_INTELLIGENCE_REPORT.md` §4 maps every layer above to the test that
implements it and lists the two checks that changed during implementation.

- [x] All typed tables rebuild from the archive with one job, identically when run twice.
- [x] Scanner rows show insider/pledge/event features; as-of replays are unchanged by later data.
- [x] Event calendar: results (from board meetings), ex-dates, expiries, estimated macro dates, imported confirmed dates;
      `?as_of=` filtering.
- [x] Flow series present after import: FII/DII cash (archive) and participant-OI derived series.
- [x] BSE filings carry NSE symbols where a mapping exists; news items carry linked companies.
- [x] Pre-open snapshot stored at 09:08; the pre-market scan at 09:10 shows the pre-open gap.
- [x] Quality report with thresholds, and Data Health issues on breach.
- [x] All test layers green; docs updated.
