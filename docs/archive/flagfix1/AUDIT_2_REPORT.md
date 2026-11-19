# Historify: audit 2 (after Phase 1.1, 1.1b and 1.2)

| | |
|---|---|
| Scope | Whole working tree on top of baseline `1ccb27b` + patches 1.1 / 1.1b / 1.2. Focus on what changed since audit 1: the Rust core `hbt-core` + `hbt_core` bindings + `lab/hbt`, the 1.1 data-readiness code (calendar sync, `available_at`, corporate actions, snapshots v2, replica) and the 1.1b fixes (guard, restore, feeds, notifications, disk guard, purge). |
| Method | Line-by-line review of the new code, then **every finding reproduced by a script** (no finding is reported from reading alone unless marked "by inspection"). Regression: full suite 345 passed / 0 failed, `app/core` coverage 92 %, cargo test + clippy `-D warnings` clean, ruff clean, bandit high = 0. |
| Evidence | `/workspace/audit2/e_core_evidence.py` → `e_core_evidence.json` (core findings X-01…X-13), `/workspace/audit2/e_app_evidence.py` (app findings; pytest, asserts the defect). |
| Status (after patch `phase-1.3-strategy-ir`) | **All 4 High and 9 Medium fixed**; Lows L1, L6, L7, L8, L9, L10 fixed on the way; L2–L5 and L11–L16 open. Regression tests: `tests/lab/test_fixpack.py` (29), `tests/integration/test_audit2_fixes.py` (2); evidence re-run: `audit2/e_core_after.json`. |
| Result | **0 Critical · 4 High · 9 Medium · 16 Low**, plus 2 tracked performance gaps. The audit-1 Critical/High/Medium fixes re-verified (tests green, attack scripts still blocked). The 9 Low items left open from audit 1 are unchanged and not repeated here. |

Severity scale: **Critical** = security breach, data loss or real-money loss now. **High** = silently wrong research results in
a default or common configuration, or a blocker for an approved decision. **Medium** = wrong results or an operational failure
in a specific but realistic configuration, or a missing safety check. **Low** = edge case, provenance gap, hygiene or docs.

## High

| ID | Area | Finding | Evidence (reproduced) | Impact | Fix | Est |
|---|---|---|---|---|---|---|
| **A2-H1** | core / ExecSpec defaults | **On daily bars the default ExecSpec rejects every entry.** The intraday session rules (`no_new_entries_after` 14:55, MIS square-off 15:10) are applied to daily bars, whose "close minute" is 24:00. The result is 0 trades and one `entry_cutoff` reject per signal, **with no warning**. The default product is also MIS, so even with the cutoff removed, a daily run would force-close every position at the next open. | X-01: `fills 0, rejects {entry_cutoff: 1}, warnings []` for `exec_spec(product="CNC")` on D bars | Every daily/swing strategy (D-17: 10 years of D data) shows "no trades" under the defaults. Users conclude the strategy does nothing. | Session rules apply only to intraday bars. The default product follows the bar size (CNC for ≥ 1 session). Refuse MIS on daily bars with a clear error. Warn when > 50 % of signals are rejected. | 0.5 d |
| **A2-H2** | core / orders | **Limit and stop entries never fill on daily bars.** A DAY order is stamped with the *signal* day and cancelled at P0 of the next session, i.e. before it ever becomes live. | X-02: limit at 101.00, next-day low 101.00 → `fills 0, rejects {expired: 1}` | Every daily strategy with a limit or stop entry (breakouts, pull-backs) has zero trades. | DAY validity starts in the session where the order becomes live (activation). Expiry counts live bars. | 0.5 d |
| **A2-H3** | core / partial fills | **A carried entry remainder re-opens a position after the strategy exits, and turns a reversal into the old direction.** When the participation cap splits an entry, the remainder is queued as a market entry. An exit signal on the next bar closes the filled part, then the remainder fills, so the strategy is long again against its own exit. A short-entry (reversal) signal is dropped because a pending entry already exists. The 10 % intraday cap is the default since 1.2 (D-09), so this triggers whenever an order exceeds 10 % of a bar's volume. | X-03: exit signal → fills `entry 30, signal-exit 30, entry 20`, final position **+20** (expected 0). X-03b: short-entry signal → final position **+20 long** (expected short) | Wrong trades and P&L for illiquid names or larger capital. No invariant in the property tests catches it. | Exit and reversal signals cancel any carried entry remainder, and a reversal queues its own entry. New property: "no entry fill without an entry signal since the last exit". | 1 d |
| **A2-H4** | core × corporate actions | **The core cannot handle corporate actions in either mode.** (a) *Adjusted* prices (D-06 / §8.4 default for equity signals) are refused: "not a multiple of 1/100 rupee". (b) *Raw* prices have no position adjustment on the ex-date (§8.4 says CA events apply to positions), so a bonus or split looks like a crash. | X-04: adjusted frame refused. X-04b: long through a 1:1 bonus → equity 100,592 → **50,500** (true change ≈ +0.6 %) | Any daily strategy held across a split or bonus (common in NIFTY 100 over 10 years) shows fake losses, fake stop-outs and fake signals. Adjusted research, the approved default, is impossible. | Signals on **adjusted f64** columns, execution on **raw i64** bars (design §8.4). The core applies CA events to positions (qty × ratio, levels × factor, cash in lieu, optional dividend credit). Covered in the Phase 1.3 design §9. | 3 d |

