# Historify - test report

Generated 2026-11-15 20:03:50 · Python 3.14.7 · Linux-6.18.44-fc-v310-x86_64-with-glibc2.39

**Overall: ✅ ALL PASSED** - 827 tests, 818 passed, 0 failed, 0 skipped, 9 expected failures (v1 characterisation), 816 s

| Layer | Result | Tests | Passed | Failed | Skipped | XFail | Covers |
|---|---|---|---|---|---|---|---|
| Static checks | ✅ pass | - | - | - | - | - | ruff (pyflakes, bugbear, imports, pyupgrade) over app, tests, run.py, benchmarks |
| Unit | ✅ pass | 69 | 69 | 0 | 0 | 0 | pure logic: time/IST, calendar (holidays, MCX DST), intervals, rate limiter, error classification, secrets, planner (property tests), schedule triggers |
| Contract (storage) | ✅ pass | 29 | 29 | 0 | 0 | 0 | BarStore contract on DuckDB and SQLite: idempotent writes, upsert, paging, resample, batched export parity, property tests |
| Integration + API E2E | ✅ pass | 155 | 155 | 0 | 0 | 0 | download engine under faults (429/5xx/timeouts/truncation/expired session/restart), scheduler time-travel, API v2, data integrity (ledger, quarantine, revisions, audit, archive, rewrite, backups, maintenance, snapshots), 1m roll-up + roll-up audit, quotes, CLI, legacy v1 import |
| Regression (findings) | ✅ pass | 45 | 36 | 0 | 0 | 9 | one test per review finding: v1 characterisation (strict xfail - the v1 rollback app keeps its defects), v2 fixes (CP-01..04, DL-05, DT-01, DB-04, EX-01/02), Phase-1 hotfix regressions |
| Browser E2E (Playwright + Chromium) | ✅ pass | 43 | 43 | 0 | 0 | 0 | every page without console errors or CSP violations, charts, scheduler, settings, import/export, setup, Data Health, roll-up source, XSS payloads inert |
| Repository hygiene | ✅ pass | 6 | 6 | 0 | 0 | 0 | no secrets, licence, pinned requirements, no dynamic tables |
| Performance budget | ✅ pass | 1 | 1 | 0 | 0 | 0 | DuckDB ingest >= 100k rows/s |
| Lab: Rust crates | ✅ pass | 16 | 16 | 0 | 0 | 0 | cargo fmt --check, clippy -D warnings, hbt-ir (parser, catalogue, compiler, canonical hash) and hbt-core (kernels, evaluator, engine) unit + property tests |
| Lab: engine, IR, integration, paper, research, readiness | ✅ pass | 463 | 463 | 0 | 0 | 0 | golden scenarios + fix pack, oracle parity (E3/E5, TA-Lib, backtesting.py), look-ahead fuzz, IR compiler, Python tier, perf budgets; runs / re-runs / API / CLI / worker processes (1.4); paper host replay equality, OMS stateful test, guardrails, kill switch, kill-9 drills, nightly parity, G2 analyzer route vs the mock (1.5); DSR, walk-forward, E9 noise rejection, registry, cohorts (1.6); readiness checklist + sign-off (1.7) |

**JavaScript syntax:** ✅ 14 files OK (node --check)

**Lab determinism (NFR-10):** ✅ identical across two processes (24 hashes of intents / fills / equity; CI also compares Linux with Windows)

**Coverage (unit + contract + integration + regression):**

```
app/core (download engine, storage, integrity, scheduler): 92%
app/web + CLI (API v2, pages):                          85%
app/lab (Lab integration, paper, research, readiness):  88%
```

## Slowest tests

| Test | Seconds |
|---|---|
| `lab.test_paper::test_drill_kill9_at_every_journal_point` | 58.1 |
| `integration.test_integrity::test_no_double_counting_and_no_missing_bars_under_chaos` | 33.5 |
| `lab.test_paper::test_g2_analyzer_route_reconciles_100_percent_under_faults` | 31.3 |
| `lab.test_lookahead_fuzz::test_generated_programs_are_causal` | 25.0 |
| `lab.test_paper::test_phase_exit_five_sessions_three_strategies` | 21.0 |
| `integration.test_audit2_fixes::test_a2_l3_a_feed_outage_alerts_once_and_recovers_once` | 19.8 |
| `ui.test_pages::test_data_health_tabs_audit_quarantine_snapshot_and_maintenance` | 14.6 |
| `ui.test_pages::test_export_creates_downloadable_file` | 14.0 |
| `contract.test_barstore_contract::test_concurrent_reads_during_writes[sqlite]` | 14.0 |
| `ui.test_pages::test_scheduler_preset_preview_save_and_run_now` | 10.9 |

