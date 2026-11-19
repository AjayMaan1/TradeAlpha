# Historify — Phase 1 (Stabilize & Baseline) + Phase 2 (Foundation) — Implementation Report & Before/After Metrics

| | |
|---|---|
| **Plan** | `HISTORIFY_MIGRATION_AND_E2E_TEST_PLAN.md`, phases 1 and 2 |
| **Baseline ("before")** | git commit `79954db` (v1.2, unmodified) |
| **After** | working tree (uncommitted, for your review); **Python 3.14** primary, 3.12-3.14 supported |
| **Test environment** | mock OpenAlgo with the **Angel One profile** (3/s · 180/min · 5000/h, max-days per interval, NSE holidays, session expiry, fault injection). Both versions run the **same scenarios, same data, same mock**, each in its own process. |

---

## 1. Gate status

### G1 — Stabilize & Baseline
| Criterion | Status |
|---|---|
| Security hotfixes (loopback bind, no debugger, API key write-only) | ✅ verified by process check and tests |
| Leaked keys removed from the working tree | ✅ `sample/` now reads `OPENALGO_API_KEY` from the environment |
| **Keys rotated + git history purged** | ⛔ **aborted as instructed**: the workspace `.git` pre-dated this work (a one-commit `baseline` snapshot created before the review, no remote) so it was left untouched. The keys live in the **upstream** repository's history; run `scripts/purge_leaked_keys.sh` there after rotating them. |
| Reliability hotfixes on v1 (timeouts, retries, chunking, no-data, rate limits, WAL, singleton scheduler, logging) | ✅ |
| 20-symbol daily download: no 429s | ✅ 20/20, 0 × 429 (baseline: 6–11 of 20 failed across runs) |
| CI + mock provider + sample datasets + characterization tests | ✅ `.github/workflows/ci.yml`, `tests/mock_provider`, L1 legacy fixture, 9 strict-xfail tests |
| **Golden capture / daily convention from a live instance** | ⏳ **could not run here**: no OpenAlgo instance, no credentials, and Angel One endpoints are unreachable from the sandbox. It must run on your machine (~5 min, read-only). Not blocking: the v2 normaliser is proven correct for both conventions (see D below). |
| Angel One limits verified live | ⏳ **needs you** (same session as the golden capture); see `docs/providers/angelone.md` |

### G2 — Foundation
| Criterion | Status |
|---|---|
| BarStore contract green on DuckDB **and** SQLite | ✅ 25 contract tests (incl. property-based) |
| DuckDB ingest ≥ 100k rows/s | ✅ ~480–600k rows/s measured (CI perf job enforces ≥ 100k) |
| Importer reconciles the L1 fixture 100% | ✅ 11/11 attributable series match the broker oracle by checksum; 1 collision + 1 orphan reported; v1 file byte-identical |
| Startup self-test detects the daily convention | ✅ `flask historify selftest` (tested against both conventions) |
| No dynamic table creation in the v2 core | ✅ CI grep gate + repo test |

---

## 2. Before / after metrics

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

**How to read this.**
- **Noise.** The sandbox was noisy. Timings are medians where marked; across runs the v1 ORM write rate varied 960–1,683 rows/s, so the storage write speed-up is ~290–500×, not one exact number.
- **Baseline varies.** The v1 multi-symbol result varies between runs (6, 9 and 11 of 20 symbols succeeded). That variability *is* the defect.
- **Section D.** The **v1 download path intentionally still stores daily bars the old way**, to keep old and new rows in v1 tables consistent. The correct representation lives in the v2 BarStore, and the importer converts existing v1 data. The v1 path is switched over in Phase 3.
- **Quotes are slower on purpose.** The new provider paces requests to 80% of the broker limit instead of bursting into it (0 errors vs 3). The real fix (WebSocket feed + cache) is Phase 4.
- **Chart API** is unchanged code; the difference is measurement noise. Paging and incremental updates are Phase 4.

Reproduce:
```bash
git archive 79954db historify-master | tar -x -C /tmp/v1                # baseline tree
python benchmarks/scenarios.py     --app-dir /tmp/v1/historify-master/historify --mock-dir historify --out before.json
python benchmarks/scenarios.py     --app-dir historify                          --mock-dir historify --out after.json
python benchmarks/process_checks.py --app-dir <copy of each tree>              --mock-dir historify --out x_proc.json
python benchmarks/report.py --raw docs/metrics/raw
```
Raw results: `docs/metrics/raw/*.json`.

### Engineering metrics

| Metric | Before | After |
|---|---|---|
| Automated tests | 0 | **140** (131 passing + 9 strict-xfail characterizations of known v1 defects), identical on Python 3.12 / 3.13 / 3.14 |
| Test layers | none | unit 46 · contract 25 · integration 27 · API-level E2E 36 · repo hygiene 6 |
| Line coverage, v2 core (`app/core`) | n/a | **92%** |
| Line coverage, whole app (incl. untouched v1 routes) | n/a | 75% |
| CI | none | lint · test matrix (Ubuntu + Windows × Py 3.12/3.13/3.14) · perf budget · licence allow-list · gitleaks |
| Lint (ruff) on new and changed code | n/a | clean |
| Dependency licences | unchecked | all runtime deps AGPL-compatible (verified in a clean venv) |
| Python support | undeclared | **3.14 primary, 3.12-3.14 supported**, suite run on all three (131 pass + 9 xfail on each); Linux + Windows wheels verified |

