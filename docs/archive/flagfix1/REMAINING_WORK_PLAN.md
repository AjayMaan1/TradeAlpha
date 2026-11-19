# Historify Lab: remaining work, implementation and test plan

> **Status update (after patch `phase-1.4-1.7-final`):** the engineering items of §1.2, §3 (1.4), §4 (1.5), §5 (1.6)
> and §6 (1.7) are implemented and tested, and T2.2 has a first cut. See `PHASE_1_4_TO_1_7_IMPLEMENTATION.md` for
> what was built, the gates (the T1.6.1 performance gates are **not** met yet) and the open items, and
> `TEST_REPORT_FINAL.md` for the test report. Still open: the owner actions (Phase 0, T1.6.3 research, T0 sign-off),
> the first GitHub CI run (§1.1), and Phase 2 onwards.


| | |
|---|---|
| As of | After patch `phase-1.3-strategy-ir` (Phases 1.1, 1.1b, 1.2, fix pack 1.2.1 and 1.3 done) |
| Sources | `docs/lab/ROADMAP_TASKS.md`, `HISTORIFY_BACKTEST_ENGINE_DESIGN.md` (§16–§20), `PHASE_1_3_STRATEGY_IR_DESIGN.md` §20, `AUDIT_2_REPORT.md` |
| Estimates | engineer-days (d), one engineer unless stated; 👤 = owner action |

## 0. Where we are

| Done | Evidence |
|---|---|
| Data readiness (calendar, `available_at`, corporate actions, lot/tick, replica, snapshots v2) | 1.1 tests |
| Hardening (guard, restore, feeds, notifications, disk, purge …) | 1.1b + audit-2 app fixes |
| Rust execution core + fix pack (4 High + 9 Medium of audit 2) | 42 goldens, oracle parity, 29 fix-pack tests |
| Strategy IR v1, 86 operators, evaluator, Python tier, look-ahead harness, CLI | 382 lab tests, 14 Rust tests, 2,000-program deep fuzz |

**Critical path to paper start (T0):**

```
Phase 0 (owner)
  → 1.4 integration
  → 1.5 paper runtime
  → 1.6 research / cohort
  → 1.7 review
  → T0
```

Total remaining engineering to T0: **≈ 56 d** (+ 4 d owner research).
- One engineer: about 13 weeks.
- Two engineers (1.4 ∥ 1.5 after week 2): about 8 weeks.

## 1. Immediate items (before or alongside 1.4)

### 1.1 First CI run of the Lab jobs (A2-L11), 1 d
- **What:**
  - Push the branch.
  - Make `lab-core` green on ubuntu and windows (Rust build, wheel, 382 lab tests).
  - Make `lab-determinism` green (Linux hashes == Windows hashes).
- **How:**
  - Expected Windows issues: path quoting in DuckDB snapshot reads, `os.chmod` on read-only files, TA-Lib wheel availability, float formatting in the determinism JSON.
  - Fix them in the code, never in the test.
- **Test:** the CI jobs themselves. Mark `lab-core` and `lab-determinism` as required checks.

