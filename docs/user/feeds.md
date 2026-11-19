# News & Filings archive

Historify keeps a local, append-only archive of **free public** market information next to your price data, so that
research and backtests can use it **point-in-time**. It never calls your broker and never uses the broker's rate budget.

UI: **Data → News & Filings** (feed with filters and full-text search, per-source health, run now, enable/disable,
self-test). CLI: `flask --app run.py historify feeds ...`. API: `/api/v2/feeds/...`.

> **Verify on your machine first.** None of these websites offers an official, stable API. The defaults were written
> from the publicly observed endpoints and formats but could **not** be checked from the development sandbox (no
> internet). After installing, run **Self-test** (UI) or `flask --app run.py historify feeds selftest`. It makes one
> request per source and reports which ones answer and how many records were understood. Every source is marked
> `unverified` until you flip `verified` in its settings.

## What is archived

| Group | Source id | What | Default schedule (IST) |
|---|---|---|---|
| Filings | `nse_announcements` | Every NSE corporate announcement (results, outcomes of board meetings, orders, credit ratings, pledges, management changes, investor meets, transcripts…) with the exchange's dissemination time and the PDF link | every 2 min 08:00–23:00, else every 30 min |
| Filings | `bse_announcements` | BSE corporate announcements (incl. BSE-only companies), paginated | every 3 min 08:00–23:00 |
| Filings | `nse_board_meetings` | Board-meeting intimations: **results dates in advance** | 08:15, 13:00, 20:15 |
| Filings | `nse_corporate_actions` | Dividends, splits, bonus, rights with ex/record dates | 08:20, 20:20 |
| Disclosures | `nse_insider_trading` | Insider trades (SEBI PIT Reg 7): promoters, directors, designated persons | 08:30, 21:00 |
| Disclosures | `nse_sast_reg29` | Substantial acquisitions: crossing 5% and every 2% change | 08:35, 21:05 |
| Disclosures | `nse_pledge` | Promoter pledge / encumbrance (SAST Reg 31) per company | 08:40, 21:10 |
| Disclosures | `nse_bulk_deals`, `nse_block_deals` | Daily bulk (> 0.5% of equity) and block deals with client names | 17:45, 19:30 (trading days) |
| Market | `nse_fii_dii` | FII/DII provisional cash flows. **The page shows only the latest day: archive it daily or it is lost.** | 18:30, 20:30 (trading days) |
| Regulators | `rbi_press_releases`, `rbi_notifications`, `sebi_updates`, `pib_releases` | RBI, SEBI and Government of India releases | every 15 min |
| News | `et_markets`, `et_stocks_news`, `moneycontrol_*`, `business_standard_markets`, `livemint_markets`, `financial_express_market`, `businessline_markets` | Headline, summary, link and publish time (no article bodies) | every 5 min 08:00–23:00 |
| News | `gdelt_india_markets` | GDELT news index filtered to Indian markets/economy | every 15 min |

Expected volume (rough): 5–10 k records per trading day, **≈ 0.5–1 GB per year** for the database (raw records are
compressed). Attachments are limited by the attachment policy (below).

## Time semantics (important for backtests)

| Field | Meaning | Use it for |
|---|---|---|
| `published_at` | The **source's** own timestamp: exchange dissemination time for filings, `pubDate` for news, `seendate` for GDELT (epoch seconds UTC; naive source times are IST) | Research and backtests: "was this known at time *t*?" → `published_at <= t` |
| `first_seen_at` | When Historify first stored the item (includes polling latency) | Live-parity checks and measuring your own latency |
| `event_date` | The date the item is *about* (board-meeting date, ex-date) | Event calendars |

Never use an item before its `published_at`. Results published at 19:40 IST can only affect the **next** session's
trades.

## Guarantees

- **Append-only and idempotent.** `(source, external id)` is unique, so re-fetching stores nothing twice. When a
  record changes (a corrigendum or a revised figure), the new version replaces it and the **old raw record is kept**
  in `item_revisions`.
- **Raw-first.** Every item stores the complete record exactly as received (compressed JSON). Mapped fields (title,
  symbol, kind…) are conveniences and can be recomputed. A response that fails to parse is kept whole (`raw_blobs`,
  reason `parse_error`) so nothing is lost while a mapping is fixed. Daily documents and snapshots (bulk deals, FII/DII,
  pledge data, board meetings, corporate actions) are also kept whole (reason `document`), de-duplicated by checksum.
- **Polite.** One request at a time per website, at least 2 s apart for NSE/BSE and 3–5 s for others, with a
  browser-like User-Agent that identifies Historify. Conditional GET for RSS (unchanged feeds cost a `304`). Failing
  sources back off exponentially (1 min → 1 h) without affecting the others.