---

## 3. What was implemented

### Phase 1 — Stabilize & Baseline (on v1)
| Task | Change | Findings |
|---|---|---|
| 1.3 Security | `run.py`: `127.0.0.1`, no debugger, no reloader, refuses debug on a non-loopback host; API key write-only (`configured` + last 4); generated per-install `SECRET_KEY`; keys removed from `sample/` | SEC-01..04 |
| 1.4 Transport | v1 fetcher now bridges to the v2 provider: pooled httpx client, 60 s timeout, retries with backoff + jitter, `Retry-After`, error classification, auth circuit breaker, chunking by Angel One max-days | DL-02, DL-03, DL-07, DL-08 |
| 1.4 Rate limits | new multi-window limiter (3/s + 180/min + 5000/h × 80%), never sleeps under its lock, safety margin at window edges | DL-09 |
| 1.4 W/M | rejected explicitly; removed from download, scheduler, export and chart UIs | DL-04 |
| 1.4 DB | SQLite WAL + busy timeout; bulk `INSERT … ON CONFLICT` instead of per-row SELECT+INSERT | DB-01, DL-06 |
| 1.4 Scheduler | not started by `create_app`; single owner via an OS lock file; fixed `zip(symbols, None)`; IST "today"; jobs manageable before start | SC-01, SC-04 |
| 1.4 Misc | logging configured once (+ rotating file); missing imports; defaults committed; working `VACUUM`; real DB path; reset keeps credentials; JSON 401 for API calls; real Excel parsing; nav/favicon/`/profile` fixes; theme key; honest "Clear all data" | OPS-01/02, DB-05/06/07, UI-05/09, EX-05 |
| 1.5 Hygiene | AGPL statement consistent; `SECURITY.md`, `CONTRIBUTING.md`, pre-commit (ruff, gitleaks); `.env.sample`; `scripts/purge_leaked_keys.sh` (not run) | SEC-02 |
| 1.7–1.11 Test infra | Angel One mock with `expected_bars()` oracle; L1 legacy fixture built by the **unmodified v1 code**; characterization tests; injectable clock; CI | — |
| Dependencies | openalgo SDK (+ numba/llvmlite) removed from the app; added duckdb, alembic, filelock, cryptography, openpyxl; dead modules deleted (`rate_limiter.py`, `data_fetcher_chunked.py`) | OPS-04 |

### Phase 2 — Foundation (v2 core, `app/core/`)
| Task | Module | Notes |
|---|---|---|
| 2.1 Meta DB | `meta/models.py`, `meta/db.py`, `meta/migrations/` | instruments (`UNIQUE(symbol, exchange)`), watchlists, intervals, **sync_state (per instrument × interval)**, jobs, job_tasks, schedules, schedule_runs, market_calendar, app_settings; Alembic `0001` (+ seed), WAL, foreign keys on |
| 2.2 BarStore | `store/base.py`, `store/duckdb_store.py`, `store/sqlite_store.py` | unified `ohlcv(instrument_id, interval_id, ts, …)`; idempotent replace-range; paging/tail/since; W/M resampling; IST daily counts; CSV/Parquet export; single writer thread (DuckDB) |
| 2.3 Singleton | `singleton.py` | OS file lock; verified across processes |
| 2.4 Time | `timeutil.py`, `clock.py` | UTC epoch for bar open; daily = 00:00 IST of the trading date; *the trading date rule is convention-independent* (property-tested) |
| 2.5 Provider | `provider/openalgo.py`, `errors.py`, `normalize.py`, `profiles.py` | REST direct (not the SDK), classification, retries, circuit breaker, vectorised normalisation + OHLC validation, self-test |
| 2.6 Calendar | `calendar.py`, `data/market_calendar.json` | sessions per exchange; **holiday list is a SAMPLE — verify against official circulars** |
| 2.7 Importer | `importer/legacy.py`, `app/cli.py` | read-only on v1, backup, attribution via watchlist × intervals, collision/orphan report, 18:30 fix, W/1d merge, reconciliation, dry-run, idempotent |
| 2.8 Secrets | `secrets.py`, `meta/settings_repo.py` | Fernet at rest (OS keyring or 0600 key file); masked public view |

---

## 4. Not changed yet (by plan), pinned by tests

These v1 defects are **still present** and are pinned by strict-xfail tests in `tests/e2e/test_characterization.py`. When a later phase fixes one, its test flips and forces the marker's removal.

| Finding | Defect | Phase |
|---|---|---|
| CP-01 | checkpoint keyed by symbol only (continue skips data across intervals) | 3 |
| CP-02 | checkpoint advanced past data actually received | 3 |
| CP-03 | intraday scheduler look-back capped at 3/7 days | 3 |
| CP-04 | market-close daily job skips today's bar | 3 |
| DL-05 | v1 path stores daily bars as "previous day 18:30" | 3 (v2 ingestion); importer already fixes stored data |
| DT-01 | `M&M` / `M-M` share a v1 table | 3/5 |
| DB-04 | same symbol on two exchanges in the v1 watchlist | 4 |
| EX-01 | export parameters overflow the session cookie | 4 |
| EX-02 | "individual" export contains only the first symbol | 4 |