## Environment

| Package | Version |
|---|---|
| TA-Lib | 0.8.2 |
| alembic | 1.20.1 |
| apscheduler | 3.11.3 |
| backtesting | 0.6.6 |
| cargo | 1.99.1 |
| duckdb | 2.0.0 |
| flask | 3.1.3 |
| hbt-core | 0.2.0 |
| httpx | 0.28.1 |
| hypothesis | 6.170.15 |
| jsonschema | 4.26.0 |
| maturin | 1.16.0 |
| numpy | 2.5.4 |
| pandas | 3.1.1 |
| playwright | 1.64.1 |
| pytest | 9.2.0 |
| pytest-timeout | 2.4.0 |
| ruff | 0.17.4 |
| rustc | 1.99.1 |
| sqlalchemy | 2.1.8 |

Raw JUnit XML per layer: `test-results/junit-*.xml` · logs: `test-results/*.log`


---

## Lab suite by module (`tests/lab`, 463 tests) and Lab browser tests

| Module | Tests | What it proves |
|---|---|---|
| `test_golden_scenarios` / `test_fixpack` | 42 / 29 | hand-computed engine cases per rule; the audit-2 fix pack |
| `test_oracle_parity` / `test_indicators` / `test_backtesting_py_crosscheck` | 14 / 12 / 10 | E3/E5 reference engines, TA-Lib exactness, backtesting.py bracket semantics (every trade identical) |
| `test_lookahead_fuzz` / `test_properties` | 217 / 2 | every operator + generated programs causal (200 in the default run; `DEEP_FUZZ=1` for 2,000); hypothesis invariants |
| `test_ir_compiler` / `test_ir_runs` / `test_python_tier` / `test_data_paths` / `test_cli_lab` / `test_perf_ir` | 34 / 17 / 7 / 3 / 1 / 4 | Strategy IR compiler, runs, Python tier, data loaders, CLI, perf budgets |
| `test_lab_integration` (1.4) | 11 | versions, profiles, runs in thread + process workers, byte-identical re-run, tamper → first difference, data-quality policy, cancel, concurrency limits, lot / tick history, CLI, Lab menu hidden without the core |
| `test_paper` (1.5) | 40 | phase exit (5 sessions × 3 strategies: completeness 100 %, 0 duplicates / orphans, parity 100 %), replay idempotence, planted revision vs evaluator divergence, 19 guardrails one by one, session refusals, blocked entries never leave a position, daily loss → flatten + pause, drawdown → kill, kill + flatten at the shadow quote, drills (hung-worker kill switch, feed stop, auth expiry, kill-9 at 4 journal points), shadow price / depth walk, hypothesis stateful OMS test, nightly job, API + CLI + web guard, G2 analyzer route 100 % reconciliation under faults, mock trading contract, restart reconciliation |
| `test_research` (1.6) | 11 | DSR = published example (0.9004), PSR / E[max] properties, WFO windows never overlap (property), neighbours, `Dataset.window` / `lagged`, E9 noise rejection (4 seeds) + positive control, projects in worker jobs, append-only registry (UPDATE / DELETE refused), DSR counts earlier projects' trials, cohorts (D-18 cut-off, gate verdicts, frozen versions, paper session from a cohort) |
| `test_readiness` (1.7) | 9 | every hard item red on a fresh instance, all green with fixtures → sign-off; each item can turn red again; input validation |
| `tests/ui/test_lab_pages` | 13 | every Lab page under the CSP without JS errors, Lab menu, **1.4 phase-exit E2E** (create → run on snapshot → results with markers → re-run identical → compare), validation pointer, paper kill switch, research verdict + folds, readiness confirmation, Settings "Backtest data & Lab" card, Data Health "Corporate actions" tab |

