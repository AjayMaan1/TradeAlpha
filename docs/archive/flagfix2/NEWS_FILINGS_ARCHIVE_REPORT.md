# News & Filings Archive: Implementation Report

| | |
|---|---|
| **Your decision** | Use free sources first; **start archiving news and filings now**. |
| **Delivered** | A News & Filings archive inside Historify: 23 free public sources, polite scheduler, append-only raw-first store, PDF attachments for watchlist names, backfill and self-test jobs, API, CLI, UI page (Data → News & Filings), backups, docs. |
| **Status** | Implemented and tested against mock websites. **Not yet run against the real sites**: the sandbox has no internet access. First step on your machine: `flask --app run.py historify feeds selftest` (or the Self-test button). |
| **Also answered** | The intraday question "pick volatile stocks via VIX?" → `INTRADAY_UNIVERSE_AND_VOLATILITY.md` |

---

## 1. What runs now

Once Historify starts (`python run.py`), the archive runs in the background. It never uses the broker or its rate
budget.

| Group | Sources | Cadence |
|---|---|---|
| Exchange filings | NSE announcements, BSE announcements, NSE board meetings (results calendar), NSE corporate actions | every 2–3 min 08:00–23:00 IST; calendars 2–3× a day |
| Big-investor / promoter disclosures | Insider trades (PIT), SAST Reg 29 stake changes, promoter pledge (Reg 31), bulk deals, block deals | daily (morning + evening), bulk/block on trading days |
| Flows | FII/DII provisional cash (only the latest day is published, so daily archiving is the only way to keep it) | 18:30, 20:30 on trading days |
| Regulators | RBI press releases, RBI notifications, SEBI, PIB | every 15 min |
| News | Economic Times (2), Moneycontrol (3), Business Standard, Mint, Financial Express, BusinessLine (RSS: headline, summary, link, time) + GDELT | every 5 min (RSS), 15 min (GDELT) |

**Key properties**
- **Point-in-time.**
  - `published_at` is the exchange/publisher timestamp; use it in backtests.
  - `first_seen_at` is when Historify stored the item; use it for live parity.
- **Append-only, raw-first.**
  - Every record is kept exactly as received (compressed).
  - Changed records keep their old versions.
  - Unparseable responses are kept whole, so they can be re-parsed after a fix.
- **Polite.**
  - ≥ 2 s between requests per site.
  - Conditional GET for RSS.
  - Per-source exponential backoff (1 min → 1 h).
  - Self-identifying User-Agent.
- **Classified.** Filings are labelled `results`, `order_win`, `credit_rating`, `pledge_encumbrance`,
  `auditor_change`, `management_change`, `fund_raising`, `m_and_a` and about 20 more, using versioned rules.
  `feeds relabel` re-classifies after the rules improve.
- **Attachments.** PDFs/XML of important filings for **your watchlist symbols** (15 MB per file, 500 MB per day,
  configurable).
- **Fixable without code.**
  - URLs, cadence and field mappings are overridable in Settings JSON (`feeds_sources`, `feeds_hosts`).
  - Custom RSS feeds can be added the same way.
- **Backed up.** `feeds.db` is included in the weekly verified backup.

## 2. What to do on your machine (10 minutes)

1. Start Historify as usual. Open **Data → News & Filings → Sources**.
2. Click **Self-test sources**. Results appear on the Jobs page: per source, OK / EMPTY / FAIL with the HTTP status,
   records understood and sample titles.
3. For any source marked FAIL:
   - wait a few minutes (NSE occasionally blocks bursts) and re-run it; then
   - check `docs/feeds.md` → "Fixing or adding a source"; or
   - disable it. The rest keep working.
4. Optional history: `flask --app run.py historify feeds backfill nse_announcements --start 2024-01-01 --end 2024-12-31`
   (about 15–20 min per year at the polite pace). Insider and SAST disclosures also support backfill.
5. Add the symbols you care about to a watchlist, so their result/order/rating PDFs are archived too.

**Sources to start archiving early, because they have no history** (only what you record exists later): FII/DII flows,
news RSS, the pledge snapshot.

## 3. Verification in the sandbox