### 1.2 Open audit-2 Lows, 4.5 d
| ID | Implementation | Test |
|---|---|---|
| L2 empty first calendar sync | In `calendar_sync.sync_years`: 0 rows for a year with no stored rows → `raise_issue("calendar_sync_empty")`, do not set `calendar_source`. `auto_calendar_sync` also runs on the first successful `test-connection` / settings save (hook in `services.settings_changed`). | `e_app_evidence.py::test_a2_02` flipped: source stays `sample`, issue raised. Configure-after-start test: sync job enqueued on the first connection. |
| L3 alert storm | Feed-job fingerprint = `feed:{fid}:{status}` with a 1 h throttle and a "N failures since …" summary; the recovery sends one "recovered" info. | Outage simulation: `error_rate=1.0` for 12 bars → 1 notification (+ 1 recovery), not 12. |
| L4 snapshot rotation | `step_lab_snapshot` rotates only after the new snapshot job succeeds (runner callback `on_success`), same pattern as M-11 backups. | Force the build to fail (monkeypatch `_frame`) → old snapshots kept. |
| L5 finality in the manifest | Add `finality_buffers_s {intraday, daily}` to snapshot manifest v2 (and to `Dataset.provenance`, already there for frames). | Manifest field present; `Dataset.from_snapshot` uses it when computing `available_at` for derived frames. |
| L12 engine structure + Rust coverage | Split `Engine::run` into `p0_session`, `p1_open`, `p2_intrabar`, `p3_close`, `p4_mark`; add `cargo-llvm-cov` to CI (report only, no gate at first). | Unchanged goldens (refactor is behaviour-neutral); coverage artefact. |
| L13 feed cap | Check `feed_max_instruments` per tick; above it → feed paused + issue. | Grow the watchlist after creation → paused, issue raised. |
| L14 private store API | `BarStore.arrow_range(iid, interval_id, ts_from, ts_to)` returning an Arrow C-stream object; `bars_from_store` uses it. | Existing data-path tests; a contract test in `tests/integration/test_store_contract.py`. |
| L15 feed latency persistence | Table `feed_bar_latency (feed_id, bar_end, finished_at, status)` in migration 0006; `FeedService._collect` writes rows; the API reads from the table. | Restart Services → latency history survives; p50 / p95 computed from SQL. |
| L16 notification watcher | Add `notified_at` to jobs / issues (migration 0006); scan `WHERE finished AND notified_at IS NULL`. | A job finished while the app was down is notified at start; the lagging-commit case is covered. |

The 9 Lows still open from audit 1 (L-2, L-3, L-5…L-11) are re-triaged against the current code in the same pass; each
one is either closed with a test or moved into a phase below.

### 1.3 Performance gaps carried from 1.3 (planned in 1.6, listed here for tracking)
- Evaluator: 112 M → ≥ 150 M node-updates/s.
- Compile: 10k nodes ≤ 20 ms.
- Sweeps: 603/s → ≥ 3,000/s.

See 1.6 (T1.6.1).

## 2. Phase 0: owner actions (needed before 1.6 research and T0)

| ID | Action | How | Verify |
|---|---|---|---|
| T0.1 👤 | Run the broker probe on 2–3 shortlisted brokers | `python broker_evaluation/broker_data_probe.py --broker … --finality` | probe JSON per broker; `broker_data_compare.py` report |
| T0.2 👤 | Choose the data and execution broker(s); verify tariffs | set `conf = "V"` in `broker_tariffs.toml`, regenerate `lab/hbt/data/brokers.json` (script in §3 T1.4.2) | `verified: true` for the chosen broker |
| T0.3 | Provider profile from measured limits (1 d) | `profiles.py` entry; `historify selftest`, `rollup-audit` | profile test + audit report |
| T0.4 👤 | Lab universe watchlist (NIFTY 100 + indices), `lab_universe_watchlist_id` | Settings | watchlist exists |
| T0.5 | Backfill 1m × 12 months + D × 10 years (0.5 d) | jobs UI / CLI | ledger has no open gaps; integrity audit clean |
| T0.6 | Post-close schedule ON (15:45 incremental 1m + D) | scheduler | first scheduled run succeeded |
| T0.7 | Calendar sync (2 years), NSE corporate-actions CSV (12 months), `replica_enabled` | CLI `calendar-sync`, `ca-import` | no open calendar / CA-candidate issues |
| D-13 👤 | Verify statutory rates (`statutory_nse.json`) against current NSE/SEBI circulars | edit + set `verified: true` | G3 gate (live blocked until then) |

## 3. Phase 1.4: Lab integration in Historify (17.5 d)

**Goal.** Runs become first-class Historify objects:
- saved strategies and versions;
- background runs in worker processes;
- artefacts and manifests;
- re-run and diff;
- REST API and CLI;
- UI pages.