## Medium

| ID | Area | Finding | Evidence | Impact | Fix | Est |
|---|---|---|---|---|---|---|
| A2-M1 | core / liquidity | Zero-volume bars fill whenever no participation cap is set (the daily default is "no cap"), despite `zero_volume_fills = false`. | X-05: market entry filled on a bar with volume 0 | Fills on suspended or illiquid sessions | Zero-volume rule independent of the cap | 0.25 d |
| A2-M2 | core / MIS | A forced MIS close at the next session (no square-off bar in the data) that is partially filled **clears its own carried remainder**, so the MIS position stays open overnight indefinitely. | X-06: 100 long, forced exit fills 30, final MIS position 70 | Overnight MIS exposure in the backtest; wrong P&L | Keep the carry (like the square-off path does) | 0.25 d |
| A2-M3 | core / inputs | **Inputs are not validated.** (a) NaN prices become INT64_MIN (frame path) or 0 (Arrow path). A NaN low triggers every long stop. (b) `ts` as datetime64 (ns/ms) is accepted, so every bar becomes its own session. (c) NaN in a float signal array counts as *True*. | X-07: frame low −9.2e18, Arrow low 0, stop filled. X-08: day numbers 2.0e10 apart per bar. X-10: 1 fill from a NaN signal | Silently wrong results from external data (`bars_from_frame` is the public entry point) | Refuse non-finite prices and NULL volume. Require epoch seconds in [2000, 2100]. Signals must be bool, or numeric without NaN. | 0.5 d |
| A2-M4 | core / config | **ExecSpec / OrderPlan values are not range-checked.** Negative stop or target percentages exit on the entry bar. `squareoff_at "25:99"` and 300 % slippage are accepted. | X-13: `stop_pct=-0.05` → entry and stop at 102.00 on the same bar; slippage 300 % → buy at 408.00 | The IR compiler (1.3) and the UI (1.4) will generate these objects; typos become silent nonsense | `validate()` for every field (ranges, HH:MM, enum combinations), in Rust and mirrored in Python | 0.5 d |
| A2-M5 | core / provenance | **The run manifest does not identify the data or the signals.** Two runs on different bars with different signals produce identical manifests. | X-11: `identical_manifests_for_different_runs: true` | §17 "byte-identical re-run" and D-25 cannot work. Runs cannot be compared or cached. | Dataset hash (core arrays + CA events) and signals/intents hash. In 1.3: IR hash + params. | 0.5 d |
| A2-M6 | core / metrics | **W / M annualisation is wrong.** `periods_per_year` treats bar seconds as calendar time: W = 35.4 (should be ≈ 49.6 on 248 sessions), M = 8.3 (should be 12). Sharpe and volatility of weekly runs are understated by 15 %, monthly by 17 %. | X-09 | Wrong ranking of W/M strategies | Periods from the interval code (W: sessions/5, M: 12) or counted from the calendar | 0.25 d |
| A2-M7 | costs | **The default cost model has no brokerage and no DP charge.** `backtest()` defaults to statutory charges only (brokerage `{}` = 0). Delivery sells also lack the DP charge (≈ ₹13.5–15.9 + GST per scrip per day), about 0.15 % on a ₹10k ticket. | by inspection (`hbt/costs.py`); `statutory_nse.json` has no DP component | Optimistic results by default, especially for small-ticket CNC strategies | Require a broker profile, or an explicit `brokerage="none"` that is flagged in the manifest. Add a per-scrip, per-day DP charge component. | 1 d |
| A2-M8 | app / restore | **A failed restore leaves the instance without its metadata DB.** The current files are moved aside first. On a failure during the rebuild (disk full, I/O error) nothing is moved back. The next start creates an **empty** meta DB: watchlists, settings, schedules and the API key are all missing until the user moves the files back by hand. | `e_app_evidence.py::test_a2_01`: after the failure the instance holds `bars.duckdb` (half-built) and **no** `historify_meta.db` | Apparent total data loss (recoverable by hand from `pre-restore-*`) | On any exception, delete the partial files and move the set-aside files back. Tell the user what was rolled back. | 0.5 d |
| A2-M9 | app / feeds | **A failed feed job is not retried within the bar.** `_last[key]` is set when the job is *enqueued*, so after a transient failure the bar is fetched only by the next bar's incremental job, five minutes later. | by inspection (`feeds.py::tick`) | Paper phase (G1): a strategy misses a decision. Bar-completeness and latency figures (D-19) are degraded. | Record the bar as done only when the job *succeeds*. Retry with backoff until the next bar becomes possible. | 0.5 d |

