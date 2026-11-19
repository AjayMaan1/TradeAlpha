# Test report - Phase 4 (live readiness) + Phase 5 pilot tooling

Run in this sandbox (Linux x86_64, 4 cores, Python 3.14.7, Rust 1.99.1, Chromium 1208) on the final code of the
Phase 4 commit (`2313460`). Every command below is a single command run from the `historify-master` folder; each one installs
what it needs first (see "Dependencies").

## 1. Results

| # | What | Command | Result | Numbers | Wall time |
|---|---|---|---|---|---|
| 1 | **Build** (venv + pinned packages, Rust toolchain, Lab core wheel, Chromium) | `bash scripts/bootstrap.sh` | ✅ exit 0 | fresh venv: 68 distributions from PyPI; `hbt_core 0.2.0` wheel built with maturin and installed; Chromium found | 2 m 27 s fresh · **6 s** when up to date (was 2 m 27 s every run) |
| 2 | **Unit tests** | `bash scripts/run_tests.sh unit` | ✅ PASSED | **377 passed**, 1 skipped (needs internet captures) | 26 s |
| 3 | **Regression suite** (contract + integration + findings + repo + lab, all non-UI / non-perf) | `bash scripts/run_tests.sh regression` | ✅ PASSED | **965 passed**, 0 failed | 3 m 52 s – 6 m 30 s (was 8 m 24 s; VM steal varies) |
| 4 | **E2E trading flow** (idea → … → live → reconciliation → demotion) | `bash scripts/run_tests.sh e2e-flow` | ✅ PASSED | **22 / 22 steps** | 59 s (was 1 m 33 s – 2 m 50 s) |
| 5 | Lint (ruff + `node --check` of 23 page scripts) | `bash scripts/run_tests.sh lint` | ✅ PASSED | 0 findings | 5 s |
| 6 | Browser E2E (Playwright + Chromium) | `bash scripts/run_tests.sh ui` | ✅ PASSED | 65 passed | 1 m 39 s |
| 7 | Performance budgets (incl. the new decision-latency budget) | `bash scripts/run_tests.sh perf` | ✅ PASSED | 7 passed; decision p95 0.046 s, p99 0.076 s (budget 0.5 / 1.0 s) | 26 s |
| 8 | Rust crates (fmt, clippy -D warnings, tests) | `bash scripts/run_tests.sh rust` | ✅ PASSED | fmt ✓, clippy ✓, 15 cargo tests passed (verify.sh reports 17 incl. fmt + clippy) | 8 s (cached build) |
| 9 | **Everything, CI gate** (bootstrap + every layer + coverage + determinism + report) | `bash scripts/verify.sh` | ✅ **ALL PASSED** | **1,419 tests: 1,409 passed, 0 failed**, 1 skipped, 9 expected xfail (v1 characterisation); e2e 22/22; determinism identical across two processes | **11 m 45 s** (was 19 m 43 s) |

Expected output of every command, line by line: [VERIFICATION.md](VERIFICATION.md). Speed-up analysis:
[TEST_PERFORMANCE.md](TEST_PERFORMANCE.md).

Coverage (verify.sh): `app/core` 92 % · `app/web` + CLI 81 % · `app/lab` 86 %.
Reports written by the commands: `test-results/TEST_REPORT.md` (verify.sh), `test-results/<layer>.log`,
`test-results/e2e_trading_flow.json` (every step with its evidence), JUnit XML per layer.

## 2. The single commands (copy / paste)

```bash
cd historify-master

bash scripts/bootstrap.sh                 # 1. build: venv (.venv-verify) + pinned packages + Rust core + Chromium
bash scripts/run_tests.sh unit            # 2. unit tests (tests/unit + the Phase 4 unit / service tests)
bash scripts/run_tests.sh regression      # 3. full regression suite (contract, integration, findings, repo, lab)
bash scripts/run_tests.sh e2e-flow        # 4. end-to-end trading flow on the mock OpenAlgo (22 steps)
bash scripts/verify.sh                    # 5. everything (the CI gate) -> test-results/TEST_REPORT.md
```

Optional single layers: `bash scripts/run_tests.sh lint|ui|perf|rust|lab|integration|findings|contract|repo|all`.
The e2e flow also runs stand-alone: `.venv-verify/bin/python scripts/e2e_trading_flow.py --report /tmp/e2e.json`.

### Dependencies (handled by the commands)
- `run_tests.sh` is **self-bootstrapping**: it uses `.venv-verify` (else `.venv`); if that venv is missing, lacks a
  test dependency (pytest, xdist, flask, sqlalchemy, duckdb, pandas, httpx) or - for `unit`, `regression`, `lab`,
  `e2e-flow`, `perf`, `all` - lacks the Lab core `hbt_core`, it runs `scripts/bootstrap.sh` first. `SKIP_BOOTSTRAP=1`
  disables that; `VENV=/path` picks another venv.
- `bootstrap.sh` is idempotent and offline-friendly: when every pinned requirement is already installed it downloads
  nothing; it works with venvs that have no `pip` module (falls back to `ensurepip`, then `uv pip`); it honours a
  preset `HISTORIFY_CHROMIUM`; when the Playwright CDN is blocked it finds a local Chromium (here
  `/opt/pw-browsers/chromium-1208`) or fetches one from npm.
- Needs: bash, Python ≥ 3.12 (or `uv`), network to PyPI and static.rust-lang.org / crates.io on the first run; node
  for `node --check` (optional). Nothing needs a broker: OpenAlgo is mocked (`tests/mock_provider`), NTP is a local
  SNTP responder.

## 3. The e2e trading flow - what each step proves

