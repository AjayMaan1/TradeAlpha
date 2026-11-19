# P1 · Data Foundation: Implementation and Review Report

| | |
|---|---|
| **Scope** | Roadmap phase P1 (`PROJECT_ROADMAP_6_PHASES.md`): calendar history, instrument metadata, corporate actions with adjusted/raw views, NSE bhavcopies (equity + F&O, legacy + UDiFF), F&O universe/ban/lot sizes, point-in-time index membership, reference series, option-chain forward collection |
| **Constraint honoured** | All new code is Python (plus the existing JavaScript UI conventions). No Java, no Go. |
| **Status** | Implemented, self-reviewed, gaps fixed, tested against mock NSE archives and the mock broker. **Not yet run against the live NSE websites** (no internet in the sandbox). |
| **Guide** | `historify-master/docs/refdata.md` · UI **Data → Reference Data** · CLI `flask historify refdata …` · API `/api/v2/refdata/…` |

![HLD](diagrams/hld_architecture.png)

---

## 1. What was built

| P1 item | Delivered |
|---|---|
| **Calendar history** (blocker B1) | The calendar is **derived from evidence**: NIFTY/BANKNIFTY daily bars, bhavcopy availability (settled 404s) and confirmed-missing ledger rows. <br>• **Preview:** holidays, special sessions (open/close from 1m bars: Muhurat, Budget Saturdays, DR drills), conflicts, per-year plausibility. <br>• **Apply:** high-confidence changes for the 6 equity exchanges, then an automatic **ledger rebuild**. <br>• **Official NSE holiday master** (future dates) is imported weekly. Official rows always win; data that disagrees is reported, never applied. |
| **Instrument metadata** (B4) | Migration 0005: `segment`, `underlying`, `option_type`, `isin`, `price_basis`. Contract symbols are parsed and round-trip (`NIFTY28MAR2522000CE`, `M&M24APR252912.5CE`, MCX futures). Metadata is filled at creation and at start-up. **ISIN** comes from the NSE equity master. **Lot sizes by date** come from UDiFF F&O bhavcopies. |
| **Corporate actions** (B3) | NSE records (archived by News & Filings; a new date-range source supports history) are parsed: split, consolidation, bonus, dividend (₹ or %), rights (TERP), demerger/merger. <br>• **Verified** against NSE's adjusted previous close in the bhavcopy. <br>• Exchange adjustments with no announcement become **implied** actions; demergers use the exchange's factor. <br>• Views: **raw / split / total**, point-in-time (ex-date ≤ as-of). <br>• The **broker price basis** (raw vs already adjusted) is detected, so adjustments are never applied twice. <br>• Used by `GET /api/v2/bars?adjust=` (W/M rolled up from adjusted daily bars) and by the **scanner** (daily features and relative-volume history). |
| **Bhavcopies** | • **Equity:** security-wise, with delivery %. <br>• **F&O:** legacy format to 2024-07-05, **UDiFF** from 2024-07-08, with lot size and underlying price. <br>• **Also:** participant OI and the F&O ban list. <br>• **Storage:** a separate DuckDB file (`refdata.duckdb`), because the bar store's compaction only copies its own tables. <br>• **Import log:** one row per (kind, date). Loads are idempotent, files for the wrong day are rejected, and the raw files are kept. <br>• **Schedule:** evening slots plus a morning retry of late files. Backfill runs newest-first and settles 404 days. |
| **Universes** | **Point-in-time index membership**: forward from daily list snapshots, and backward from a CSV of reconstitution history. `members(index, date)` says whether the answer is exact. The scanner can use a PIT index universe (survivorship-free). Also: F&O underlyings by date, ban list by date, lot-size history. |
| **Reference series** | Split-safe **breadth** from the bhavcopy (advances/declines, A/D ratio, % above 50/200 DMA, 52-week highs/lows) and CSV import of any named series (e.g. the 91-day T-bill). |
| **Option-chain collection** | After each F&O bhavcopy: nearest 2 option expiries × ATM ± 15 strikes × CE/PE + 2 futures (cap 300). Instruments are created with metadata and their 1m bars queued (backfill priority). |
| **Reconciliation** (I-7) | Broker daily closes vs the official bhavcopy close → price basis + a Data Health issue when more than 2% of days differ. Runs weekly and after backfills. |
| **Ops** | Background jobs (backfill, self-test, ledger rebuild), weekly backup of every reference table (Parquet, row counts verified), `HISTORIFY_REFDATA_AUTOSTART`, **Endpoints editor** (hosts + URL templates) in the UI, CLI group, docs. |

