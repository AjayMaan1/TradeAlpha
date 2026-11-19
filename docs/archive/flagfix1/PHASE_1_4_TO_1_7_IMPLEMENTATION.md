# Historify Lab: phases 1.4 – 1.7 implementation report

| | |
|---|---|
| Scope | Remaining Phase 1 work from `REMAINING_WORK_PLAN.md`: immediate items (audit-2 Lows), 1.4 integration, 1.5 paper runtime (G1), 1.6 research MVP, 1.7 readiness review; first cut of the Phase 2 G2 analyzer route; sandbox bootstrap + test runner |
| Patch | `changes/phase-1.4-1.7-final.patch` (apply after the four earlier patches) |
| Test report | `TEST_REPORT_FINAL.md` (copy of `historify-master/test-results/TEST_REPORT.md`) |
| Not code | Phase 0 owner actions, the research on real data (T1.6.3), the T0 review / sign-off, the 3-month paper period and everything after it need the owner, a broker and calendar time |

## 1. Status by phase

| Phase | Result |
|---|---|
| Immediate items | Audit-2 Lows L2, L3, L4, L5, L12, L13, L14, L15, L16 fixed with tests (`tests/integration/test_audit2_fixes.py`). L11 (first CI run) needs a push to GitHub. |
| 1.4 Integration | Done: schema (0006), LabService, LabDispatcher (worker processes), runs with Parquet artefacts + manifests, byte-identical re-run with first-difference diff, compare, data-quality policy, time-versioned lot / tick, ExecSpec JSON schema, REST API, CLI, UI (Strategies, Backtests, Run, Compare), Settings "Backtest data & Lab" card and Data Health "Corporate actions" tab (T1.1.11), Lab menu hidden without the core (T1.4.1). Phase-exit Playwright E2E passes. |
| 1.5 Paper runtime G1 | Done: strategy host, decision journal on as-seen bars, simulated broker with shadow quotes, journal-first OMS, 19 guardrails, kill switch (page / API / CLI), nightly parity, daily report, drills, paper page, mock OpenAlgo trading side. Phase-exit test (5 sessions × 3 strategies) passes. Engine-level stop-limit / IOC orders were not added (see §4). |
| 1.6 Research MVP | Done: walk-forward, deflated Sharpe ratio (reproduces the published example), robustness subset, append-only trial registry, research projects as worker jobs, cohorts with the D-18 cut-off, E9 noise rejection, backtesting.py cross-check, Research page. Performance gates are only partly met (see §3). The research itself (T1.6.3) is the owner's. |
| 1.7 Readiness | Done: 16-item checklist evaluated from the instance, owner confirmations, risk limits, sign-off (refused until ready), page + CLI. The review and sign-off are the owner's. |
| Phase 2 (partial) | G2 analyzer route, first cut: analyzer check before every order, no blind resend, book reconciliation, position check; 100 % reconciliation on the mock under faults. Open: order-update stream, a 10-session run against a real OpenAlgo. |

## 2. What was built

### 2.1 Phase 1.4: the Lab inside Historify

- **Schema** (`app/core/meta/lab_models.py`, migrations 0006–0008). 0006: strategies, immutable versions (unique content hash), execution / broker / cost profiles, runs, run metrics, `feed_bar_latency`, `notified_at`. 0007: paper sessions, members, as-seen bars, decisions, orders, fills, guardrail events, parity reports, drills. 0008: research projects, trials (append-only through SQLite triggers), cohorts, sign-offs. A test checks that the models and the migrations describe the same schema (autogenerate diff empty) and that 0005 → 0008 → 0005 round-trips.
- **LabService** (`app/lab/service.py`). Strategies and versions (deduplicated by hash, frozen once they trade on paper or join a cohort), profiles seeded from the bundled tariffs and statutory schedules (`verified = false`, D-13), run creation / preparation / completion, artefacts, series for the charts, re-run with an identity check, compare, delete.
- **LabDispatcher** (`app/lab/dispatcher.py`). Lab jobs (`lab_run`, `lab_sweep`, `lab_research`) go through the existing JobService. Each job runs in a spawned worker process at below-normal priority, or in a thread on small machines. Limits: `lab_workers`, and fewer workers while a download runs. Cancel terminates the worker. Jobs are restart-safe.
- **Execution** (`app/lab/execution.py`). Workers receive a resolved Dataset and never open the app databases (D-03). They write trades, fills, intents and equity as Parquet through DuckDB, each with a file hash and a content hash.
- **Datasets** (`app/lab/datasets.py`). Sources are a snapshot (reproducible), the replica, or the live store (flagged "not reproducible"). The data-quality policy is `warn | exclude_sessions | fail`.
- **API** `/api/v2/lab/*` (`app/web/lab_api.py`), **CLI** `flask historify lab …`, and **UI** pages (Alpine CSP, no inline script, no `innerHTML`).

