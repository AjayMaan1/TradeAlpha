# Historify - test report

Generated 2026-11-15 15:28:46 · Python 3.12.3 · Linux-6.18.44-fc-v310-x86_64-with-glibc2.39

**Overall: ✅ ALL PASSED** - 762 tests, 752 passed, 0 failed, 1 skipped, 9 expected failures (v1 characterisation), 456 s

| Layer | Result | Tests | Passed | Failed | Skipped | XFail | Covers |
|---|---|---|---|---|---|---|---|
| Static checks | ✅ pass | - | - | - | - | - | ruff (pyflakes, bugbear, imports, pyupgrade) over app, tests, run.py, benchmarks |
| Unit | ✅ pass | 352 | 351 | 0 | 1 | 0 | pure logic: time/IST, calendar (holidays, MCX DST), intervals, rate limiter, error classification, secrets, planner (property tests), schedule triggers |
| Contract (storage) | ✅ pass | 29 | 29 | 0 | 0 | 0 | BarStore contract on DuckDB and SQLite: idempotent writes, upsert, paging, resample, batched export parity, property tests |
| Integration + API E2E | ✅ pass | 280 | 280 | 0 | 0 | 0 | download engine under faults (429/5xx/timeouts/truncation/expired session/restart), scheduler time-travel, API v2, data integrity (ledger, quarantine, revisions, audit, archive, rewrite, backups, maintenance, snapshots), 1m roll-up + roll-up audit, quotes, CLI, legacy v1 import, reference data, intelligence, backtests, signals (paper + live order gate), portfolios with tax, F&O / options, auth, WebSocket bars, metrics |
| Regression (findings) | ✅ pass | 36 | 36 | 0 | 0 | 0 | one test per review finding: v2 fixes (CP-01..04, DL-05, DT-01, DB-04, EX-01/02), Phase-1 hotfix regressions of the v1 app |
| Legacy v1 known defects (opt-in) | ✅ pass | 9 | 0 | 0 | 0 | 9 | the v1 rollback app's known defects pinned as strict xfail - 'XFail' here is the expected result, not a failure |
| Browser E2E (Playwright + Chromium) | ✅ pass | 49 | 49 | 0 | 0 | 0 | every page without console errors or CSP violations, charts, scheduler, settings, import/export, setup, Data Health, roll-up source, XSS payloads inert |
| Repository hygiene | ✅ pass | 6 | 6 | 0 | 0 | 0 | no secrets, licence, pinned requirements, no dynamic tables |
| Performance budget | ✅ pass | 1 | 1 | 0 | 0 | 0 | DuckDB ingest >= 100k rows/s |

**JavaScript syntax:** ✅ 21 files OK (node --check)

**Coverage (unit + contract + integration + regression):**

```
app/core (download engine, storage, integrity, scheduler): 92%
app/web + CLI (API v2, pages):                          83%
```

## Slowest tests

| Test | Seconds |
|---|---|
| `integration.test_signals::test_eod_time_travel_equals_the_p3_backtest` | 69.7 |
| `ui.test_backtests_page::test_backtests_page_run_detail_new_run_sweep_and_data` | 49.6 |
| `integration.test_signals::test_intraday_15m_day_equals_the_p3_backtest_and_parity_is_clean` | 35.3 |
| `ui.test_pages::test_every_page_renders_without_errors_or_csp_violations[/watchlists]` | 34.4 |
| `ui.test_p5_p6_pages::test_signals_live_tab_and_login_flow` | 34.2 |
| `ui.test_intel_page::test_intel_page_tabs_quality_events_and_symbol` | 33.7 |
| `unit.test_ratelimit::test_angel_one_windows_respected_at_80_percent_budget` | 31.7 |
| `integration.test_signals::test_paused_deployments_still_exit_but_do_not_enter` | 26.3 |
| `integration.test_signals::test_time_travel_with_the_mock_broker_download_engine` | 25.1 |
| `integration.test_p6_live_ops::test_live_orders_dry_run_then_live_analyzer_check_caps_and_kill_switch` | 23.4 |

## Environment

| Package | Version |
|---|---|
| alembic | 1.20.1 |
| apscheduler | 3.11.3 |
| duckdb | 2.0.0 |
| flask | 3.1.3 |
| httpx | 0.28.1 |
| hypothesis | 6.170.15 |
| numpy | 2.5.4 |
| pandas | 3.1.1 |
| playwright | 1.64.1 |
| pytest | 9.2.0 |
| pytest-timeout | 2.4.0 |
| ruff | 0.17.4 |
| sqlalchemy | 2.1.8 |

Raw JUnit XML per layer: `test-results/junit-*.xml` · logs: `test-results/*.log`