| ID | Implementation | Tests / acceptance | Est |
|---|---|---|---|
| T1.4.2 Schema | Alembic **0006**: `lab_strategies`, `lab_strategy_versions` (ir_json, ir_sha256 UNIQUE, ops_version), `lab_exec_profiles`, `lab_broker_profiles` (seeded from `brokers.json`, `verified` flag), `lab_cost_schedules` (seeded from `statutory_nse.json`), `lab_runs` (manifest_json, status, dataset kind/id, artefact dir), `lab_run_metrics`; plus `feed_bar_latency`, `notified_at` (1.2 above). A script `scripts/gen_broker_profiles.py` regenerates `brokers.json` from the TOML. | migration up/down on an empty DB and on a 0005 DB with data; model ↔ migration autogenerate check produces no diff; seeds present | 1.5 |
| T1.4.3 LabDispatcher | `app/lab/dispatcher.py`: a `multiprocessing` **spawn** pool (Windows-safe), size `min(cores-1, setting)`, below-normal priority. Job kinds `lab_run` / `lab_sweep` through the existing JobService (progress, cancel, pause). Workers read snapshots / the replica only (never the live DuckDB file, D-03). The core gets a cancellation flag (`AtomicBool`, checked every 64k bars) and a progress callback. Backpressure: while a download job runs, the pool is capped at `lab_cpu_share_during_downloads`. | cancel mid-run → status cancelled within 1 s; worker crash → job failed with the traceback, pool healthy; **load test: download throughput with 3 busy workers ≥ 90 % of the idle baseline** (mock provider, 50 series) | 3 |
| T1.4.4 Run orchestration | Dataset resolution: snapshot id / replica (as-of date) / live-quick (small ranges from the store, flagged `reproducible: false`). Run manifest = `hbt.run` manifest + dataset provenance + strategy version id. Artefacts in `instance/lab/runs/<run_id>/`: `manifest.json`, `trades.parquet`, `fills.parquet`, `intents.parquet`, `equity.parquet` (sha256 of each recorded in the manifest). Re-run loads the manifest, checks every hash is available, runs, compares artefact hashes; on a mismatch it shows a structured diff (first diverging intent / fill / equity bar). **Data-quality policy** (ExecSpec `data_quality: warn \| exclude_sessions \| fail`): caveats from the snapshot manifest (incomplete sessions, revisions, CA candidates) → warnings or excluded sessions. **Time-versioned lot / tick** from `instrument_attributes` into the Dataset (per-bar lot / tick arrays, the engine reads them). ExecSpec JSON-schema export. | **byte-identical re-run** (artefact hashes equal); a tampered snapshot → re-run refused with "dataset hash mismatch"; a diff test with a changed ExecSpec shows the first diverging fill; data-quality: an incomplete session under `exclude_sessions` is skipped (golden); lot change mid-range → quantities follow the history (golden) | 3 |
| T1.4.5 API + CLI | `app/web/lab_api.py` blueprint `/api/v2/lab/*`: strategies (CRUD, versions, validate → IR errors with pointer, explain), runs (create → job id, get, trades / fills / equity paging in `{t,o,h,l,c,v}` style, rerun, compare, delete), profiles (exec / broker / costs, GET / PUT). Same error model and web guard as API v2. CLI: `lab strategy add/list`, `lab run --strategy name@v`, `lab rerun`, `lab compare`. | Flask test-client API tests per endpoint (happy path, IR error payload, 404, cross-site refusal via the guard); OpenAPI-style contract snapshot test; CLI tests with CliRunner | 2 |
| T1.4.6 UI (+ T1.1.11) | Pages (Jinja + Alpine CSP, no inline scripts, as the existing v2): **Strategies** (library, JSON editor with live validation that underlines the error pointer, pseudo-code, version diff), **New run** (strategy@version, dataset picker, interval, range, ExecSpec profile with a "semantics" panel, broker profile, capital, params), **Run results** (status, caveats, reproducibility badge, equity + drawdown, trades on the price chart with stops / targets as price lines, metrics grid, monthly heat map, cost breakdown, virtualised trade table with "why this fill"), **Compare** (2–4 runs). T1.1.11: Settings "Backtest data" card, Data Health "Corporate actions" tab + "Record". | **Playwright** (as `tests/ui/test_pages.py`): every page loads under the CSP, validation errors appear at the right field, a run completes end to end against the mock, trade markers are drawn; `node --check` + the no-innerHTML CI gates | 5 |
| T1.4.1 Packaging | optional extra `historify[lab]` (hbt-core wheel); app detects the core and hides Lab pages without it | install without the extra: app starts, Lab menu hidden, `tests/lab` not collected | 1 (with Phase 5 packaging) |

