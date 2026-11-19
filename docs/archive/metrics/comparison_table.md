| Area | Metric | Before (v1.2 baseline) | After (Phase 1 + 2) | Change | Finding |
|---|---|---|---|---|---|
| **A. Download reliability (Angel One limits)** | 20 symbols x daily x 1 month: symbols succeeded | 9/20 | 20/20 | all succeed | DL-09 |
|  | ... broker HTTP 429 rate-limit rejections | 11 | 0 | eliminated | DL-09 |
|  | 1-minute x 90 days (Angel cap 30 d/request) | FAILED (0 rows) | OK (22,875 rows) | now works | DL-07 |
|  | 5-minute x 150 days (cap 100 d) | FAILED (0 rows) | OK (7,800 rows) | now works | DL-07 |
|  | Daily x 10 years (cap 2000 d) | FAILED (0 rows) | OK (2,606 rows) | now works | DL-07 |
|  | Weekend / holiday range reported as failure | yes | no | fixed | DL-03 |
|  | 'Weekly' request | 'success', 61 DAILY bars stored as weekly | rejected with explanation, 0 rows | no silent corruption | DL-04 |
|  | Broker latency 6.5 s (SDK timeout 5 s) | FAILED after 5.07 s | OK in 6.92 s | now works | DL-02 |
|  | 30% random HTTP 5xx, 10 symbols: succeeded | 8/10 | 10/10 | retries with backoff | DL-02 |
|  | Expired broker session, 5 symbols: calls sent to broker | 5 | 1 | auth circuit breaker | Angel One |
|  | ... recovers after re-login + 'Test connection' | yes | yes | - |  |
|  | Writes during a 6-month 1-minute download: failed ('database is locked') | 6/19 | 0/8 | eliminated | DB-01 |
|  | ... worst write latency | 10.09 s | 0.97 s | 10.4x faster | DB-01 |
| **B. Performance** | Download 1-minute x 30 days (7,500 bars), median of 3 | 8.37 s | 0.53 s | 15.8x faster | DL-06 |
|  | ... re-download (update path) | 6.93 s | 0.47 s | 14.7x faster | DL-06 |
|  | ... end-to-end rows/s | 896 | 14,151 | 15.8x | DL-06 |
|  | ... share of time spent writing the DB | 97.4% | 53.7% | fetch now dominates | DL-06 |
|  | Download 1-minute x 6 months (47,625 bars) | 52.5 s | 4.15 s | 12.7x faster | DL-06 |
|  | Chart API, full 1-minute history (code unchanged) | 0.655 s, 1372 KB | 0.679 s, 1372 KB | unchanged (Phase 4) | CH-01 |
|  | Quotes for 50 symbols | 4.84 s, 3 errors | 6.16 s, 0 errors | slower: paced to broker limit; WS/cache in Phase 4 | UI-08 |
| **C. Storage engine (937,500 1-minute bars, identical data)** | Write throughput (rows/s) | 960 (v1 ORM per row) | 478,941 DuckDB / 122,476 SQLite-unified | 498.9x | DL-06 |
|  | Disk per bar | 192.0 B | 16.2 B DuckDB / 66.0 B SQLite | 11.9x smaller | DB-03 |
|  | Range read, 41,760 bars | 1.316 s | 0.032 s DuckDB | 41.1x faster |  |
|  | Tables needed for 10 series | 11 | 1 | dynamic tables gone | DT-* |
|  | Weekly + monthly bars for 50 instruments x 10 y | not possible (W = mislabelled D) | 1.369 s (derived from D) | new | DL-04 |
| **D. Data correctness** | Daily bar of 2025-01-01 stored as | 2024-12-31 18:30 | 2025-01-01 (v2, both broker conventions) | fixed in v2 store | DL-05 |
|  | M&M and M-M stored in | 1 shared table | 2 separate series (v2) | fixed in v2 store | DT-01 |
|  | v1 database import into v2 store | not available | 11/11 series reconciled, 1 collision + 1 orphan reported, v1 file unchanged | new | DT-01/02 |
| **E. Security & operations** | Listening addresses | http://127.0.0.1:5001, http://192.0.2.2:5001 | http://127.0.0.1:5001 | loopback only | SEC-01 |
|  | Werkzeug debugger exposed | yes | no | fixed | SEC-01 |
|  | Scheduled runs per trigger (persisted 1-min job, 130 s) | 4 calls (2 processes) | 2 calls (1 process) | no duplicates | SC-01 |
|  | API key returned by GET /api/settings | plaintext | masked (configured + last 4) | fixed | SEC-03 |
|  | Hard-coded API keys in working tree | 2 | 0 | history purge pending approval | SEC-02 |
|  | Default settings persisted | 0 | 9 | fixed | DB-05 |
|  | JSON API when OpenAlgo not configured | HTTP 302 redirect to HTML | HTTP 401 JSON | fixed | UI-05 |
|  | 'Optimize database' | fails (NameError) | works | fixed | DB-06 |
|  | Application INFO logs | suppressed (root level 30) | visible (level 20) + rotating file | fixed | OPS-01 |
|  | Scheduler started by create_app (tests/CLI/workers) | yes | no | fixed | SC-01 |
|  | Excel import returns | fake rows (RELIANCE...) | the uploaded rows (HDFCBANK...) | fixed | EX-05 |
|  | Pages offering W/M intervals | 4 | 0 | fixed | DL-04 |
|  | /profile/ ; watchlist nav highlight | HTTP 500 ; no | HTTP 404 ; yes | fixed | UI-09 |
|  | Export of 800 symbols: session cookie size | 7,922 B | 7,746 B | unchanged (Phase 4) | EX-01 |
|  | Runtime install (site-packages) | 369 MB | 298 MB | -19% (numba/llvmlite removed) |  |
