# Unification U0–U5: status

This is what was built from `UNIFICATION_ANALYSIS.md` §4, using the recommended decisions: ADR-001 execution defaults, IR / hbt Python tier for new strategies, and live trading only through the unified gateway.

![Unified flow](diagrams/unification/unified_flow.png)

| | |
|---|---|
| Commits | `c652316` U0 + U1 · `b437630` U2 · `2d0e59e` U3 · `149dc1a` U4 + U5 · `5dbf13a` gaps closed (ADR-003) |
| Migrations | `0016` unification safety · `0017` one trial registry · `0018` one paper lifecycle · `0019` one orders table. Every existing row is kept. |
| Decisions recorded | `docs/adr/ADR-001-execution-defaults.md` · `docs/adr/ADR-002-one-lifecycle-one-order-path.md` · `docs/adr/ADR-003-gaps-closed.md` |
| Test gate (final tree, after the gap closure) | Python **1,282 passed**, 1 skipped (needs internet) · browser **64 passed** · perf **6 passed** · Rust fmt + clippy `-D warnings` + tests clean (incl. the portfolio test) · ruff clean |
| New tests | 33 test functions in 7 new files (`test_unification_safety`, `test_unification_foundations`, `test_unified_defaults`, `test_unified_backtests`, `test_unified_lifecycle`, `test_unified_ops`, `test_settings_audit`). Also a D3 fault test, a trading-API test and 2 browser tests. |

## 1. Defects D1–D8

| # | Defect | Status | Where / proof |
|---|---|---|---|
| D1 | Duplicate order after a lost acknowledgement when the order book lags (flagfix2) | **Fixed (U0)** | A placement error is now labelled "unsent" or "network". An order whose outcome is unknown is never resent inside `unknown_order_grace_s` (120 s). Live orders are never resent automatically: the order becomes `unknown`, the deployment pauses and a Data Health issue is raised. Test: `test_unification_safety` (X5 rerun → 1 order at the broker). |
| D2 | Peeking user strategies could be frozen and deployed (flagfix2) | **Fixed (U0)** | `app/lab/lookahead.py` runs a corrupt-the-future check at freeze. The look-ahead verdict is required before paper, eligible_live or live, and this cannot be forced. IR strategies are verified by construction. |
| D3 | Protective stops lived only in the engine (flagfix1) | **Fixed (U3)** | On the G2 / broker route, one SL order rests at the broker at the engine's active stop (Rust `Outcome.active_stop`). The engine's own stop exit is not sent again. Test: `test_d3_g2_rests_the_protective_stop_at_the_broker_and_never_exits_twice` (the app "crashes" and the broker stop closes the position; no second exit). |
| D4 | Intraday research used lag 0 s | **Fixed (U0)** | `BacktestService.finality_s()` uses the planner finality; the PIT default follows it. |
| D5 | Trial counts were split across two registries | **Fixed (U2)** | There is one append-only `lab_trials` table (migration 0017; triggers kept). P3 sweeps and walk-forward runs register trials. DSR uses N = trials in the registry before the run + the group. |
| D6 | The P3 pool had no priority or back-pressure; the two dispatchers oversubscribed the CPU | **Fixed (U0, U5)** | `app/lab/compute.py` is one budget for both dispatchers, with low priority and download back-pressure. U5 made it one setting, `lab_workers`. |
| D7 | Options exchange fee before Oct 2024 was understated | **Fixed (U1)** | The cost schedule is generated from the hbt tables (`schedule_from_tables`). |
| D8 | Different execution defaults | **Fixed (U1)** | ADR-001: 15:10, pessimistic, 5 % participation, flagfix2 slippage. `test_unified_defaults` checks both tables. |

## 2. Steps