**Phase exit test (1.4):** a Playwright E2E goes from "create strategy" → "run on the weekly snapshot" → "results
page" → "re-run = identical" → "compare with a param change". It runs in the `ui` CI job.

## 4. Phase 1.5: Paper runtime G1 (18 d)

**Goal.** Frozen strategy versions run every bar on the live universe, with no real orders:
- simulated fills under the ExecSpec, with shadow quotes;
- a restart-safe journal;
- guardrails and a kill switch;
- nightly parity with batch replay.

### New test infrastructure (first, 2.5 d)
1. **Mock OpenAlgo, trading side.** Extend `tests/mock_provider/server.py` with the real endpoint names:
   - orders: `placeorder`, `placesmartorder`, `modifyorder`, `cancelorder`, `cancelallorder`, `closeposition`, `orderstatus`;
   - books: `orderbook`, `tradebook`, `positionbook`, `funds`, `margin`;
   - analyzer: `analyzer`, `analyzer/toggle`;
   - quotes: `depth`, `multiquotes`.

   It runs a small matching simulator: market orders fill at the next mock bar's open ± configurable slippage; limit / SL orders rest. Fault injection for rejects, partial fills, latency, auth expiry, a duplicate-ack race and order-book lag. This serves G1 (quotes / depth), G2 (analyzer) and G3 drills.
2. **Session replay harness:** a `FrozenClock` plus a bar feeder that pushes stored bars into the mock at accelerated speed. A full 75-bar session runs in seconds in CI.
3. **Drill harness:** start the app in a subprocess, `kill -9` at a chosen point (e.g. after the journal write, before the HTTP call), restart, then assert the OMS state.

| ID | Implementation | Tests / acceptance | Est |
|---|---|---|---|
| T1.5.2 Strategy host | `app/lab/paper/host.py`: one `MarketEval` per (strategy version, symbol) kept alive across bars, fed by FeedService completions (the 1.1b feed). `IrSource` in streaming mode; the engine in an incremental variant (`Engine::step_bar`, refactor of the P0–P4 loop from L12). Decision journal row per bar: as-seen bar hash (D-25), node values of the rule roots, intents. | **Replay equality**: the session replayed bar by bar through the host gives exactly the intents / fills of `hbt.run` on the same bars (per-bar hash equality) | 2 |
| T1.5.3 Simulated broker | Fill model = ExecSpec with the measured `decision_latency_s`; shadow quotes: at decision time fetch `quotes` (or `depth` above a size threshold) and fill buys at ask / sells at bid (D-24); records implementation shortfall vs the bar-based fill. | unit: spread crossing, depth walk for size, stale-quote fallback to the bar model; integration on the mock with scripted quotes | 2.5 |
| T1.5.4 OMS + journal | Migration 0007: `lab_paper_sessions`, `lab_orders`, `lab_fills`, `lab_guardrail_events`. State machine `intent → validated → sent → acknowledged → (partially_)filled \| rejected \| cancelled`; journal written **before** any broker call; idempotency key per intent (hash of strategy version + bar ts + rule id); on start, open orders are reconciled before anything else. | **hypothesis stateful test** (`RuleBasedStateMachine`) of the OMS with random acks / fills / rejects / restarts: invariants = no duplicate orders per key, position == Σ fills, terminal states absorbing; **kill-9 drill** at 5 points → no duplicates, no lost fills | 3 |
| T1.5.5 Guardrails + kill switch | `app/lab/guardrails.py`: the G-PRE / G-IN / G-SYS set from design §16.3, table-driven (id, check, hard/soft, parameters per strategy), evaluated in the main-process OMS only. Kill switch in the UI, CLI (`historify lab kill [--flatten]`) and API; it works with a hung worker (OMS-side). Data staleness (G-IN-5), NTP offset check (G-SYS-3), daily loss / drawdown. | one table-driven test per guardrail (breach → blocked + event); kill switch with a hung worker (worker `time.sleep`) → orders cancelled within 2 s; staleness drill (feed stopped) → no new entries | 3 |
| T1.5.6 Nightly parity | Maintenance step: batch-replay the day's sessions from the final stored bars, compare with the paper decision journal; report divergences classified as data revision (D-25), evaluator divergence (hard: demote), or timing. | planted revision → classified correctly; identical day → 100 % parity | 1.5 |
| T1.5.7 Paper page + daily report | Sessions, positions, orders, fills, guardrail status, latency, shadow cost; daily report (notification + JSON). | Playwright; report content test | 2.5 |
| 1.2 carry-over | Order types stop-limit, IOC, cancel / replace in the engine (needed by the OMS), with goldens. | goldens G-OT1..6 | 1.5 |

