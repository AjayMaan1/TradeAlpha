# Verification guide - one command per check, and exactly what a reviewer should see

Run everything from the `historify-master` folder with bash. Each command installs what it needs first
(`run_tests.sh` calls `scripts/bootstrap.sh` when the venv, a test dependency or the Lab core is missing), prints a
summary block and ends with a `RESULT:` line; the shell exit code is the verdict. Append `; echo "exit code: $?"` to
see it (do not pipe the command into `tail` - that would print tail's exit code).

| # | Check | Command | Reviewer should see (last lines) | Exit code |
|---|---|---|---|---|
| 1 | Build: venv + pinned packages + Rust core wheel + Chromium | `bash scripts/bootstrap.sh; echo "exit code: $?"` | `==> Lab core: up to date ...` (or `building ...` on the first run) · `hbt_core 0.2.0 - ops 1` · `summary: venv ... · hbt_core 0.2.0 · Chromium ...` · **`RESULT: build OK - exit code 0`** | **0** |
| 2 | Unit tests (tests/unit + the Phase 4 unit / service tests) | `bash scripts/run_tests.sh unit; echo "exit code: $?"` | summary row `unit  PASSED  377 passed, 1 skipped` · `PASSED: unit` · **`RESULT: PASSED - exit code 0`** | **0** |
| 3 | Regression suite (contract, integration, findings, repo, lab incl. the e2e flow test) | `bash scripts/run_tests.sh regression; echo "exit code: $?"` | summary row `regression  PASSED  965 passed` · `PASSED: regression` · **`RESULT: PASSED - exit code 0`** | **0** |
| 4 | End-to-end trading flow (mock OpenAlgo, research + trader nodes) | `bash scripts/run_tests.sh e2e-flow; echo "exit code: $?"` | 22 lines `[PASS] 0. ...` … `[PASS] 21. ...` · `E2E trading flow: 22/22 steps passed in … s -> OK` · summary row `e2e-flow  PASSED  22/22 steps passed -> OK` · **`RESULT: PASSED - exit code 0`** | **0** |
| 5 | Lint (ruff + `node --check` of every page script) | `bash scripts/run_tests.sh lint; echo "exit code: $?"` | `All checks passed!` · summary row `lint  PASSED  ruff: all checks passed; node --check: 23 files OK` · **`RESULT: PASSED - exit code 0`** | **0** |
| 6 | Browser E2E (Playwright + Chromium) | `bash scripts/run_tests.sh ui; echo "exit code: $?"` | summary row `ui  PASSED  65 passed` · **`RESULT: PASSED - exit code 0`** (a machine without a usable Chromium shows `ui  SKIPPED`) | **0** |
| 7 | Performance budgets (serial) | `bash scripts/run_tests.sh perf; echo "exit code: $?"` | summary row `perf  PASSED  7 passed, ... deselected` · **`RESULT: PASSED - exit code 0`** | **0** |
| 8 | Rust crates (fmt, clippy -D warnings, cargo tests) | `bash scripts/run_tests.sh rust; echo "exit code: $?"` | summary row `rust  PASSED  fmt ok, clippy ok, 15 cargo tests passed` · **`RESULT: PASSED - exit code 0`** | **0** |
| 9 | Everything (the CI gate: bootstrap, every layer, coverage, determinism, report) | `bash scripts/verify.sh; echo "exit code: $?"` | the layer table with every row `✅ pass` (Static checks, Unit, Contract, Integration + API E2E, Regression (findings), Legacy v1 known defects - 9 XFail = expected, Browser E2E, Repository hygiene, Performance budget, Lab: Rust crates, Lab: engine ..., Lab: performance budgets) · `## E2E trading flow ... ✅ 22/22 steps` · `**Overall: ✅ ALL PASSED** - 1419 tests, 1409 passed, 0 failed, 1 skipped, 9 expected failures` · **`RESULT: ALL PASSED - exit code 0`** | **0** |
| 10 | Where the time goes (after 2-9) | `python3 scripts/test_timings.py` | per layer: test count, summed test time, the slowest files and tests | 0 |

Every `run_tests.sh` layer prints this block at the end (example: `bash scripts/run_tests.sh unit`):

```
================================ SUMMARY (unit) ================================
  layer        result   tests                                                          wall
  unit         PASSED   377 passed, 1 skipped                                          26 s

PASSED: unit
RESULT: PASSED - exit code 0
```

`bash scripts/run_tests.sh all` runs lint, unit, contract, integration, findings, legacy, repo, perf, rust, lab,
e2e-flow and ui in one go and prints one summary row per layer.

## Counts explained
- `unit`: 377 = 355 in tests/unit + 22 Phase 4 tests (`tests/lab/test_phase4_live.py`); the 1 skip needs captured
  live intel pages (internet) - expected.
- `regression`: 965 = contract 29 + integration 345 + findings 36 + repo 11 + lab 544 (non-perf; includes the e2e
  flow test and the Phase 4 tests).
- `verify.sh`: 1,419 test cases across all layers; the 9 "expected failures" are the v1 rollback app's known defects
  pinned as strict xfail (reported as XFail - the expected result).

## Artefacts a reviewer can open
`test-results/<layer>.log` (full pytest output), `test-results/junit-<layer>.xml`, `test-results/e2e_trading_flow.json`
(every e2e step with its evidence), `test-results/TEST_REPORT.md` (verify.sh), `docs/TEST_PERFORMANCE.md`
(bottleneck analysis).

## If something fails
The summary row shows `FAILED` with pytest's count line and the command exits 1. The failing test names are at the end
of `test-results/<layer>.log` (`FAILED tests/...`). Re-run one test with
`cd historify && ../.venv-verify/bin/python -m pytest "tests/...::test_name" -q`.
