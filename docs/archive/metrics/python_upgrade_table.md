| Metric | v2 on Python 3.12 (previous lock) | v2 on Python 3.14 (new lock) | Change |
|---|---|---|---|
| Download 1-minute x 30 days (median of 3) | 0.53 s | 0.44 s | 1.2x better |
| Re-download same range | 0.47 s | 0.46 s | about the same |
| Download 1-minute x 6 months (47,625 bars) | 4.15 s | 3.29 s | 1.3x better |
| Worst concurrent write latency | 0.97 s | 0.45 s | 2.2x better |
| Chart API, full history (median of 7) | 0.679 s | 0.311 s | 2.2x better |
| Quotes for 50 symbols (rate-limit paced) | 6.16 s | 6.17 s | about the same |
| DuckDB ingest | 478,941 rows/s | 440,446 rows/s | about the same |
| SQLite-unified ingest | 122,476 rows/s | 231,478 rows/s | 1.9x better |
| DuckDB range read (41,760 bars) | 0.032 s | 0.017 s | 1.9x better |
| DuckDB disk per bar | 16.2 B | 16.5 B | about the same |
| W+M resample, 50 instruments x 10 y | 1.369 s | 0.729 s | 1.9x better |
| Legacy import of L1 fixture | 1.173 s | 0.526 s | 2.2x better |
| App start (create_app) | 1.705 s | 0.918 s | 1.9x better |
| All functional outcomes (sections A, D, E) identical | - | - | yes |
| Scheduled runs per trigger / bind address | 2 calls / http://127.0.0.1:5001 | 2 calls / http://127.0.0.1:5001 | same |