- **Isolated.** The archive has its own SQLite file (`instance/feeds/feeds.db`). It is included in the weekly backup
  (`feeds.db` next to `historify_meta.db`).

## Kinds (filings classifier)

Filings and disclosures get a `kind` from versioned rules (`rules-1`):

`results, board_outcome, board_meeting_intimation, auditor_change, pledge_encumbrance, sast_insider, credit_rating,
order_win, buyback, fund_raising, dividend, bonus_split, m_and_a, management_change, litigation_regulatory,
transcript, audio_recording, investor_meet, investor_presentation, press_release, business_update, agm_egm,
trading_window, shareholding, annual_report, compliance, clarification, other`

Structured sources use fixed kinds: `insider_trade`, `sast_disclosure`, `pledge_snapshot`, `bulk_deal`, `block_deal`,
`fii_dii_flow`, `board_meeting`, `corporate_action`. After the rules are improved, `flask ... feeds relabel`
re-classifies everything already stored.

## Attachments (PDF/XML)

New or changed filings with an attachment are downloaded according to the policy (Settings JSON `feeds_attachments`,
or `PUT /api/v2/feeds/settings`):

```json
{"scope": "watchlists", "kinds": ["results", "order_win", "credit_rating", "..."], "max_bytes": 15000000,
 "daily_budget_bytes": 500000000}
```

- `scope`: `none`, `watchlists` (symbols in any watchlist; the default) or `all`.
- Files are stored content-addressed under `instance/feeds/files/` and served at
  `/api/v2/feeds/attachments/<id>/file`.

## Fixing or adding a source without code changes

Sources are overridden through the `feeds_sources` setting (JSON). The following changes the cadence of one source,
disables another, and adds a custom RSS feed:

```json
{
  "et_markets": {"active_every_s": 600},
  "moneycontrol_business": {"enabled": false},
  "my_sector_feed": {"label": "My sector RSS", "group": "news", "fmt": "rss", "url": "https://example.com/rss",
                     "item_type": "news"}
}
```

- `PATCH /api/v2/feeds/sources/<id>` with `enabled`, `active_every_s`, `idle_every_s`, `daily_at`, `url` and the
  other fields does the same for one source.
- Host bases (`{nse}`, `{nse_archives}`, `{bse_api}`, `{gdelt}`) can be changed through `feeds_hosts`.
- Field mappings are lists of candidate field names, for example `"time": ["an_dt", "exchdisstime"]`. If NSE renames a
  field, add the new name to the list.
- Minimum cadence is 30 s. Please keep polling modest: these are public websites.

## Backfill

Sources whose URL takes a date range support history:
- `nse_announcements` and `bse_announcements` (1-day windows);
- `nse_insider_trading` and `nse_sast_reg29` (7-day windows).

```bash
flask --app run.py historify feeds backfill nse_announcements --start 2024-01-01 --end 2024-12-31
# or POST /api/v2/feeds/backfill {"source": "...", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}  (runs as a job)
```

- Windows are fetched newest first, at the polite pace (a year of daily windows takes about 15–20 minutes).
- Backfill never changes the live schedule.
- RSS feeds and the FII/DII page have **no history**: only what you archive from now on exists. Start early.
- For news history before that, GDELT's raw files and BigQuery tables reach back to 2015.

## Using the archive in research

```bash
flask --app run.py historify feeds export items.parquet --since 2025-01-01 --format parquet
```

```python
import duckdb
items = duckdb.sql("""
  SELECT symbol, kind, title, to_timestamp(published_at) AS published, raw
  FROM 'items.parquet' WHERE kind IN ('results', 'order_win', 'credit_rating') ORDER BY published
""").df()
```

Or query `instance/feeds/feeds.db` directly with any SQLite tool (read-only while Historify runs). The tables are
`items`, `item_revisions`, `raw_blobs`, `attachments`, `fetch_log` and `source_state`; full-text search runs through
`items_fts`. Schema v2 (upgraded in place) adds `item_entities` - derived company links for items without an exchange
symbol (BSE scrip code / ISIN → NSE symbol, headline → companies; see [intel.md](intel.md)). The symbol filter of the
News & Filings page and `GET /api/v2/feeds/items?symbol=` include linked items. Typed disclosures (insider trades,
deals, SAST, pledges) extracted from these records live in the meta DB - see [intel.md](intel.md).

## Terms of use

These are public pages intended for people.
- The archive fetches them at a low, human-like rate, for **personal research**, and stores headlines/links rather
  than republishing articles.
- Commercial use or redistribution needs a data licence (NSE Data & Analytics, BSE, the publishers).
- Disable any source you are not comfortable with.
- Set `HISTORIFY_FEEDS_AUTOSTART=0` to keep the archive from starting with the server.