| Layer | Result |
|---|---|
| New unit tests (`tests/unit/test_feeds_parsing.py`) | **43 passed**: 14 timestamp formats (NSE, BSE, RSS, GDELT, epoch), tolerant JSON/RSS/Atom/CSV/GDELT parsing, XML-bomb safety, classifier cases, source registry and overrides, store idempotency + revisions + full-text search |
| New integration tests (`tests/integration/test_feeds.py`) | **18 passed** against a mock of NSE/BSE/RSS/GDELT. They cover: cookie warm-up and refresh, BSE pagination and Referer, RSS ETag (304), idempotency and revisions, parse error → raw kept + backoff, HTTP 5xx retry vs 404, schedules (active/idle, trading-day-only across a holiday), the scheduler tick, attachment policy and budget, instrument linking, backfill and self-test jobs, API, CLI, backups, autostart switch |
| New browser tests (`tests/ui/test_feeds_page.py` + page sweep) | **3 passed**: feed table, filters, full-text search, detail drawer (raw record rendered as text; an HTML/JS payload stays inert), sources tab, toggles; no console errors, no CSP violations |
| Full existing suite (all non-browser layers, incl. slow/perf) | **315 passed, 9 expected v1 xfails**. Two backup tests were updated because backups now also contain `feeds.db` (intended). |
| Full browser suite | **33 passed**. 5 chart tests need `LANG=en_US.UTF-8` in this sandbox (`Invalid language tag: en-US@posix` from the sandbox's POSIX locale; not related to this change). |
| Lint / JS syntax / repo hygiene | ruff clean · `node --check` OK · hygiene gates pass (no `create_all`: the archive uses explicit versioned DDL) |
| Offline behaviour with the real URLs | Self-test over 5 real sources fails fast and cleanly (DNS errors, 2 s total), logs every attempt, and the service keeps running |

## 4. Files

**New**
- `app/core/feeds/` (about 1,800 lines):
  - `sources.py` (registry);
  - `http.py` (polite client);
  - `parsers.py`;
  - `classify.py`;
  - `store.py` (SQLite: items, revisions, raw blobs, fetch log, attachments, FTS5);
  - `service.py` (scheduler, runs, attachments, backfill, self-test, export).
- `app/web/feeds_api.py`: `/api/v2/feeds/...` (status, items, item detail, kinds, log, run, source patch, settings,
  self-test, backfill, attachment download).
- `app/templates_v2/feeds.html` and `app/static/js/v2/pages/feeds.js`: the News & Filings page.
- `tests/mock_feeds/server.py`, `tests/unit/test_feeds_parsing.py`, `tests/integration/test_feeds.py`,
  `tests/ui/test_feeds_page.py`.
- `docs/feeds.md`: operator guide (sources, time semantics, guarantees, fixing sources, backfill, research usage,
  terms of use).

**Modified**
- Services wiring (start/stop/close, `HISTORIFY_FEEDS_AUTOSTART`).
- Background job kinds (`feeds_backfill`, `feeds_selftest`).
- Weekly backup (+ `feeds.db`).
- CLI group `historify feeds ...`.
- Navigation + command palette.
- README, `.env.sample`.
- Test config: the archive never autostarts in tests, so CI never polls real websites.

## 5. Known limitations / next steps

| Item | Plan |
|---|---|
| Default endpoints and field names are unverified | Self-test on your machine; fix through Settings JSON; mark sources `verified` |
| Filings are stored as events; structured tables (insider trades, pledges, deals as typed columns) are not built yet | Parse from the stored raw records in the next step (no re-download needed): `deals`, `insider_trades`, `encumbrance` tables + pledge metrics (catalogue §4) |
| Results XBRL (point-in-time fundamentals) not parsed yet | Catalogue §5: core XBRL fields from the archived filings (≈ 10–15 d) |
| News entity linking (headline → company) | Rules on company names/symbols, later a local NLP model; exchange filings already carry symbols |
| BSE items are linked by BSE scrip code only (no NSE symbol) | Add the BSE ↔ ISIN ↔ NSE mapping from the security masters (refdata job) |
| F&O ban list, ASM/GSM lists, participant OI | Add as feeds sources (daily documents) together with the intraday scanner work |