**Phase exit test (1.5):** a 5-session accelerated paper run on the mock with 3 strategies.
- Passes: bar completeness 100 %, 0 duplicate or orphan orders, nightly parity 100 %.
- Drills pass: kill-9, auth expiry, feed stop, kill switch.

## 5. Phase 1.6: Research MVP and first cohort (12 d + 4 d 👤 research)

| ID | Implementation | Tests / acceptance | Est |
|---|---|---|---|
| T1.6.1 Fused sweeps + trial registry | (a) Evaluator speed: compact `repr(C)` kernel structs, inputs by index arrays, specialised `step` for the 10 hottest kernels (no NaN branch once started), SoA across variants for the same kernel type. (b) **Fused metric accumulators**: the engine updates return / drawdown / trade stats in the bar loop (no equity vector for sweeps). (c) Symbol-major multi-instrument Universe loader (one Arrow stream → per-symbol slices, 1.2 carry-over). (d) `lab_trials` append-only table + Parquet bulk. (e) Compile speed: intern labels lazily, hash keys without Vec allocs. | perf gate: **≥ 3,000 intraday backtests/s on 4 cores** (256-variant grid), evaluator ≥ 150 M/s, 10k-node compile ≤ 20 ms, 10 M bars loaded < 0.5 s; **fused metrics == `metrics::compute` on the full equity** (property test); registry append-only (UPDATE/DELETE refused by trigger) | 5 |
| T1.6.2 Validation | Walk-forward (12 m: 6 m IS / 1 m OOS rolling); deflated Sharpe (DSR) with N = the registry's trial count; robustness subset (±1–2 grid steps, costs × 1.5, +1-bar delay). backtesting.py cross-check of bracket semantics (1.2 carry-over, dev-only oracle). | **reproduces the E9 noise rejection** (no-edge data: in-sample winners rejected, DSR ≈ 0); WFO windows never overlap (property); DSR against the published formula examples | 3 |
| T1.6.3 👤 Research | Candidates (5m / 15m intraday, daily / swing) on pre-T0 data only (D-18); cohort frozen (versions hashed) | cohort report generated from the registry | 4 |

## 6. Phase 1.7: T0 readiness review (1 d 👤)

This is a checklist page and CLI (`historify lab readiness`) that evaluates every item automatically where possible:
- data clean;
- replica and weekly snapshot present;
- parity suites green;
- drills passed;
- versions frozen;
- risk limits set;
- broker / statutory verification status.

Owner sign-off starts paper.

**Test:** each checklist item has a fixture in which it fails, and the page shows red.

## 7. Phase 2: paper period (T0 → T0 + 3 months)

| ID | Implementation | Tests / acceptance | Est |
|---|---|---|---|
| T2.1 G1 operation | weekly review: calibrate `decision_latency_s` and the shadow-spread cost into the ExecSpec profile (versioned) | D-19 criteria computed from the journal | ongoing |
| T2.2 G2 analyzer route | `app/lab/broker/openalgo_route.py`: **`analyzer` status checked before every order** (hard block if off); order-update stream or polling; orderbook / tradebook / positionbook reconciliation loop (G-POST-1); restart / auth / kill drills | **100 % reconciliation** on the mock with fault injection (lagging books, partial fills, duplicate acks); drills scripted in CI; a 10-session analyzer run against the real OpenAlgo (manual, logged) | 6 |
| T2.3 Research tools | optuna (TPE, NSGA-II) on the batched objective; CPCV / PBO; full robustness suite (§15.4); Research page; compare runs | PBO / CPCV against reference implementations on synthetic data; E9 in the product (Playwright) | 10 |
| T2.4 Cohorts | monthly additions under the T0 cut-off rule | registry-wide DSR report | ongoing |
| T2.5 WebSocket bars (D-22) | OpenAlgo `feed` (quote / LTP) → local 1m bar builder; nightly parity vs historical bars (revision machinery) | bar builder unit tests (session edges, gaps, late ticks); nightly parity report ≥ 99.x % OHLC match | 5 |
| T2.6 👤 Live prerequisites | static IP, broker app registration, SEBI algo items, tariff + statutory verification | checklist | – |
| T2.7 G3 build | live route (same OMS, `mode = live` behind G-SYS-7); contract-note import + cost reconciliation to the paisa (G-POST-3); slippage-drift monitor (G-POST-2); promotion / demotion workflow (§16.2); hash-chained audit log (G-SYS-6); security review | G3 dry run in the analyzer; tampering with the audit chain detected; contract-note reconciliation goldens from real notes (anonymised); demotion on each trigger | 8 |
| T2.8 👤 Paper report | report against D-19 → owner decision on G3 | – | 1 |