| Step | Status | Done |
|---|---|---|
| **U0 Safety** | ✅ done | D1, D2, D4, D6, D7; one Telegram setting (finished in U5) |
| **U1 Foundations** | ✅ done | One cost schedule (both kernels); ADR-001; merged corporate-action view; one finality / PIT rule; **one lot / tick history** (`instrument_attrs.merged_history` / `lot_on`, exchange file for the past + broker master after; fixes E15 using today's lot for past days) |
| **U2 One backtest process** | ✅ done | IR strategies in the P3 pipeline. **Rust portfolio mode** (`hbt-core::portfolio`: shared cash, portfolio-relative sizing, `max_positions`; one leg = `hbt.run` bit-identical). **All 8 flagfix2 built-ins ported to IR** (`app/lab/strategies/ir`), with PIT data columns for IR (`app/lab/ir_extras.py`); identical entries against the Python originals. **One execution model** (ADR-003: finality latency, MIS leverage, `cancel_entries`, flagfix2 slippage model in Rust). |
| **U3 One order path** | ✅ done | D3 resting broker stop; one guardrail set; one kill switch; **one physical orders table** (migration 0019: `orders`, single-table inheritance, views for the old names); `/api/v2/trading/orders` reads it; one execution-quality report (hosted fills included) |
| **U4 One paper lifecycle** | ✅ done | Deployments for both kinds; IR on the hbt host with the backtest's execution model and live PIT extras; **hosted parity in the P4 classes** (`ParityCheck`: logic / data_revision / late_data / host_error) |
| **U5 Ops and UI** | ✅ done | One notification bus; one worker budget; settings registry + audit test; **one follow service** (`FeedService.follow` for feeds, hosted deployments and the replay runtime; the WebSocket stream wakes it at bar close); **one trading mock engine** (two calibrations); one test runner |

No open items remain from the unification plan. Deliberate differences between the two kernels (entries only when flat at the decision; MIS orders never survive the session end) are documented in ADR-003 §2.

**Next:** `NEXT_PHASE_HLD.md` (idea intake → evaluation → paper → trade data) - **implemented**; the generator
contract is `IDEAS.md`, the remaining work (Phase 4 live) and the feature audit are `FEATURE_AUDIT.md`.

## 3. Exit criteria from the plan

| Step | Exit test in the plan | Evidence |
|---|---|---|
| U0 | X5 rerun → 1 order; X4 rerun → freeze refused; downloads keep throughput with a sweep running | `test_unification_safety` (duplicate order, look-ahead freeze, no-data verdict); `test_one_worker_budget_for_both_dispatchers` + `test_dispatcher_limits_concurrency_while_downloads_run` (one budget, download back-pressure) |
| U1 | X1/X2 conventions identical; CA migration keeps every row | `test_unified_defaults`, `test_unification_foundations`, golden g36 updated |
| U2 | one registry, one API / UI | `test_unified_backtests` (IR through P3, registry N, deprecation headers) + UI test |
| U3 | 0 duplicate / orphan orders under faults; a crash leaves a resting stop | D3 fault test; existing G2 fault test; `test_one_trading_view_and_one_kill_endpoint` |
| U4 | P4 time-travel day test and the flagfix1 phase-exit test pass on the unified lifecycle | Both original tests are green. `test_u4_an_ir_idea_goes_through_the_one_lifecycle_on_the_hbt_host` checks, inside the lifecycle: parity 100 %, completeness 100 %, 0 duplicate orders, resting protective stop, promotion gates, pause/resume, kill |
| U5 | UI suite; no duplicate settings left | 64 browser tests; `tests/repo/test_settings_audit.py` |

## 4. What this means for the trading engine (ideas → paper → live)

The path an idea takes today:
1. The idea engine writes an **IR strategy** (`POST /api/v2/lab/strategies`). It cannot read the future: the compiler refuses it.
2. **Backtest / sweep / walk-forward** in the one pipeline (`strategy: "ir:<name>"`). Every trial counts in the one registry.
3. **Freeze** (`ir:<name>`). **Deploy** with a universe and book (`POST /api/v2/signals/deployments`). Promote **draft → signals** with the P3 evidence attached.
4. **signals → shadow → paper** on the hbt host. Signals go out by webhook / Telegram, and from the paper stage orders go to the OpenAlgo Analyzer. Gates use the host's journal, parity and fills.
5. **Trade data for analysis**: signals (`/api/v2/signals/...`), the decision journal and fills (`/api/v2/lab/paper/sessions/<id>/...`), the unified orders view (`/api/v2/trading/orders`), nightly parity and daily reports.
6. **Live** is closed for these deployments until phase 4 (the Execution Gateway live route). That phase now builds on one OMS, one guardrail set, resting broker stops and one kill switch.

Next development, in the order of `TRADING_ENGINE_5_PHASES.md`:
- **Phase 2, idea intake** (~12 d): idea API + schema validation, idempotent submission, automatic backtest / WFO and gate report per idea.
- **Rust portfolio mode** (~8–10 d). This should land before multi-symbol ideas are promoted.
- **Phase 4, live** (~18 d): gateway live route on the unified OMS, one orders table, live gate from flagfix2, dry run.