### 2.2 Phase 1.5: paper runtime G1 (`app/lab/paper/`)

**Strategy host** (`service.py`). When a completed bar arrives (store poll by the paper thread, or the replay harness):

1. The bar is stored in `lab_paper_bars` exactly as it was seen.
2. The frozen strategy version runs on the member's as-seen bars with the same engine and ExecSpec as a backtest.
3. One transaction journals everything for that bar:
   - the decision: the as-seen dataset hash, the intents of the bar, and the fills the engine made in it;
   - fills of pending orders;
   - cancels of unfilled market orders;
   - new orders in state `intent`.
4. Only then are the orders driven through guardrails and the broker.

The core is causal (look-ahead harness, phase 1.3), so a run on bars `0..t` reproduces bar by bar what a longer run does. Replay equality therefore holds by construction, and the nightly parity checks it every day. Cost: 23–28 ms per member-bar (warm-up of 5 sessions on 5m), which is about 2.5 s per 5m bar close for 100 members. This is fine for G1. An incremental `Engine::step_bar` would be needed for 1m × hundreds of symbols (§4).

**OMS** (`oms.py`):
- **Journal-first state machine.** `intent → validated → sent → acknowledged → partially_filled → filled`, with `blocked`, `rejected` and `cancelled` as terminal states. Every transition is a compare-and-set (`UPDATE … WHERE status IN …`). The kill switch (another thread or another process) and the host can race without overwriting each other, and terminal states are absorbing.
- **Idempotency key.** `session:member:bar:what:index` is UNIQUE.
- **Before a broker call.** The session status is re-read, and the order is journaled as `sent` first.
- **Lost acknowledgements.** A lost ack is never resent blindly.
- **On restart.** `reconcile()` resumes every open order.
- **OMS vs engine book.** The OMS book may hold less than the engine (a blocked entry). Engine exits are clipped to the OMS position, and an orphan guard flattens any OMS position the engine no longer holds.

**Simulated broker and shadow fills.** A quote is captured when an order is sent. The shadow fill is at the ask for buys and the bid for sells, walking the depth for size. A stale quote falls back to the bar model. The implementation shortfall is reported.

**Guardrails** (`guardrails.py`). Table-driven, following design §16.3:

| Group | IDs |
|---|---|
| G-SYS | 1 kill, 2 heartbeat, 3 clock, 5 broker health |
| G-PRE | 1 whitelist, 2 notional / lots, 3 price collar, 4 fat finger, 5 positions / exposure, 6 margin, 7 order rate, 9 trading window |
| G-IN | 1 protective stop, 4p paused member, 5 staleness, 5b bad tick, 2 daily loss → flatten + pause, 3 drawdown → kill + flatten, 4 consecutive losses → pause |

Session creation is refused for:
- no protective stop (G-IN-1);
- square-off after 15:10 (G-IN-6);
- live mode (G-SYS-7);
- `fill_timing = close`;
- the 1m magnifier.

**Kill switch.** Available from the page (KILL ALL, flatten checkbox), `POST /api/v2/lab/paper/kill` and `flask historify lab kill [--flatten]`. It touches only the database, so it works while a strategy evaluation is hung. The drill measures it below 2 s.

**Nightly parity** (`parity.py`). The day is replayed twice:
- on the bars the session saw: this must equal the journal, otherwise it is a divergence and the member is demoted (G-PAR);
- on the final stored bars: differences are classified as "revision" only when a revised, missing or vanished bar lies at or before the decision.

It runs after `lab_parity_at` (default 17:30 IST), with a 7-day catch-up. The daily report (completeness, orders, fills, fees, shadow cost, latency, guardrail events, parity) is saved as JSON and sent as a notification.