## 2. Self-review: gaps found and fixed

I reviewed every P1 change after the first implementation. Each gap below was fixed and covered by a test.

| # | Gap found | Risk if left | Fix |
|---|---|---|---|
| 1 | Bar store **rewrite-compaction copies only `ohlcv`** (found before building) | Reference tables placed in `bars.duckdb` would vanish at the next monthly rewrite | Separate `refdata.duckdb`, backed up as Parquet with verification |
| 2 | Derived calendar covers only the **past** | Holidays this year and next would be treated as trading days (bad schedules, false gaps) | **Official NSE holiday master** import (weekly, marked official, wins over derived data) |
| 3 | A single **404 on a recent day** counted as "closed" | A bhavcopy published late could turn a trading day into a holiday | 404 evidence only when settled (seen twice, or older than 7 days); conflicts are never applied |
| 4 | **Late bhavcopies** after 22:15 were never retried | Permanent holes in the bhavcopy history | Morning retry of the previous trading day |
| 5 | **Official vs derived** disagreement appeared as a pending change forever | Preview never converges; the user may overwrite official data | Official rows win; disagreements are reported separately (`official_disagrees`) |
| 6 | `adjust=raw` skipped the transform for **broker-adjusted** series | Raw view would show adjusted prices for such symbols | Transform whenever the requested view differs from the broker basis |
| 7 | Scanner **relative volume** used raw pre-split 1m volumes | Relative volume inflated by the split ratio for about 20 days after a split | Adjusted 1m history (point-in-time) for the volume baseline |
| 8 | **Option-chain contracts flooded coverage** (about 130 per day); **expired contracts showed "stale"** | Data Manager and dashboard slow and full of false "stale" warnings | Contracts outside watchlists hidden by default (`?contracts=1` shows them, summary counts them); expired contracts never stale |
| 9 | The coverage filter used `NOT (segment IN …)` (**SQL NULL logic**); caught by the existing suite | Every not-yet-enriched instrument disappeared from the Data Manager | NULL-safe `coalesce` filter + regression test |
| 10 | **Demergers** matched an announcement but got **no factor** | Price history distorted around demergers (e.g. Reliance / Jio Financial, 2023) | The exchange's implied factor is applied to demerger/merger records |
| 11 | Corporate-action sync **re-parsed the whole archive daily**; verification was O(n²) | Slower every year | Incremental (records re-seen since the last run, 1 h overlap) + indexed verification |
| 12 | Breadth recomputed window functions over **all history** on every import | Daily import slows as years accumulate | Bounded 420-day lookback; a test proves it is identical to the full computation |
| 13 | One failing scheduled step **aborted the rest** of the morning/evening tasks | Silent missing data for the day | Each step isolated, logged and reported |
| 14 | **No way to change host bases** (docs said "fix in settings") | A changed NSE URL would need a code change | `PUT /api/v2/feeds/hosts` + Endpoints editor (hosts + URL templates) |
| 15 | Announcement times were stored in the **machine's local time zone** | Wrong point-in-time on non-IST machines | Naive UTC like every other DateTime column |
| 16 | Manual demerger factor was stored as a "split" | Misleading audit trail | Kind kept; demerger/merger factors applied when present |

## 3. Verification (sandbox)