Two nodes in one process: **research** (`HISTORIFY_ROLE=research`) and **trader** (`HISTORIFY_ROLE=trader`), one mock
OpenAlgo (orders, order/trade/position books, Analyzer toggle, margin, funds), one SNTP responder.

| Step | Proves |
|---|---|
| 0 | both nodes migrate to schema 0021, see the same market data |
| 1 | signed IdeaSpec accepted (202); a tampered spec is refused (401); the trader node refuses ideas (409) |
| 2 | validation + evaluation (backtest, walk-forward, sweep, DSR, PBO, capacity, claimed vs reproduced) → accepted → deployment in `signals` on the per-bar host |
| 3 | the orchestrator promotes signals → shadow on its gate; the research node never promotes into paper (role) |
| 4 | the research node runs the shadow → paper gate and exports a signed bundle (stage paper + gate report) |
| 5 | the trader refuses a modified bundle (400), imports the real one (same deployment id, IR re-compiled, hash checked) and starts it on the OpenAlgo **Analyzer**; a re-import is a no-op |
| 6 | three Analyzer days: orders + fills at the broker, **nightly parity `ok` every day**, **broker reconciliation `ok` every day** |
| 7 | all 8 drills pass on the production route code: kill-9, kill switch, feed stop, auth expiry, **broker down, WS drop, disk full, clock skew** |
| 8 | paper → eligible_live on the trader's own journal (sessions, parity, slippage vs model, loss stops) + drills + sign-off + explicit approval |
| 9 | the live gate lists what is missing (8 criteria) and **cannot be forced** (409) |
| 10 | live readiness set up: user + auth, live switch with the typed phrase and global cap, stepped caps, deployment limits, Analyzer OFF, NTP offset measured (≈ 0.0005 s), tariffs verified |
| 11 | the live gate passes → the first live session is a **dry run** |
| 12 | dry-run day: orders journaled as `DRY-<id>`, **zero orders reach the broker** |
| 13 | after the dry-run session the host watch moves the deployment to the **live route** (caps 250k / 750k / 10 orders, margin required) |
| 14 | live day: orders at the broker with the Analyzer OFF; **margin and funds asked before every entry** |
| 15 | end-of-day reconciliation vs the broker's books `ok`; contract note imported (idempotent) → charges note 395.56 = model 395.56, live fees = note charges |
| 16 | the pilot report (routes paper 4 days / dry run 1 / live 1, P&L net of charges, reconciliation record, verdict); step-up refused until 5 clean sessions |
| 17 | 41 TradeEvents relayed trader → research (HMAC), ingested with their origin, **re-delivery fully deduplicated**, research chain verifies |
| 18 | audit chain verifies; an edited audit row is detected at its id; restored → ok |
| 19 | secret rotation: the relay carries both signatures during the grace window; order-route token rotation keeps the old token valid for the grace |
| 20 | broker rejects beyond `max_rejects_per_day` → **automatic demotion** to eligible_live, paused, live session killed and flattened, notification |
| 21 | after the demotion the live book is flat at the broker and in the OMS; every broker order maps to its journal row |

## 4. New tests in this phase

| File | Tests | Covers |
|---|---|---|
| `tests/lab/test_phase4_live.py` | 22 | live / dry-run routes, margin-unknown, G-SYS-7 live only via the lifecycle, step validation, WS aggregation + partial buckets + REST fallback, parity `data_source`, latency histograms on /metrics, SNTP clock check → G-SYS-3, contract-note parsing, reconciliation ok / differences / no-data / charges warning / live fees / protective-stop parity / earlier-day orders, demotion reasons, audit chain (tamper, delete, truncation vs anchor), secret rotation grace + expiry, API + order-token rotation, ingest (HMAC, exactly once, chain break, own origin), retention anchor, roles, bundle refusals, idea notifications, API token / auth rules |
| `tests/lab/test_e2e_trading_flow.py` | 1 (22 steps) | the whole flow (also run by `run_tests.sh e2e-flow` and `verify.sh`) |
| `tests/lab/test_perf_live.py` | 1 (perf) | decision latency budget of the full-recompute host |
| `tests/lab/test_paper.py` | +1 case | G-PRE-8 (orders per day) in the every-guardrail matrix |
| `tests/lab/test_unified_lifecycle.py` | updated | hosted live is now gated (live drills, NTP, reconciliation) and unforceable |

## 5. Defects the e2e flow found (fixed in this phase, with regression tests)

1. **Nightly parity counted the host's broker-resting stop records as engine decisions** (unification U3/D3): every
   Analyzer / live session whose strategy has a bracket stop showed a `divergence` on its first night - and the host
   watch would have paused the deployment. Fixed in `paper/parity.py` (`HOST_RECORDS`); covered by
   `test_reconciliation_ok_then_differences_then_contract_note` and e2e step 6.
2. **Reconciliation called the session's own earlier-day orders "unknown"** when the broker's book still listed them.
   Fixed (any order of the session is known); covered by the same test.
3. **Gate evidence attributed daily-bar decisions to the previous stage** (bar start vs the decision at the bar's
   end): a shadow stage showed 0 sessions after a day. Fixed in `host_evidence` / `_stage_days` (decision time).
4. `live_step` read the step timestamp (naive UTC) as local time. Fixed.
5. The disk-full drill's fault injection was process-wide; it now affects only the drill's scratch instance.
6. `run_tests.sh ui` ignored `HISTORIFY_CHROMIUM` and silently skipped the browser layer. Fixed.

## 6. Not covered here (needs your broker / calendar)
Real contract notes and tariff verification (D-13), a real OpenAlgo WebSocket session (provisional vs final bar
divergence), SEBI algo obligations, the ≥ 20-session paper calendar per idea and 10 real Analyzer sessions with 100 %
reconciliation (FEATURE_AUDIT L8) - the tooling for each is in place and exercised against the mock.
