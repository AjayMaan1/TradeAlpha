# ADR-003 · Closing the unification gaps: portfolio mode, ported strategies, one execution model, one of everything

| | |
|---|---|
| Status | **Accepted** (closes the open items of `UNIFICATION_STATUS.md` §2) |
| Enforced by | `rust/hbt-core/src/portfolio.rs` (test), `tests/lab/test_kernel_unified.py`, `tests/lab/test_ir_builtins.py`, `tests/lab/test_unified_backtests.py::test_a_multi_symbol_ir_run_is_one_account_the_rust_portfolio_mode`, `tests/lab/test_unified_lifecycle.py::test_u4_a_ported_builtin_with_live_index_data_runs_on_the_host`, `tests/integration/test_unified_ops.py`, `tests/unit/test_unified_defaults.py` |

## 1. Rust portfolio mode (one account for a universe)
- `hbt-core::portfolio`: every instrument runs the normal engine with its own cash **ledger**. All instruments are stepped together on the union of their bar timestamps. Before each bar, an instrument's engine sees the rest of the account: cash, equity, gross exposure, open positions.
- **Shared cash.** Sizing (`all_in`, `pct_equity`, `risk_pct`) is relative to the portfolio's equity. Shorts count the other legs' gross exposure against the leverage limit. `max_positions` refuses a new position (reject `max_positions`).
- **Priority.** Instruments with a bar at the same timestamp are served in universe order. The rule is deterministic; it is the only allocation rule, and it is documented.
- **Default sizing for a multi-symbol IR run.** A plan left on `all_in` becomes equal-weight slots (`pct_equity = 1/max_positions`, where `max_positions = min(ExecConfig.max_positions, instruments)`). Per-intent `size` in the IR overrides this.
- **Hosted paper sessions** use the same rule: equal-weight slots plus `max_open_positions = max_positions` at the OMS (G-PRE-5).
- With one instrument, a portfolio run is bit-identical to `hbt.run` (same decisions hash). Python: `hbt.run_portfolio(program, {symbol: Dataset}, max_positions=...)`.

## 2. One execution model for both kernels (found while porting)
Comparing each Python built-in with its IR port through the same pipeline exposed four divergences that ADR-001 had not covered. All four are resolved in favour of the Python engine's model, applied by `ir_engine.unified_spec()` / `slippage_spec()` in the unified pipeline **and** on the paper host:

| # | Divergence | Before | Decision |
|---|---|---|---|
| E1 | Intraday decision latency | IR runs filled at the next bar's open, with no finality delay. That was a D4-type optimism: one minute early on every intraday entry. | Orders reach the market after `max(order_latency_s, finality)` on the stored 1m bars (`decision_latency_s` + 1m segments, also in portfolio mode). |
| E2 | MIS leverage | hbt default 1× | `ExecConfig.mis_leverage` (5×) |
| E3 | Participation remainder | hbt carried entry remainders into later bars, building positions 4–5× larger than the Python engine. | New rule `cancel_entries`: an entry never exceeds one bar's participation share; an exit is carried until flat. |
| E4 | Slippage | pct approximation | Rust ExecSpec gets the flagfix2 refinements as optional keys (`slippage_min_ticks`, `slippage_edge_minutes`/`_widen`, `slippage_clip`): bucket bps, ≥ 1 tick, ×1.5 in the first and last 15 minutes (daily bars: fills at the open), clipped to the bar. Spec hashes of existing runs are unchanged (the keys are absent unless set). |

Result: `orb_cpr`'s IR port and the Python original make the same 88 entries to the minute, with the same quantities and costs, and final equity within 0.03 %.

Two differences remain **deliberately**:
- **Entry while in a position.** The Python engine queues an entry signalled while a position is open and fills it if the position closes at the next open. hbt (and the paper host) accept entries only when flat at the decision. The latter is the only rule a live route can follow without risking a doubled position.
- **MIS across the session end.** hbt cancels every MIS order at the session end. The Python engine can fill an MIS entry decided on the last bar at the next morning's open.

Targets fill at their limit price in hbt (no slippage on a limit). Intrabar stop-versus-target ambiguity can resolve differently.

## 3. The flagfix2 built-in strategies as IR (`app/lab/strategies/ir/*.json`)
- All eight are ported: `buy_hold`, `trend_allocation`, `rsi2`, `breakout_delivery`, `event_risk`, `results_drift` (daily) and `mtf_pullback`, `orb_cpr` (intraday).
- Same product and the same parameters. String choices become numbered (`source` 0/1, `ma` 0/1/2).
- They are shipped like the Python built-ins: listed as `ir:<name>` (built-in) and registered in the hbt Lab registry on first use. A user strategy of the same name wins.
- **Point-in-time data columns for IR** (`app/lab/ir_extras.py`: `$nifty_close`, `$nifty_above_sma200`, `$nifty_above_ema20`, `$deliv_pct`, `$results_day`, `$days_to_high_impact_event`). They are computed with the same portal functions and `available_at` rules as the Python strategies: from the snapshot in backtests and from the live store on the paper host.
- **Parity with the originals:** identical entries on all daily strategies and parameter sets; orb_cpr identical to the minute; mtf_pullback identical except the documented MIS rule. The Python originals stay for comparison and for your own experiments.

## 4. One lot / tick history
`instrument_attrs.merged_history` / `lot_on` is the only accessor:
- **Lot:** the exchange F&O bhavcopy for every date it covers; the broker master after that.
- **Tick:** the broker master; exchange-only periods take the instrument's known tick.

Datasets, the paper host, snapshots and E15 read it. This also fixes **E15 pricing historical days with today's lot**.

## 5. One physical orders table (migration 0019)
- `orders` (`path` = `lab` | `signals`). `LabOrder` and `PaperOrder` are single-table-inheritance classes with their old attribute names as synonyms.
- Per-path CHECK constraints; one id space; lab orders carry their deployment.
- Existing rows migrate: lab ids are kept (fills still point at them); signals orders are renumbered after them.
- `lab_orders` / `paper_orders` remain as read-only views. `/api/v2/trading/orders` reads the one table.

## 6. One market-data follow service
- `FeedService.follow` is the only live-bar download entry point: persistent feeds, hosted deployments' managed feeds, and the replay runtime's UniverseFollow.
- The WebSocket stream is an optional source that **wakes** the feed loop at a bar close. Tick-built bars are never the record.

## 7. Hosted parity and execution quality in the P4 views
- Nightly parity of a hosted session becomes the deployment's `ParityCheck`, with classes `logic` / `data_revision` / `late_data` / `host_error`. A logic divergence raises a Data Health issue. `timing` cannot occur on a per-bar host.
- The journal reports hosted broker fills' execution quality against the quote mid at send.

## 8. One trading mock engine
`tests/mock_provider/trading.py` is one engine: one order store, books, faults and admin API.
- The two former engines are now **calibrations**: `analyzer` (OpenAlgo sandbox semantics, the default) and `matching` (quote matching, `tests/lab`).
- `sandbox.py` is removed.