**Drills** (`drills.py`, `flask historify lab drills run`, Readiness page). They run in scratch instances on synthetic data and record their results in `lab_drills`:
- **kill-9**: SIGKILL at 4 journal points, then a restart. The result equals the uninterrupted reference.
- **kill switch**: a hung worker; the switch takes under 2 s and nothing is sent after the worker wakes.
- **feed stop**: a stale alert is raised, and late bars cannot open positions.
- **auth expiry**: paper trading continues without shadow quotes, and the G2 route hard-refuses.

**Mock OpenAlgo, trading side** (`tests/mock_provider/trading.py`):
- The real endpoint names and shapes: orders, books, funds, margin, analyzer, depth, multiquotes.
- A matching simulator.
- Fault injection: rejects, partial fills, latency, lost acks, duplicate book rows, book lag, auth expiry.

**G2 route** (`openalgo_route.py`).
- Analyzer mode is checked before every order. If it is off, the order is refused.
- A placeorder call is never retried.
- Lost acks are found in the order book by strategy tag.
- Fills come only from the trade book (deduplicated).
- The position book is compared with the OMS (G-POST-1).
- A kill asks the broker to cancel, and the book sync applies the broker's final state.

### 2.3 Phase 1.6: research MVP

- **Library** (`lab/hbt/research.py`, no Historify imports):
  - `wfo_windows` (month-aligned, disjoint and contiguous OOS) and `walk_forward` (IS sweep → best by objective with a minimum number of trades → OOS run; OOS returns concatenated).
  - `deflated_sharpe` (Bailey & López de Prado 2014, own inverse normal). It reproduces the paper's example: DSR 0.9004, SR0 0.1132.
  - `robustness`: grid neighbours ±1/±2 steps, costs × 1.5 (every rate and fixed charge), one bar more latency via `Dataset.lagged`, which works on any interval.
  - `evaluate`: full-period sweep + WFO + DSR of the in-sample winner with N = all trials in the registry (this project's included) + robustness + verdict against the gates.
  - `Dataset.window` / `Dataset.lagged` added.
- **App** (`app/lab/research.py`):
  - Projects run as `lab_research` worker jobs.
  - Every evaluated variant is appended to `lab_trials`; UPDATE and DELETE are refused by triggers. Trials are also written to Parquet per project.
  - Cohorts freeze accepted versions and the selected parameters. D-18 is enforced: the research data must end before the cut-off. A paper session can be created from a cohort.
- **E9 noise rejection.** On random walks (4 seeds), the in-sample winner has Sharpe ≈ 1, but its DSR is 0.37 and its OOS Sharpe is below zero, so it is rejected. A momentum market is the positive control and is accepted (DSR > 0.95, OOS Sharpe > 1).
- **backtesting.py cross-check** (dev-only oracle, AGPL). On 10 seed × parameter cases, every trade's entry and exit time and price is identical. One semantic difference is documented and handled in the oracle: IR `cross_above` is `prev <=`, while backtesting.py's `crossover` needs `prev <`.

### 2.4 Phase 1.7: readiness

`app/lab/readiness.py`, `GET /api/v2/lab/readiness`, Readiness page, `flask historify lab readiness` (exit code 1 until ready; `--signoff NAME`).

Items (★ = hard):
- ★ data clean
- ★ universe watchlist
- ★ calendar synced
- ★ replica fresh
- ★ weekly snapshot
- ★ engine self-test (live == batch, deterministic)
- paper parity
- ★ drills (30 days)
- ★ cohort
- ★ frozen versions
- ★ risk limits
- ★ broker verified
- statutory verified
- ★ 3 owner confirmations: NTP clock, OpenAlgo session, costs reviewed

The sign-off stores the checklist it was given. Every hard item has a test fixture in which it fails.

### 2.5 Tooling

- **`scripts/bootstrap.sh`** downloads everything into the repository folder, with no root needed:
  - the venv with `requirements-dev.txt` and the new `requirements-lab.txt` (maturin, TA-Lib, jsonschema, backtesting.py);
  - the pinned Rust toolchain via rustup-init from static.rust-lang.org;
  - the hbt-core wheel, built and installed;
  - Chromium from the Playwright CDN, or a local one, or `@sparticuz/chromium` from the npm registry (brotli-unpacked with node).

  It writes `.sandbox-env`. It was verified from scratch here: an empty `CARGO_HOME` / `RUSTUP_HOME`, Python 3.12, and a fresh venv on 3.14.
- **`scripts/verify.sh`** (extended) calls the bootstrap, then runs these layers: lint, unit, contract, integration, regression, UI (incl. Lab pages), repo, perf, **Rust** (fmt, clippy, cargo test → JUnit via `scripts/cargo_junit.py`), **Lab** (`tests/lab`), and **determinism** (hashes from two processes). It writes `test-results/TEST_REPORT.md`.
- **CI**: `lab-core` installs `requirements-lab.txt` and runs the Lab browser tests on Linux.

## 3. Gates and measurements

All performance numbers are best-of-N on this sandbox (4 vCPU shared, timings vary ±40 % between runs): `audit3/m7_perf_final.json`.

| Gate | Target | Measured | Status |
|---|---|---|---|
| Byte-identical re-run | identical | identical (thread and process workers) | ✅ |
| Paper replay equality / parity | 100 % | 100 % (5 sessions × 3 strategies, 1,125 decisions) | ✅ |
| Duplicate / orphan orders | 0 | 0 (phase exit, kill-9 at 4 points, stateful test) | ✅ |
| Kill switch with a hung worker | < 2 s | ~0.01 s, nothing sent after wake-up | ✅ |
| G2 reconciliation under faults (mock) | 100 % | 100 % | ✅ |
| DSR vs the published formula example | 0.9004 | 0.9004 | ✅ |
| E9 noise rejection | rejected | rejected on 4 seeds (DSR 0.37); positive control accepted | ✅ |
| Sweeps | ≥ 3,000 intraday backtests/s on 4 cores | best 795 runs/s (runs: 471–800) of 39,150-bar 5m backtests, all cores (m6 after 1.3: 503) | ❌ open |
| Evaluator | ≥ 150 M node-updates/s | 106.7 M (m6: 52.1 with the same script) | ❌ open |
| Compile 10k nodes | ≤ 20 ms | 22.5 ms for 9,900 nodes (4,000 nodes: 16.4 ms) | ❌ close |
| Load 10 M bars | < 0.5 s | 0.94–1.5 s incl. the DuckDB scan (one outlier 3.1 s; shared disk) | ❌ re-measure on local disk |
| Paper host | – | 22.8 ms per member-bar (5 warm-up sessions, 5m) | info |

## 4. Deviations and open items

1. **Performance gates of T1.6.1 are not met** (table above). What was delivered: fused metric accumulators, the symbol-major loader and bounded labels. The compact `repr(C)` kernel layout and specialised hot kernels were not started. Sweeps are still fast enough for the MVP research grids: a 36-variant grid with 40 walk-forward folds on 1,000 daily bars evaluates in under 1 s. The gate matters for large intraday grids.
2. **The paper host recomputes the as-seen series on every bar** instead of an incremental `Engine::step_bar`. It is correct by construction (causality) and checked nightly. The cost is linear in the warm-up length.
3. **Engine order types stop-limit / IOC / cancel-replace** (1.2 carry-over) are not in the engine. The OMS models LIMIT / SL orders and cancels, and the G2 route sends engine-managed exits as MARKET orders.
4. **G2** polls the books; the order-update stream and the 10-session run against a real OpenAlgo analyzer remain.
5. **`historify[lab]` pip extra**: it ships with Phase 5 packaging. The app already detects the core.
6. **The first GitHub CI run (A2-L11)** needs a push; Windows-specific fixes may follow.

## 5. What is left

| Who | What |
|---|---|
| 👤 Owner (Phase 0) | broker probe on the shortlist → choose the broker and verify tariffs; Lab universe watchlist; backfill 1m × 12 months + D × 10 years; post-close schedule; calendar sync + corporate-actions CSV; verify statutory rates |
| Engineering (small) | provider profile from measured limits (T0.3); first CI run (Linux + Windows) |
| 👤 Research (T1.6.3) | candidates on pre-T0 data with the Research page → freeze the first cohort |
| 👤 T0 review (1.7) | drills on the real machine (`lab drills run`), risk limits, confirmations, sign-off → paper starts |
| Phase 2 | paper operation and calibration (D-19), G2 completion, research tools (optuna, CPCV / PBO), WebSocket bars, G3 build; performance work from §4 |