## 8. Phase 3: guarded pilot (G3) → live (G4)

| ID | What | How / tests |
|---|---|---|
| T3.1 | ≥ 20 pilot sessions under hard caps | daily reconciliation job; incident runbook; every guardrail event reviewed |
| T3.2 | Slippage model calibrated from real fills | fit per instrument bucket; re-run backtests with the calibrated ExecSpec; acceptance: median drift ≤ model + 1 tick |
| T3.3 | G4 promotion with stepped caps | caps ×2 at most per 10 clean sessions (enforced in code, tested); auto-demotion tests for each trigger |

## 9. Phase 4: expansion (backlog)

| ID | What | Est |
|---|---|---|
| T4.1 | Portfolio engine: shared cash, sizing methods, constraints, rebalancing, benchmarks (multi-instrument core on the Universe loader) | 20–25 |
| T4.2 | F&O: contracts + expiry calendar as data, margin L1 / L2 (calibrated with OpenAlgo `margin`), options selection + Greeks, STT on exercise, expired-contract data | 30–40 |
| T4.3 | GP strategy generation over the IR (typed subtree crossover / mutation), templates, optional LLM ideation | 15–25 |
| T4.4 | 1m history > 1 year, pre-2020 calendar (T1.1.2), index-membership history (D-07, survivorship) | 5–8 |
| T4.5 | macOS wheels; free-threaded CPython evaluation | 3 |

## 10. Test strategy summary

| Layer | Tools | Where / gate |
|---|---|---|
| Rust unit + property | `cargo test`, proptest | `lab-core` (Linux + Windows) |
| Golden scenarios | hand-computed micro cases per rule | `tests/lab/test_golden_scenarios.py`, `test_fixpack.py` (+ new order-type / data-quality / lot goldens) |
| Oracles / parity | E3 / E5 references, TA-Lib, backtesting.py (1.6), batch vs paper (1.5) | `lab-core`, nightly parity job |
| Look-ahead | `hbt.fuzz` (every operator, generated programs, acceptance strategies, saved versions on save from 1.4) | mandatory; deep run 2,000 programs weekly |
| Determinism | hashes Linux == Windows | `lab-determinism` |
| Stateful / fault injection | hypothesis `RuleBasedStateMachine`; mock OpenAlgo with faults; kill-9 drills | 1.5 / 2.2 suites |
| API / integration | Flask test client + mock OpenAlgo | `test` job |
| UI | Playwright, CSP gates | `ui` job |
| Performance | budgets (3× measured) | `perf` + `lab-core` |
| Security | web guard tests, bandit, pip-audit, gitleaks; audit log tamper test (G3) | existing jobs + 2.7 |

## 11. Sequencing and effort

| Block | Effort | Can start |
|---|---|---|
| 1. Immediate (CI, Lows) | 5.5 d | now |
| 3. Phase 1.4 | 17.5 d | now |
| 4. Phase 1.5 | 18 d | test infrastructure now; host / OMS after T1.4.2 schema |
| 5. Phase 1.6 engineering | 12 d | after 1.4 (registry uses the runs tables) |
| 6. 1.7 review | 1 d | last |
| Phase 0 engineering (T0.3, T0.5–T0.7) | 2.1 d | after the owner's broker choice |
| **Total to T0** | **≈ 56 d** (+ 4 d owner research) | one engineer ≈ 12–13 weeks; two engineers ≈ 8 weeks |