## Low

| ID | Area | Finding | Fix |
|---|---|---|---|
| A2-L1 | core | `exec_spec(product="CNC")` keeps `squareoff_at 15:10` (`setdefault` on an existing key is a no-op), which adds noise to the spec hash (X-12) | assign, don't `setdefault` |
| A2-L2 | calendar | An empty first OpenAlgo holiday answer is recorded as a successful sync (`calendar_source = openalgo`, 0 rows). The auto-sync (M-8) is only considered at process start, so a user who configures the key after starting never gets it until the weekly step. (`e_app_evidence.py::test_a2_02`) | treat 0 rows for a year as "no data" → issue; re-check on the first connection |
| A2-L3 | notifications | Every failed feed job raises its own notification (fingerprint = job id). A broker outage means one alert per bar per feed, i.e. an alert storm on Telegram. | fingerprint per feed + status; summarise |
| A2-L4 | snapshots | The weekly Lab snapshot rotates old snapshots out **before** the new one is built and verified (same class as M-11 for backups) | rotate after the build job succeeds |
| A2-L5 | snapshots | The manifest does not record the finality buffers used for `available_at` (a process-global setting since M-6) | record `intraday_final_buffer_s` / daily buffer |
| A2-L6 | snapshots / lab | `file` from a snapshot manifest is joined to the folder unchecked (path traversal with an imported snapshot; read-only Parquet) | confine to the folder (`commonpath`) |
| A2-L7 | core | With 1m segments (magnifier/latency), `bracket_live_on_entry_bar = false` means "from the next **1m** segment", not from the next bar | track the entry bar, not the segment |
| A2-L8 | core / metrics | `profit_factor = +inf` with no losing trades: "Infinity" is invalid strict JSON once metrics are served (1.4) | `null` + `no_losses` flag |
| A2-L9 | core / metrics | `periods_per_year` uses a fixed 248 sessions and 375 min; special and partial sessions are ignored | from the calendar in range |
| A2-L10 | docs | "Zero-copy" wording: `bars_from_arrow` converts and copies (float → int) and `run()` copies the numpy arrays. No measured cost, but the claim is inaccurate. | fix wording, or borrow slices in `run()` |
| A2-L11 | CI | The `lab-core` job has never run, so the Windows wheel is unverified | first push; keep the job required |
| A2-L12 | process | No Rust-side property tests or coverage figure. `Engine::run` is ~250 lines in one function. | proptest + cargo-llvm-cov; split phases P0–P4 into functions |
| A2-L13 | feeds | `feed_max_instruments` is checked only at creation; a watchlist can grow past the cap later | check per tick; warn |
| A2-L14 | lab | `bars_from_store` uses private BarStore internals (`_reading`, `_src`) | public `arrow_range()` on the store |
| A2-L15 | feeds | Feed latency stats live in memory (lost on restart); D-19 needs them as paper-phase evidence | persist per bar (1.5) |
| A2-L16 | notifications | The watcher looks back only 1 s before the last scan: a job whose finish commit lags is missed, and failures during downtime are never reported | scan by "not yet notified" flag instead of time |

## Tracked performance gaps (not defects)

| Target | Measured | Plan |
|---|---|---|
| Sweeps ≥ 3,000 backtests/s (design §21) | `run_batch` 725/s (256 sets, 4 cores) | Shared indicator nodes (1.3: ×4.4 measured on signals) + fused metrics accumulators (1.6) |
| 10 M bars → core < 0.5 s | 9.79 M bars in 0.73 s warm, incl. DuckDB scan + sort | Symbol-major Universe loader (1.6) |

## Re-verification of audit-1 fixes

All 18 hardening tests pass. The cross-site and DNS-rebinding scripts (`audit/a1_cross_site.py`) are still blocked
(403/421, no key exfiltration). The calendar shrink guard, restore round trip (101/101 series identical), purge and
feeds tests are green.