Also unchanged (planned): downloads are still synchronous in the request (Phase 3), chart full-history payload (Phase 4), quotes via WebSocket (Phase 4), login/CSRF (Phase 5).

**Known limitations of this delivery**
- After an auth failure the breaker blocks broker calls for 60 s unless **Settings → Test connection** is used after re-login (a Resume button arrives with the Phase 3 job engine).
- The market-calendar holiday list is a development sample.
- The web UI does not use the v2 store yet. It is populated via `flask historify import-legacy` and by the Phase 3 engine.

---

## 5. Actions needed from you (original list, superseded by §7)

1. **Rotate** the two OpenAlgo API keys that were committed in `sample/` (they are compromised regardless of the purge).
2. **Approve the history rewrite**, then run `scripts/purge_leaked_keys.sh` and force-push. Forks and clones must re-clone.
3. **Run the golden capture** against your OpenAlgo + Angel One instance (~5 min, read-only) and share `fixtures/golden/` (no API key is written):
   `OPENALGO_HOST=… OPENALGO_API_KEY=… python review_harness/capture_golden.py --out historify/tests/fixtures/golden`
4. Review the changes (uncommitted working tree), then commit and tag **v1.2.1** before Phase 3 starts.

---

## 6. Update: Python 3.14 upgrade (approved)

**What changed**
- **Interpreter:** Python **3.14** is the primary version (`historify/.python-version`); **3.12 and 3.13** stay supported. 3.11 was dropped because numpy 2.5 ships no 3.11 wheels. `run.py` and `start.bat` refuse older interpreters with a clear message.
- **Dependencies** re-resolved from a new top-level `requirements.in` into a **cross-platform lock** (`uv pip compile --universal`):
  - **major bumps:** pandas 2.2 → **3.1**, numpy 2.2 → **2.5**, SQLAlchemy 2.0 → **2.1**, python-dateutil 2 → 4;
  - **minor bumps:** Flask 3.1.3, Werkzeug 3.1.9, APScheduler 3.11.3 (kept on 3.x; 4.x is an API rewrite);
  - **dropped:** `requests` (unused) and `greenlet` (SQLAlchemy 2.1 no longer needs it).
  
  uv's marker-aware resolve gives the same 33 packages on Linux and Windows. `pip freeze` was not used, because it drops platform-specific dependencies.
- **Code modernised** for a 3.12 floor: `StrEnum`, `datetime.UTC`, and pyupgrade (`UP`) lint rules enabled for new code. One pandas-4 deprecation was fixed (`Series.dt.weekday` → `dt.day_of_week`).
- **Leaks found by Python 3.14's stricter `ResourceWarning`s, and fixed:**
  - the importer's backup used `sqlite3.connect()` as a context manager, which commits but never closes;
  - the `meta-upgrade` CLI never disposed its engine.
- **CI:** matrix is now Python 3.12 / 3.13 / 3.14 on Ubuntu + Windows; the lint, perf and licence jobs run on 3.14.

**Verification**

| Check | Result |
|---|---|
| Full suite, fresh venv from the new lock, Python 3.14.7 | 131 passed, 9 xfailed, core coverage 92% |
| Same, Python 3.13.15 | 131 passed, 9 xfailed, 92% |
| Same, Python 3.12.3 | 131 passed, 9 xfailed, 92% |
| Wheels for every locked package, cp312-cp314, Linux (manylinux) + Windows (win_amd64) | all available |
| Licence gate on the runtime lock (Python 3.14) | pass |
| Runtime install size, Python 3.14 | 312 MB (v1: 369 MB) |
| Real `run.py` on 3.14 | loopback only, no debugger, 1 process, 2 scheduled runs in 130 s |

**Performance, same v2 code, Python 3.12 + previous lock vs Python 3.14 + new lock**

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

All functional outcomes are identical. The sandbox is noisy: an early 3.14 run that overlapped a `pip install` showed DuckDB at 34k rows/s, and a quiet re-run showed 1.12M rows/s. Treat differences under ~2× as "no regression, likely modest gains", not as firm speed-ups.

## 7. Actions still needed from you (updated)

1. **Rotate** the two OpenAlgo API keys that were committed in `sample/`.
2. **Purge them from the upstream repository's history** (not this workspace; see §1): run `scripts/purge_leaked_keys.sh` in a clone of the upstream repo, then force-push.
3. **Run the golden capture on a machine that can reach your OpenAlgo + Angel One** (~5 min, read-only), then share `fixtures/golden/`:
   `OPENALGO_HOST=http://127.0.0.1:5000 OPENALGO_API_KEY=… python review_harness/capture_golden.py --out historify/tests/fixtures/golden`
4. Install **Python 3.14** (or ≥ 3.12) on the machines that run Historify, then `pip install -r requirements.txt` in a fresh venv.
