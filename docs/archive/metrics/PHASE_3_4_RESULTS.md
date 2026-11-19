# Phase 3-4 results: before / after

Measured with `benchmarks/phase34.py` against the mock OpenAlgo broker (Angel One limits: 3/s, 180/min, 5,000/h; Historify budget 80%), frozen clock Fri 2025-03-28 16:00 IST, Python 3.12.3. *Before* = v1.2 baseline, *Phase 1-2* = previous report (both from `docs/metrics/raw`). Absolute timings come from a slow shared sandbox VM (DuckDB `SELECT 1` takes ~1-5 ms there vs ~0.1 ms on a desktop); ratios and counts are the meaningful part.

| Area | Metric | Before (v1.2) | After Phase 1-2 | After Phase 3-4 (v2) | Gate |
|---|---|---|---|---|---|
| **A. Downloads** | 20 symbols x D x 1 month: succeeded / HTTP 429 | 9/20 · 11 | 20/20 · 0 | 20/20 · 0 (8.08 s, background job) |  |
|  | 1m x 90 d · 5m x 150 d · D x 10 y (chunked to Angel One caps) | all 3 FAILED | OK (sync, in request) | OK: 3+2+2 requests, 0 range errors, 0 x 429 |  |
|  | 1 year of 1m for 10 symbols (130 requests) | not possible | blocks the HTTP request | succeeded in 54.1 s = 2.4 req/s (budget 2.4), 0 x 429, 971,250 bars | G3 |
|  | 30% HTTP 5xx, 10 symbols: succeeded | 8/10 | 10/10 | 10/10 (4 retries) |  |
|  | Broker session expired: broker calls / outcome | 5 calls, all fail | 1 call, all fail | 1 call, job `paused_auth`, 0 calls while paused, resumed → succeeded |  |
|  | Truncated broker responses | silently stored, checkpoint advanced (CP-02) | same | detected (2 tasks flagged), shown as gaps/stale, repaired in 2 requests | G3 |
|  | Kill -9 during a 200-symbol x 2-interval job, restart | job lost | job lost | 113/400 done at kill → resumed to 400/400; checksums identical to an uninterrupted run: **True** (400 series) | G3 |
|  | UI API p95 while a 500-symbol job runs | n/a (downloads block requests) | n/a | p95 168.2 ms over 2178 requests (worst: coverage 277.5 ms); 0 errors; 'database is locked': 0 | G3 (< 300 ms) |
|  | Coverage vs calendar after 10% truncation + repair + incremental (10 symbols x D, 5m) | n/a | n/a | 100.0% · all series ok: True | G3 |
| **B. Performance** | Download 1m x 30 days (median of 3) | 8.37 s | 0.53 s | 0.31 s (25,403 rows/s end to end) |  |
|  | Chart first paint (1m series) | 1372 KB / 655 ms (full history, every load) | 1372 KB / 679 ms | 71.3 KB (20.8 KB gzip) / 14.0 ms for 1,500 of 97,125 bars (1 year of 1m) | G4 (≤ 200 KB, ≤ 150 ms) |
|  | Scroll back one page · unchanged poll (ETag) | n/a (reload everything) | n/a | 17.5 ms · HTTP 304, 0 bytes, 5.9 ms | G4 |
|  | Weekly bars | mislabelled daily data | rejected | derived from D in 13.4 ms |  |
|  | Quotes, 50-symbol watchlist | 4.84 s per page load, 3 errors | 6.16 s per page load, 0 errors | 2.56 ms from cache; first visit: page answers in 1.51 s, all 50 in 8.37 s (broker quote limit) | G4 (< 1 s from cache) |
|  | Export 800 series (1 y daily) | session cookie 7,922 B > 4 KB limit | 7,746 B | background job, no cookie; one streamed query 0.74 s vs 3.92 s per-series (5.3x); csv-zip D+W job 6.24 s |  |
|  | Plan a repair for 400 series | n/a | n/a | 0.079 s with one grouped query vs 2.73 s per series (34.5x) |  |
| **C. Scheduler** | Post-close run fetches today's final daily bar | no (CP-04) | no | 15:45 run → latest bar 2025-03-28 (2 requests) | G4 |
|  | Holiday behaviour | runs and 'fails' | runs and 'fails' | `skipped_holiday` recorded; preview never shows a holiday (skips listed: 2025-03-31, 2025-04-10, 2025-04-14, 2025-04-18) | G4 |
| **D. UI** | Pages with a strict CSP / external resources | no CSP · 7 CDN URLs | no CSP · 7 CDN URLs | 9/9 · 0 |  |
|  | `innerHTML` uses · inline event handlers · inline styles | 91 · 45 · - | same | 0 · 0 · 0 | G4 |
|  | Page weight (first visit, uncompressed, cached afterwards) | CDN-dependent | CDN-dependent | 239-453 KB, 7-8 requests |  |
| **E. Storage** | DuckDB file for 23,625 1m bars | - | 16.2 B/bar at 937k bars | 44.9 B/bar (fixed ~1 MB file overhead dominates small stores) |  |