## Deep look-ahead fuzz

`HBT_FUZZ_EXAMPLES=2000 pytest tests/lab/test_lookahead_fuzz.py`: **217 passed in 269 s**. That is every operator, 2,000 generated programs and the acceptance strategies, with 0 look-ahead violations.

## Performance (informational; `audit3/m7_perf_final.json`, best of 2 runs on this shared 4-vCPU sandbox)

| Metric | Measured | Target |
|---|---|---|
| Sweep, 256 EMA variants × 39,150 5m bars (all cores) | 795 runs/s (m6 after 1.3: 503) | ≥ 3,000 (T1.6.1) - **not met** |
| Evaluator, 32-node program | 106.7 M node-updates/s | ≥ 150 M - **not met** |
| Compile 9,900-node document | 22.5 ms | ≤ 20 ms - close |
| Symbol-major loader, 10 M bars from DuckDB | 1.1 s (best earlier run 0.94 s) | < 0.5 s - re-measure on local disk |
| Single IR run, 39,150 bars | 9.5 ms | - |
| Streaming step, 100 symbols × 32 nodes | 30.5 µs | - |
| Paper host, per member-bar | 22.8 ms | - |

## How to run the tests in the sandbox

```bash
cd /workspace/historify-master

# 1) everything in one command: installs every dependency (see below), runs all layers, writes the report
bash scripts/verify.sh                       # -> test-results/TEST_REPORT.md, exit code 0 only if all passed
#    options: FRESH=1 (new venv)  SKIP_UI=1  SKIP_RUST=1  DEEP_FUZZ=1 (2,000 fuzz programs)  REPEAT=3 (flakiness)

# 2) or: dependencies only, then individual layers
bash scripts/bootstrap.sh                    # idempotent; writes .sandbox-env
source .sandbox-env                          # venv, cargo PATH, HISTORIFY_CHROMIUM, HISTORIFY_SECRET_BACKEND=file
cd historify
ruff check app tests run.py ../benchmarks ../scripts lab
pytest tests/unit tests/contract tests/integration tests/e2e tests/repo -m "not perf" -q
pytest tests -m perf --ignore=tests/lab -q
pytest tests/lab -m "perf or not perf" -q              # Lab: engine, IR, 1.4 integration, 1.5 paper, 1.6 research, 1.7 readiness
pytest tests/ui -q                                      # browser E2E incl. Lab pages (Chromium from .sandbox-env)
(cd rust && cargo fmt --all -- --check && cargo clippy --workspace --all-targets --release -- -D warnings \
          && cargo test -p hbt-ir -p hbt-core --release)
python tests/lab/determinism_hashes.py > /tmp/a.json && python tests/lab/determinism_hashes.py > /tmp/b.json && cmp /tmp/a.json /tmp/b.json
HBT_FUZZ_EXAMPLES=2000 pytest tests/lab/test_lookahead_fuzz.py -q   # deep look-ahead fuzz (weekly in the plan)
pytest tests/lab/test_paper.py -q -k "phase_exit or kill9 or g2"       # 1.5 exit test, kill-9 drills, G2 route
```

What `scripts/bootstrap.sh` downloads and installs (no root, everything under the repo / home folder):

| Component | Source | Verified here |
|---|---|---|
| Python venv + `requirements-dev.txt` (app lock, pytest, hypothesis, ruff, playwright) + `requirements-lab.txt` (maturin, TA-Lib, jsonschema, backtesting.py) | pypi.org | fresh venv on Python 3.14 and on 3.12 |
| Rust 1.99.1 + rustfmt + clippy | rustup-init from static.rust-lang.org (when rustup is missing), crates from index.crates.io | from an empty `CARGO_HOME` / `RUSTUP_HOME` |
| Lab core wheel `hbt-core` | built locally with maturin, installed into the venv | yes |
| Chromium | `playwright install chromium`; fallback: a local Chromium; fallback: `@sparticuz/chromium` from registry.npmjs.org, unpacked with node | Playwright CDN blocked here, so the npm fallback was used |

Raw results: `historify-master/test-results/` (JUnit XML per layer, logs, coverage, determinism hashes).