| Layer | Result |
|---|---|
| New unit tests (`tests/unit/test_refdata_units.py`) | **23 passed**. They cover: <br>• parsers for every file kind, including NSE formatting quirks; <br>• contract round trips; <br>• 10 corporate-action subjects; <br>• adjustment maths for raw vs adjusted broker series; <br>• breadth lookback = full history. |
| New integration tests (`tests/integration/test_refdata.py`) | **20 passed**. Against mock NSE archives + the mock broker, they cover: <br>• imports (legacy/UDiFF/404/HTML/wrong-day/idempotent); <br>• backfill + resume + holiday derivation; <br>• calendar from index bars with a DR-drill Saturday; <br>• official precedence; <br>• corporate actions verified / implied / demerger, point-in-time factors, total return; <br>• broker basis raw vs adjusted; <br>• adjusted API views incl. weekly; <br>• PIT index membership, F&O, lot sizes, ban; <br>• option-chain plan → instruments → completed 1m job; <br>• morning/evening schedule, late-file retry, failing-step isolation; <br>• coverage hiding + expired status + NULL safety; <br>• PIT-index scanner universe + split-adjusted relative volume; <br>• self-test job; backup; endpoints API; API + CLI. |
| New browser test (`tests/ui/test_refdata_page.py`) + page sweep | Reference Data page: coverage KPIs, calendar preview → apply (the calendar really changes), endpoints editor saves, corporate-actions tab; no console errors or CSP violations |
| Full suite | See §6 (numbers from the final run) |
| Storage sizing (measured) | Equity bhavcopy ≤ 85 MB/year, F&O UDiFF ≤ 300 MB/year (random, poorly compressible values; real data is smaller) |

## 4. Known limitations (documented in docs/refdata.md)
| Limitation | Plan |
|---|---|
| NSE archive URLs and the ban-file date convention are unverified | Run **Self-test sources** on your machine; fix via the Endpoints editor |
| BSE-only companies: no bhavcopy verification | BSE bhavcopy importer (P2 or later) |
| Symbol changes break `cm_eod` continuity by symbol | ISIN-based stitching via the equity master + NSE symbol-change file (P2) |
| MCX holidays are not derived; currency-only holidays only from the holiday master | MCX reference series / MCX holiday list (when MCX matters) |
| Lot sizes before July 2024 are not in bhavcopies | NSE lot-size circular archive (optional) |
| Index membership history before the first snapshot needs a CSV import | Import NSE Indices reconstitution history once |
| Illiquid option minutes → "short" sessions re-checked once | Accept (about +1 request per contract-day), or skip re-checks for chain contracts (P6) |

## 5. What to do on your machine
1. **Reference Data → Self-test sources** (or `flask --app run.py historify refdata selftest`). Fix any failing source under **Datasets → Endpoints**.
2. **Backfill:**
   - equity bhavcopy (`cm_bhav`) from 2016;
   - F&O for the years you will research (e.g. 2022 →);
   - participant OI and the ban list.
3. **News & Filings →** enable and backfill `nse_corporate_actions_hist` (e.g. 2016 → today), then **Reference Data → Corporate actions → Parse & verify**.
4. **Scanner → Prepare research data** (NIFTY/BANKNIFTY daily), then **Reference Data → Calendar → Preview → Apply**.
5. **Datasets → Reconcile broker vs bhavcopy:** tells you whether Angel One's history is raw or adjusted.
6. Optional: import index-membership history (CSV) and the 91-day T-bill series.
7. Still open from before: the **roll-up audit** (`flask historify rollup-audit`) and **E17** on your real data.

## 6. Test results (final run)
| Suite | Result |
|---|---|
| All non-browser layers (unit, contract, integration/API, regression, repo hygiene, performance) | **383 passed**, 9 expected v1 xfails (was 340 before P1) |
| Browser E2E (Chromium, every page incl. Reference Data) | **37 passed** (run with `LANG=en_US.UTF-8`; the sandbox's POSIX locale breaks the chart library's `Intl`, unrelated to this change) |
| Lint / JS syntax | ruff clean · `node --check` OK |
| Migration | Alembic `0005` (instrument metadata, corporate actions, index membership); tests updated for the new revision |
| Backup contract change | Backups now also contain `refdata/*.parquet` (two backup tests updated accordingly) |
